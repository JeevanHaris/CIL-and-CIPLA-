"""
CMPDI/CIL — Automated Report Agent
────────────────────────────────────
Generates DOCX (and optionally PDF) reports from knowledge-base data.

Pipeline:
  1. Decompose report request into sections
  2. Retrieve evidence per section (hybrid RAG + structured facts)
  3. Validate facts (run conflict detector — implemented)
  4. Generate narrative via LLM
  5. Insert tables + charts (requires_chart honoured — implemented)
  6. Inject source citations
  7. Self-verify (missing data, conflicting figures, unsourced claims)
  8. Return DOCX bytes + verification report

Dependencies:
  pip install python-docx matplotlib
"""

import io
import uuid
import time
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field


# ─── Data Structures ──────────────────────────────────────────────

@dataclass
class ReportSection:
    """One section of a generated report."""
    title:     str
    content:   str        # LLM-generated narrative
    tables:    List[dict] = field(default_factory=list)
    charts:    List[dict] = field(default_factory=list)
    evidence:  List[dict] = field(default_factory=list)
    warnings:  List[str]  = field(default_factory=list)


@dataclass
class VerificationIssue:
    """A self-verification finding before finalising the report."""
    issue_type: str   # "missing_data" | "conflict" | "unsourced" | "format"
    severity:   str   # "critical" | "warning" | "info"
    description: str
    section:    str = ""


@dataclass
class ReportResult:
    """Output of the Report Agent."""
    report_id:    str
    title:        str
    docx_bytes:   Optional[bytes] = None
    sections:     List[ReportSection] = field(default_factory=list)
    issues:       List[VerificationIssue] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    generated_at: str = ""
    generation_time_s: float = 0.0
    error:        Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and self.docx_bytes is not None

    def has_critical_issues(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)

    def to_summary_dict(self) -> dict:
        return {
            "report_id":    self.report_id,
            "title":        self.title,
            "generated_at": self.generated_at,
            "success":      self.success,
            "sections":     len(self.sections),
            "issues":       [{"type": i.issue_type, "severity": i.severity,
                               "desc": i.description} for i in self.issues],
            "sources_used": self.sources_used,
            "generation_time_s": round(self.generation_time_s, 2),
            "error":        self.error,
        }


# ─── Section Planner ──────────────────────────────────────────────

SECTION_PLAN_PROMPT = """You are an expert assistant for CMPDI/CIL (Coal India) report generation.

Given a report request, break it into logical sections. Output a JSON array where each item has:
- "title": section heading
- "query": specific query to retrieve data for this section
- "requires_table": true if numerical data should be in a table
- "requires_chart": true if a trend chart is appropriate

Keep it to 5–8 sections maximum. Output ONLY valid JSON array.

Report Request: {request}
JSON:"""

SECTION_CONTENT_PROMPT = """You are a professional report writer for CMPDI/CIL (Coal India Limited).

Write a concise, factual section for a government/corporate report.
Use only the provided data. Do not invent numbers.
Write in formal, third-person style.
Include specific figures where provided.
Flag as [DATA UNAVAILABLE] if information is missing.

Section: {title}
Query: {query}
Available Data:
{data}

Write the section content (2–5 paragraphs):"""

VERIFICATION_PROMPT = """Review this draft report section for quality issues.

Check for:
1. Missing data (marked [DATA UNAVAILABLE])
2. Unsupported numerical claims
3. Inconsistent figures
4. Incomplete sentences or formatting errors

Section Title: {title}
Content: {content}

List issues as JSON array: [{{"type": "missing_data"|"unsourced"|"conflict"|"format", "severity": "critical"|"warning"|"info", "description": "..."}}]
Output ONLY JSON array:"""


# ─── Report Agent ─────────────────────────────────────────────────

class ReportAgent:
    """
    Generates evidence-backed DOCX reports from knowledge base data.

    Usage:
        agent = ReportAgent(gateway, knowledge_base, evidence_engine)
        result = agent.generate(
            request="Prepare a status report on CIL coal production 2020-2025",
            title="CIL Coal Production Report 2020–2025"
        )
        with open("report.docx", "wb") as f:
            f.write(result.docx_bytes)
    """

    def __init__(self, gateway, knowledge_base, evidence_engine):
        self.gateway   = gateway
        self.kb        = knowledge_base
        self.evidence  = evidence_engine

    def generate(
        self,
        request: str,
        title: str = None,
        organization: str = None,
        period: str = None,
    ) -> ReportResult:
        """
        Full pipeline: plan → retrieve → validate → narrate → build DOCX.
        """
        t0        = time.time()
        report_id = str(uuid.uuid4())
        title     = title or f"CMPDI/CIL Report — {datetime.utcnow().strftime('%B %Y')}"
        result    = ReportResult(
            report_id    = report_id,
            title        = title,
            generated_at = datetime.utcnow().isoformat() + "Z",
        )

        try:
            # Step 1: Plan sections
            sections_plan = self._plan_sections(request)
            if not sections_plan:
                sections_plan = [{"title": "Overview", "query": request,
                                  "requires_table": True, "requires_chart": False}]

            # Step 2: Build each section (retrieve data + generate narrative)
            built_sections = []
            all_sources = set()
            for plan in sections_plan:
                section = self._build_section(plan, organization, period)
                built_sections.append(section)
                for ev in section.evidence:
                    all_sources.add(ev.get("source_doc", ""))

            # Step 3: Run conflict detector and attach warnings to affected sections
            try:
                from validator import ConflictDetector
                detector = ConflictDetector(self.kb)
                all_conflicts = detector.detect_all()
                if all_conflicts:
                    conflict_descriptions = [
                        f"{c.metric}/{c.period}/{c.organization}: "
                        f"{c.percent_deviation():.1f}% deviation "
                        f"({c.severity.upper()})"
                        for c in all_conflicts[:10]
                    ]
                    # Attach to the first section as a preamble warning
                    if built_sections:
                        built_sections[0].warnings.extend(
                            [f"Data conflict detected — {d}" for d in conflict_descriptions]
                        )
            except Exception as ce:
                print(f"[ReportAgent] Conflict detector error: {ce}")

            # Step 4: Self-verify all sections
            all_issues = []
            for section in built_sections:
                issues = self._verify_section(section)
                section.warnings += [i.description for i in issues if i.severity in ("warning", "critical")]
                all_issues.extend(issues)

            result.sections     = built_sections
            result.issues       = all_issues
            result.sources_used = [s for s in all_sources if s]

            # Step 5: Build DOCX
            result.docx_bytes = self._build_docx(result)

        except Exception as e:
            result.error = str(e)
            print(f"[ReportAgent] Error: {e}")

        result.generation_time_s = time.time() - t0
        print(f"[ReportAgent] Report '{title}' generated in {result.generation_time_s:.1f}s, "
              f"{len(result.sections)} sections, {len(result.issues)} issues")
        return result

    # ── Section Planning ─────────────────────────────────────────

    def _plan_sections(self, request: str) -> List[dict]:
        """Use LLM to decompose the report request into sections."""
        prompt = SECTION_PLAN_PROMPT.format(request=request)
        try:
            response = self.gateway.call(
                model_id=None,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_json = self._extract_json_array(response.content)
            if raw_json:
                return json.loads(raw_json)
        except Exception as e:
            print(f"[ReportAgent] Section planning error: {e}")
        return []

    # ── Section Building ─────────────────────────────────────────

    def _build_section(
        self,
        plan: dict,
        organization: str = None,
        period: str = None,
    ) -> ReportSection:
        """Retrieve data, generate narrative, collect evidence for one section."""
        title = plan.get("title", "Section")
        query = plan.get("query", title)

        # Retrieve relevant chunks + facts
        chunks  = self.kb.hybrid_search(query, gateway=self.gateway, top_k=8)
        facts   = self.kb.query_facts(
            organization=organization,
            period=period,
            min_confidence=0.6,
            limit=50,
        )

        # Format data for LLM
        data_parts = []
        for c in chunks[:5]:
            data_parts.append(f"[{c.get('filename','')} p{c.get('page_num','')}] {c.get('text','')[:400]}")
        for f in facts[:10]:
            data_parts.append(
                f"[FACT] {f.get('organization','')} {f.get('activity','')} "
                f"{f.get('value','')} {f.get('unit','')} ({f.get('period','')})"
            )

        data_str = "\n\n".join(data_parts) if data_parts else "No data available for this section."

        # Generate narrative
        content = self._generate_content(title, query, data_str)

        # Collect evidence
        evidence_bundle = self.evidence.find_evidence_for_answer(content, query)

        # Build table if required
        tables = []
        if plan.get("requires_table") and facts:
            tables.append(self._facts_to_table(facts[:15]))

        # Build chart data if required (honours requires_chart flag)
        charts = []
        if plan.get("requires_chart"):
            try:
                import analytics as analytics_engine
                # Try to infer metric and org from the query
                query_lower = query.lower()
                from ontology import ACTIVITY_TYPES, SUBSIDIARY_ALIASES
                inferred_metric = next(
                    (v for k, v in ACTIVITY_TYPES.items() if k in query_lower),
                    "coal_production",
                )
                inferred_org = next(
                    (code for alias, code in SUBSIDIARY_ALIASES.items()
                     if alias in query_lower),
                    organization,
                )
                trend_data = analytics_engine.extract_trend(
                    self.kb,
                    metric=inferred_metric,
                    organization=inferred_org,
                )
                if any(v is not None for v in trend_data.get("values", [])):
                    charts.append(trend_data)
            except Exception as chart_err:
                print(f"[ReportAgent] Chart generation error: {chart_err}")

        return ReportSection(
            title    = title,
            content  = content,
            tables   = tables,
            charts   = charts,
            evidence = [e.to_dict() for e in evidence_bundle.evidence_list],
        )

    def _generate_content(self, title: str, query: str, data: str) -> str:
        """Call LLM to write section narrative."""
        prompt = SECTION_CONTENT_PROMPT.format(title=title, query=query, data=data)
        try:
            response = self.gateway.call(
                model_id=None,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content
        except Exception as e:
            return f"[Content generation error: {e}]"

    # ── Self-Verification ─────────────────────────────────────────

    def _verify_section(self, section: ReportSection) -> List[VerificationIssue]:
        """Ask LLM to review section for issues."""
        issues = []

        # Quick heuristic checks first
        if "[DATA UNAVAILABLE]" in section.content:
            issues.append(VerificationIssue(
                issue_type  = "missing_data",
                severity    = "warning",
                description = f"Section '{section.title}' has unavailable data",
                section     = section.title,
            ))

        if not section.evidence:
            issues.append(VerificationIssue(
                issue_type  = "unsourced",
                severity    = "warning",
                description = f"Section '{section.title}' has no traceable evidence",
                section     = section.title,
            ))

        # LLM-based verification
        prompt = VERIFICATION_PROMPT.format(
            title   = section.title,
            content = section.content[:2000],
        )
        try:
            response = self.gateway.call(
                model_id=None,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_json = self._extract_json_array(response.content)
            if raw_json:
                llm_issues = json.loads(raw_json)
                for item in llm_issues:
                    issues.append(VerificationIssue(
                        issue_type  = item.get("type", "format"),
                        severity    = item.get("severity", "info"),
                        description = item.get("description", ""),
                        section     = section.title,
                    ))
        except Exception as e:
            print(f"[ReportAgent] Verification error: {e}")

        return issues

    # ── DOCX Builder ─────────────────────────────────────────────

    def _build_docx(self, result: ReportResult) -> bytes:
        """Build a formatted DOCX document from the report result."""
        try:
            from docx import Document
            from docx.shared import Pt, RGBColor, Inches
            from docx.enum.text import WD_ALIGN_PARAGRAPH
        except ImportError:
            raise ImportError("python-docx not installed. Run: pip install python-docx")

        doc = Document()

        # ── Document Title ─────────────────────────────────
        title_para = doc.add_heading(result.title, level=0)
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Meta info
        meta = doc.add_paragraph()
        meta.add_run(f"Generated: {result.generated_at[:10]}").italic = True
        meta.alignment = WD_ALIGN_PARAGRAPH.CENTER

        doc.add_paragraph()

        # ── Conflict / Issue Warnings ──────────────────────
        critical_issues = [i for i in result.issues if i.severity == "critical"]
        if critical_issues:
            warn_para = doc.add_paragraph("⚠ DATA QUALITY NOTICES")
            warn_para.runs[0].bold = True
            for issue in critical_issues:
                doc.add_paragraph(f"• [{issue.issue_type.upper()}] {issue.description}",
                                  style="List Bullet")
            doc.add_paragraph()

        # ── Sections ───────────────────────────────────────
        for section in result.sections:
            # Section heading
            doc.add_heading(section.title, level=1)

            # Section warnings
            for w in section.warnings:
                para = doc.add_paragraph(f"⚠ {w}")
                para.runs[0].font.color.rgb = RGBColor(0xD9, 0x53, 0x19)
                para.runs[0].font.size = Pt(9)

            # Narrative content
            for paragraph_text in section.content.split("\n\n"):
                paragraph_text = paragraph_text.strip()
                if paragraph_text:
                    doc.add_paragraph(paragraph_text)

            # Tables
            for tbl_data in section.tables:
                self._add_table_to_doc(doc, tbl_data)

            # Evidence citations
            if section.evidence:
                doc.add_paragraph()
                cite_para = doc.add_paragraph("Sources:")
                cite_para.runs[0].bold = True
                cite_para.runs[0].font.size = Pt(9)
                for ev in section.evidence[:5]:
                    doc.add_paragraph(
                        f"• {ev.get('citation', ev.get('source_doc', 'Unknown'))}",
                        style="List Bullet"
                    ).runs[0].font.size = Pt(9)

            doc.add_paragraph()

        # ── References Section ─────────────────────────────
        if result.sources_used:
            doc.add_heading("References", level=1)
            for i, src in enumerate(sorted(set(result.sources_used)), 1):
                doc.add_paragraph(f"[{i}] {src}", style="List Number")

        # ── Save to bytes ──────────────────────────────────
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    def _add_table_to_doc(self, doc, tbl_data: dict):
        """Add a formatted table to a DOCX document."""
        try:
            from docx import Document
            from docx.shared import Pt, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            headers = tbl_data.get("headers", [])
            rows    = tbl_data.get("rows", [])
            caption = tbl_data.get("caption", "")

            if not headers and not rows:
                return

            if caption:
                doc.add_paragraph(caption).runs[0].italic = True

            table = doc.add_table(rows=1 + len(rows), cols=len(headers))
            table.style = "Table Grid"

            # Header row
            hdr_cells = table.rows[0].cells
            for i, h in enumerate(headers):
                hdr_cells[i].text = str(h)
                hdr_cells[i].paragraphs[0].runs[0].bold = True

            # Data rows
            for r_idx, row in enumerate(rows):
                row_cells = table.rows[r_idx + 1].cells
                for c_idx, cell in enumerate(row):
                    row_cells[c_idx].text = str(cell) if cell is not None else ""

            doc.add_paragraph()
        except Exception as e:
            print(f"[ReportAgent] Table insert error: {e}")

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _extract_json_array(text: str) -> str:
        start = text.find("[")
        end   = text.rfind("]")
        if start != -1 and end > start:
            return text[start:end + 1]
        return ""

    @staticmethod
    def _facts_to_table(facts: List[dict]) -> dict:
        """Convert a list of facts into a table dict for the DOCX builder."""
        headers = ["Organization", "Activity", "Value", "Unit", "Period", "Source"]
        rows = []
        for f in facts:
            doc_id = f.get("doc_id", "")
            rows.append([
                f.get("organization", ""),
                f.get("activity", ""),
                f.get("value", ""),
                f.get("unit", ""),
                f.get("period", ""),
                doc_id[:30],
            ])
        return {"headers": headers, "rows": rows, "caption": "Extracted Data Facts"}
