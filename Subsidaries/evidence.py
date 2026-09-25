"""
CMPDI/CIL — Evidence Engine
────────────────────────────
Traces every factual claim in an AI answer back to a specific
source document, page, section, and excerpt.

This is a core USP: every number or assertion in a CMPDI query
response is backed by a verifiable source citation.

Usage:
    engine = EvidenceEngine(knowledge_base)
    evidence_list = engine.find_evidence_for_answer(
        answer_text="CIL produced 773 MT of coal in 2023-24",
        query="What was CIL production in 2023-24?",
    )
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from ontology import UNIT_CANONICAL, ACTIVITY_TYPES, SUBSIDIARIES


# ─── Data Structures ──────────────────────────────────────────────

@dataclass
class Evidence:
    """One piece of traceable evidence linking a claim to its source."""
    claim:       str        # The claim or sentence being supported
    source_doc:  str        # Document filename
    doc_id:      str
    page_num:    int
    section:     str        # Section heading or table name
    excerpt:     str        # Source sentence/table row
    value:       Optional[float] = None
    unit:        str = ""
    confidence:  float = 0.8

    def citation_text(self) -> str:
        """Format as a footnote-style citation."""
        return (
            f"Source: {self.source_doc}"
            + (f", Page {self.page_num}" if self.page_num > 0 else "")
            + (f", {self.section}" if self.section else "")
        )

    def to_dict(self) -> dict:
        return {
            "claim":       self.claim[:200],
            "source_doc":  self.source_doc,
            "doc_id":      self.doc_id,
            "page_num":    self.page_num,
            "section":     self.section,
            "excerpt":     self.excerpt[:300],
            "value":       self.value,
            "unit":        self.unit,
            "confidence":  round(self.confidence, 3),
            "citation":    self.citation_text(),
        }


@dataclass
class EvidenceBundle:
    """Evidence for a complete answer — may cover multiple claims."""
    answer_text:   str
    evidence_list: List[Evidence] = field(default_factory=list)
    has_gaps:      bool = False          # True if some claims have no evidence
    gap_claims:    List[str] = field(default_factory=list)

    def primary_sources(self) -> List[str]:
        """Unique list of source documents used."""
        return list(dict.fromkeys(e.source_doc for e in self.evidence_list))

    def to_dict(self) -> dict:
        return {
            "evidence": [e.to_dict() for e in self.evidence_list],
            "primary_sources": self.primary_sources(),
            "has_gaps": self.has_gaps,
            "gap_claims": self.gap_claims,
            "citation_block": self._build_citation_block(),
        }

    def _build_citation_block(self) -> str:
        lines = ["**Sources:**"]
        for i, e in enumerate(self.evidence_list, 1):
            lines.append(f"[{i}] {e.citation_text()}")
        return "\n".join(lines)


# ─── Number Extraction Helpers ────────────────────────────────────

_NUM_RE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)")
_UNIT_PATTERNS_STR = "|".join([
    r"million\s+tonnes?", r"lakh\s+tonnes?", r"line\s+km",
    r"sq\.?\s*km", r"crores?", r"\bmt\b", r"\bbt\b", r"\bkt\b",
    r"\bkm\b", r"\bmw\b",
])
_UNIT_RE = re.compile(r"(" + _UNIT_PATTERNS_STR + r")", re.IGNORECASE)


def _extract_numbers_from_text(text: str) -> List[tuple]:
    """Return list of (value, unit) pairs found in text."""
    pairs = []
    for m in _NUM_RE.finditer(text):
        val = float(m.group().replace(",", ""))
        # Look for unit immediately after the number
        after = text[m.end():m.end() + 30]
        unit_m = _UNIT_RE.match(after.strip())
        unit = unit_m.group(0) if unit_m else ""
        unit = UNIT_CANONICAL.get(unit.lower().strip(), unit)
        pairs.append((val, unit, m.start()))
    return pairs


# ─── Evidence Engine ──────────────────────────────────────────────

class EvidenceEngine:
    """
    Links AI-generated answers to specific source documents and pages
    by matching numerical values and key phrases in the knowledge base.
    """

    def __init__(self, knowledge_base):
        self.kb = knowledge_base

    def find_evidence_for_answer(
        self,
        answer_text: str,
        query: str = "",
        top_k: int = 5,
    ) -> EvidenceBundle:
        """
        Main entry: find evidence for all numerical claims in the answer.

        Strategy:
          1. Extract numbers from the answer text
          2. Match each number against the facts table in SQLite
          3. For non-numerical claims, use hybrid search
        """
        bundle = EvidenceBundle(answer_text=answer_text)
        sentences = self._split_sentences(answer_text)

        for sentence in sentences:
            nums = _extract_numbers_from_text(sentence)
            found = False

            for (val, unit, _) in nums:
                ev = self._match_fact(val, unit, sentence)
                if ev:
                    bundle.evidence_list.append(ev)
                    found = True
                    break

            if not found:
                # Try text-based chunk match
                ev = self._match_chunk(sentence)
                if ev:
                    bundle.evidence_list.append(ev)
                    found = True

            if not found and len(sentence.split()) > 4:
                bundle.has_gaps = True
                bundle.gap_claims.append(sentence[:150])

        return bundle

    def find_evidence_for_facts(
        self,
        metric: str,
        period: str,
        organization: str = None,
    ) -> List[Evidence]:
        """
        Look up evidence for specific (metric, period, org) — used by Report Agent.
        """
        facts = self.kb.query_facts(
            metric=metric,
            period=period,
            organization=organization,
            min_confidence=0.5,
        )
        evidence = []
        for f in facts:
            doc = self.kb.get_document(f["doc_id"]) or {}
            ev = Evidence(
                claim      = f.get("excerpt", ""),
                source_doc = doc.get("filename", f["doc_id"]),
                doc_id     = f["doc_id"],
                page_num   = f.get("page_num", 1),
                section    = f.get("activity", ""),
                excerpt    = f.get("excerpt", "")[:300],
                value      = f.get("value"),
                unit       = f.get("unit", ""),
                confidence = f.get("confidence", 0.8),
            )
            evidence.append(ev)
        return evidence

    def _match_fact(self, value: float, unit: str, sentence: str) -> Optional[Evidence]:
        """Match a numerical value against the facts table.

        Requires metric AND unit to agree with what is found in the claim
        sentence to prevent cross-metric false provenance (e.g. employee count
        being cited as coal production evidence just because the number matches).
        """
        # Allow ±0.5% tolerance for rounding
        tol = max(abs(value) * 0.005, 0.01)

        # ── Determine expected metric from sentence context ──────────────
        sentence_lower = sentence.lower()
        expected_metric: Optional[str] = None
        for keyword, canonical in ACTIVITY_TYPES.items():
            if keyword in sentence_lower:
                expected_metric = canonical
                break

        # ── Determine expected organisation from sentence context ────────
        from ontology import SUBSIDIARY_ALIASES
        expected_org: Optional[str] = None
        for alias, code in SUBSIDIARY_ALIASES.items():
            if re.search(r'\b' + re.escape(alias) + r'\b', sentence_lower):
                expected_org = code
                break

        # ── Build SQL with metric + unit filters ──────────────────────────
        params: list = [value, tol]
        extra_conditions = ""

        if unit:
            # Normalise unit for comparison
            unit_canon = UNIT_CANONICAL.get(unit.lower().strip(), unit)
            # Allow the fact to have the same canonical unit OR no unit stored
            extra_conditions += " AND (f.unit = ? OR f.unit = '' OR f.unit IS NULL)"
            params.append(unit_canon)

        if expected_metric:
            extra_conditions += " AND f.metric = ?"
            params.append(expected_metric)

        if expected_org:
            extra_conditions += " AND (f.organization = ? OR f.organization = '' OR f.organization IS NULL)"
            params.append(expected_org)

        rows = self.kb._conn.execute(
            "SELECT f.*, d.filename FROM facts f "
            "JOIN documents d ON f.doc_id=d.id "
            f"WHERE ABS(f.value - ?) <= ?{extra_conditions} "
            "ORDER BY f.confidence DESC LIMIT 1",
            params,
        ).fetchone()

        # Fallback: if strict match fails, relax org constraint only
        if not rows and expected_org and expected_metric:
            params_relaxed = [value, tol, unit_canon if unit else None, expected_metric]
            params_relaxed = [p for p in params_relaxed if p is not None]
            fallback_sql = (
                "SELECT f.*, d.filename FROM facts f "
                "JOIN documents d ON f.doc_id=d.id "
                "WHERE ABS(f.value - ?) <= ?"
            )
            fallback_params: list = [value, tol]
            if unit:
                fallback_sql += " AND (f.unit = ? OR f.unit = '' OR f.unit IS NULL)"
                fallback_params.append(unit_canon if unit else "")
            if expected_metric:
                fallback_sql += " AND f.metric = ?"
                fallback_params.append(expected_metric)
            fallback_sql += " ORDER BY f.confidence DESC LIMIT 1"
            rows = self.kb._conn.execute(fallback_sql, fallback_params).fetchone()

        if not rows:
            return None

        r = dict(rows)
        return Evidence(
            claim      = sentence,
            source_doc = r.get("filename", r["doc_id"]),
            doc_id     = r["doc_id"],
            page_num   = r.get("page_num", 1),
            section    = r.get("activity", ""),
            excerpt    = r.get("excerpt", "")[:300],
            value      = r.get("value"),
            unit       = r.get("unit", unit),
            confidence = r.get("confidence", 0.8),
        )

    def _match_chunk(self, sentence: str) -> Optional[Evidence]:
        """Match a sentence against text chunks via BM25."""
        results = self.kb.bm25_search(sentence, top_k=1)
        if not results:
            return None
        r = results[0]
        doc = self.kb.get_document(r["doc_id"]) or {}
        return Evidence(
            claim      = sentence,
            source_doc = doc.get("filename", r["doc_id"]),
            doc_id     = r["doc_id"],
            page_num   = r.get("page_num", 1),
            section    = "",
            excerpt    = r.get("text", "")[:300],
            confidence = 0.65,
        )

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Split text into sentences for per-claim evidence finding."""
        # Split on ". " or ".\n" or newlines
        raw = re.split(r"(?<=[.!?])\s+|\n+", text)
        return [s.strip() for s in raw if s.strip() and len(s.split()) > 3]
