"""
CMPDI/CIL — Cross-Document Validation & Contradiction Detection
────────────────────────────────────────────────────────────────
Identifies conflicts where the same (metric, period, organization)
tuple has different values across multiple source documents.

Also provides a source priority resolver that uses document type
hierarchy (gazette > annual report > memo, etc.) to recommend the
authoritative figure.
"""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from ontology import DOC_TYPE_PRIORITY


# ─── Conflict Data Structures ─────────────────────────────────────

@dataclass
class ConflictSource:
    """One source contributing to a conflict."""
    doc_id:    str
    filename:  str
    page_num:  int
    value:     float
    unit:      str
    excerpt:   str
    doc_type:  str       # from ontology.DOC_TYPE_PRIORITY
    confidence: float


@dataclass
class Conflict:
    """
    A detected contradiction in the knowledge base.
    Same metric + period + organization, different values.
    """
    conflict_id:  str
    metric:       str
    period:       str
    organization: str
    sources:      List[ConflictSource] = field(default_factory=list)
    severity:     str = "warning"   # "critical" | "warning" | "info"
    resolved:     bool = False
    authoritative_doc_id: str = ""
    authoritative_value:  Optional[float] = None
    resolution_reason:    str = ""

    def value_range(self) -> tuple:
        vals = [s.value for s in self.sources]
        return (min(vals), max(vals))

    def percent_deviation(self) -> float:
        lo, hi = self.value_range()
        if lo == 0:
            return 0.0
        return round(abs(hi - lo) / lo * 100, 2)

    def to_dict(self) -> dict:
        return {
            "conflict_id":   self.conflict_id,
            "metric":        self.metric,
            "period":        self.period,
            "organization":  self.organization,
            "severity":      self.severity,
            "resolved":      self.resolved,
            "percent_deviation": self.percent_deviation(),
            "authoritative_value": self.authoritative_value,
            "resolution_reason": self.resolution_reason,
            "sources": [
                {
                    "doc_id":    s.doc_id,
                    "filename":  s.filename,
                    "page_num":  s.page_num,
                    "value":     s.value,
                    "unit":      s.unit,
                    "excerpt":   s.excerpt[:200],
                    "doc_type":  s.doc_type,
                    "confidence":s.confidence,
                }
                for s in self.sources
            ],
        }


# ─── Source Priority Resolver ─────────────────────────────────────

class SourcePriorityResolver:
    """
    Resolves conflicts using document type hierarchy.
    Higher position in DOC_TYPE_PRIORITY = higher authority.
    """

    def priority(self, doc_type: str) -> int:
        try:
            return DOC_TYPE_PRIORITY.index(doc_type)
        except ValueError:
            return 0

    def resolve(self, conflict: Conflict) -> Conflict:
        """
        Pick the authoritative source by document type priority.
        If tie, prefer higher confidence.
        """
        if not conflict.sources:
            return conflict

        best = max(
            conflict.sources,
            key=lambda s: (self.priority(s.doc_type), s.confidence),
        )

        conflict.resolved             = True
        conflict.authoritative_doc_id = best.doc_id
        conflict.authoritative_value  = best.value
        conflict.resolution_reason    = (
            f"Selected '{best.filename}' (type={best.doc_type}, "
            f"priority={self.priority(best.doc_type)}, "
            f"confidence={best.confidence:.2f}) as authoritative source."
        )
        return conflict


# ─── Conflict Detector ────────────────────────────────────────────

# Tolerance thresholds for declaring a conflict
CRITICAL_DEVIATION_PCT = 5.0   # > 5% → critical
WARNING_DEVIATION_PCT  = 1.0   # 1–5% → warning
# 0.0 would flag identical values from two imports of the same file.
# Require at least 0.01% difference to suppress phantom self-conflicts.
INFO_DEVIATION_PCT     = 0.01  # < 0.01% difference → not a conflict


class ConflictDetector:
    """
    Scans the knowledge base for contradictory facts.

    Usage:
        detector = ConflictDetector(knowledge_base)
        conflicts = detector.detect_all()
        conflicts = detector.detect_for_query("coal_production", "2023-24", "CCL")
    """

    def __init__(self, knowledge_base):
        self.kb       = knowledge_base
        self.resolver = SourcePriorityResolver()

    def detect_for_query(
        self,
        metric: str,
        period: str,
        organization: str = None,
    ) -> List[Conflict]:
        """
        Check for conflicts for a specific (metric, period, org) combination.
        Called at query time to flag potential data issues.
        """
        facts = self.kb.get_facts_for_conflict_check(metric, period, organization)
        if len(facts) < 2:
            return []
        return self._analyse_fact_group(facts, metric, period, organization or "")

    def detect_all(self, min_facts: int = 2) -> List[Conflict]:
        """
        Full scan: find all (metric, period, organization) groups with
        conflicting values. Returns list of Conflict objects.
        """
        # Group all facts by (metric, period, organization)
        rows = self.kb._conn.execute(
            "SELECT metric, period, organization, COUNT(*) as cnt "
            "FROM facts GROUP BY metric, period, organization HAVING cnt >= ?",
            (min_facts,)
        ).fetchall()

        conflicts = []
        for row in rows:
            metric, period, org, _ = row["metric"], row["period"], row["organization"], row["cnt"]
            group_conflicts = self.detect_for_query(metric, period, org)
            conflicts.extend(group_conflicts)

        # Deduplicate
        seen_ids = set()
        unique = []
        for c in conflicts:
            if c.conflict_id not in seen_ids:
                seen_ids.add(c.conflict_id)
                unique.append(c)

        return unique

    def _analyse_fact_group(
        self,
        facts: List[dict],
        metric: str,
        period: str,
        organization: str,
    ) -> List[Conflict]:
        """
        Given a group of facts with same (metric, period, org),
        check if values differ significantly. Return Conflict if so.
        """
        import uuid
        if not facts:
            return []

        # Group by document, preferring the highest-confidence fact per doc.
        doc_best: Dict[str, dict] = {}
        for f in facts:
            doc_id = f["doc_id"]
            if doc_id not in doc_best or f["confidence"] > doc_best[doc_id]["confidence"]:
                doc_best[doc_id] = f

        # Further deduplicate by value: if two different doc_ids carry the
        # exact same value (e.g. the same file was re-ingested with a new ID),
        # keep only the one with the highest confidence so we don't report a
        # phantom conflict between a document and itself.
        value_best: Dict[float, dict] = {}
        for f in doc_best.values():
            v = round(f["value"], 6)
            if v not in value_best or f["confidence"] > value_best[v]["confidence"]:
                value_best[v] = f

        unique_facts = list(value_best.values())
        if len(unique_facts) < 2:
            return []

        values = [f["value"] for f in unique_facts]
        lo, hi = min(values), max(values)
        if lo == 0 and hi == 0:
            return []

        # Percent deviation
        if lo != 0:
            dev = abs(hi - lo) / lo * 100
        else:
            dev = 100.0

        if dev < INFO_DEVIATION_PCT:
            return []

        # Determine severity
        if dev >= CRITICAL_DEVIATION_PCT:
            severity = "critical"
        elif dev >= WARNING_DEVIATION_PCT:
            severity = "warning"
        else:
            severity = "info"

        # Build ConflictSource list
        sources = []
        for f in unique_facts:
            doc = self.kb.get_document(f["doc_id"]) or {}
            sources.append(ConflictSource(
                doc_id    = f["doc_id"],
                filename  = doc.get("filename", f["doc_id"]),
                page_num  = f.get("page_num", 1),
                value     = f["value"],
                unit      = f.get("unit", ""),
                excerpt   = f.get("excerpt", ""),
                doc_type  = doc.get("doc_type_hint", "other"),
                confidence= f.get("confidence", 0.8),
            ))

        conflict = Conflict(
            conflict_id  = str(uuid.uuid4()),
            metric       = metric,
            period       = period,
            organization = organization,
            sources      = sources,
            severity     = severity,
        )

        # Auto-resolve using source priority
        conflict = self.resolver.resolve(conflict)
        return [conflict]


# ─── Validation Report ────────────────────────────────────────────

class ValidationReport:
    """
    Summary of validation results for a query or document batch.
    Presented to the user when data conflicts exist.
    """

    def __init__(self, conflicts: List[Conflict]):
        self.conflicts = conflicts

    @property
    def has_critical(self) -> bool:
        return any(c.severity == "critical" for c in self.conflicts)

    @property
    def has_warnings(self) -> bool:
        return any(c.severity == "warning" for c in self.conflicts)

    def summary_text(self) -> str:
        if not self.conflicts:
            return "✅ No data conflicts detected."
        lines = [f"⚠ {len(self.conflicts)} data conflict(s) detected:"]
        for c in self.conflicts:
            lines.append(
                f"  [{c.severity.upper()}] {c.organization} / {c.metric} / {c.period}"
                f"  — {len(c.sources)} sources, {c.percent_deviation():.1f}% deviation"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "total_conflicts": len(self.conflicts),
            "has_critical":    self.has_critical,
            "has_warnings":    self.has_warnings,
            "conflicts":       [c.to_dict() for c in self.conflicts],
            "summary":         self.summary_text(),
        }
