"""
CMPDI/CIL — Structured Fact Extractor
────────────────────────────────────────
Extracts mining-domain numerical facts and named entities from
document text, producing structured records suitable for the
knowledge base (SQLite) and validation engine.

Extraction strategy:
  1. Regex patterns for common numerical fact formats
  2. LLM-assisted extraction for complex / ambiguous sentences
  3. Ontology-based entity tagging (subsidiary, activity, unit)

Output example:
  {
    "organization": "CMPDI",
    "activity": "2D/3D seismic survey",
    "metric": "seismic_2d3d",
    "value": 234.57,
    "unit": "line_km",
    "period": "2023-24",
    "source_doc": "annual_report_2024.pdf",
    "page": 167,
    "excerpt": "CMPDI achieved 234.57 line km of 2D/3D seismic survey",
    "confidence": 0.91
  }
"""

import re
import uuid
import time
from dataclasses import dataclass, field
from typing import List, Optional

from ontology import (
    SUBSIDIARIES, SUBSIDIARY_ALIASES,
    ACTIVITY_TYPES, UNIT_CANONICAL,
    MINING_QUERY_SIGNALS, PERIOD_PATTERNS,
)


# ─── Data Structures ──────────────────────────────────────────────

@dataclass
class ExtractedFact:
    """One structured numerical fact extracted from a document."""
    fact_id:      str
    doc_id:       str
    page_num:     int
    organization: str           # e.g. "CCL", "CIL", "CMPDI"
    activity:     str           # raw activity string
    metric:       str           # canonical metric key from ACTIVITY_TYPES
    value:        float
    unit:         str           # canonical unit from UNIT_CANONICAL
    period:       str           # e.g. "2023-24"
    excerpt:      str           # source sentence/snippet
    confidence:   float = 0.8
    extraction_method: str = "regex"  # "regex" | "llm"

    def to_dict(self) -> dict:
        return {
            "fact_id":      self.fact_id,
            "doc_id":       self.doc_id,
            "page_num":     self.page_num,
            "organization": self.organization,
            "activity":     self.activity,
            "metric":       self.metric,
            "value":        self.value,
            "unit":         self.unit,
            "period":       self.period,
            "excerpt":      self.excerpt[:300],
            "confidence":   round(self.confidence, 3),
            "extraction_method": self.extraction_method,
        }


@dataclass
class ExtractedEntity:
    """Named entity found in text."""
    entity_type:  str   # "subsidiary", "activity", "period", "location"
    value:        str
    canonical:    str
    start:        int   # char offset in text
    end:          int


# ─── Regex Patterns ───────────────────────────────────────────────

# Number with optional decimal and thousands separator
_NUM_PAT = r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)"

# Unit patterns (order matters — longer first)
_UNIT_PATTERNS = [
    r"million\s+tonne[s]?",
    r"lakh\s+tonne[s]?",
    r"thousand\s+tonne[s]?",
    r"line\s+km",
    r"sq\.?\s*km",
    r"crore[s]?",
    r"\bmt\b",
    r"\bbt\b",
    r"\bkt\b",
    r"\bkm\b",
    r"\bm\b",
    r"\bha\b",
    r"\bmw\b",
    r"\bnos\.?\b",
]
_UNIT_RE = re.compile(
    r"(" + "|".join(_UNIT_PATTERNS) + r")",
    re.IGNORECASE
)

# Period patterns
_PERIOD_RE = re.compile(
    r"(?:FY\s?)?(\d{4})[-–—](\d{2,4})"
    r"|(?:FY\s?)?(20\d{2})"
    r"|(?:Q[1-4]\s+(?:FY\s?)?(20\d{2}))",
    re.IGNORECASE
)

# Combination: number + unit (the core extraction pattern)
_FACT_RE = re.compile(
    _NUM_PAT + r"\s*" + r"(" + "|".join(_UNIT_PATTERNS) + r")",
    re.IGNORECASE
)


# ─── Entity Extraction ────────────────────────────────────────────

def extract_entities(text: str) -> List[ExtractedEntity]:
    """
    Find mining-domain named entities in text.
    Returns list of ExtractedEntity for subsidiaries, activities, periods.
    """
    entities = []
    text_lower = text.lower()

    # Find subsidiaries
    for alias, code in SUBSIDIARY_ALIASES.items():
        pattern = r"\b" + re.escape(alias) + r"\b"
        for m in re.finditer(pattern, text_lower):
            entities.append(ExtractedEntity(
                entity_type="subsidiary",
                value=m.group(),
                canonical=code,
                start=m.start(),
                end=m.end(),
            ))

    # Find activities
    for keyword, canonical in ACTIVITY_TYPES.items():
        if keyword in text_lower:
            start = text_lower.index(keyword)
            entities.append(ExtractedEntity(
                entity_type="activity",
                value=keyword,
                canonical=canonical,
                start=start,
                end=start + len(keyword),
            ))

    # Find periods
    for m in _PERIOD_RE.finditer(text):
        period_str = _normalise_period(m.group())
        entities.append(ExtractedEntity(
            entity_type="period",
            value=m.group(),
            canonical=period_str,
            start=m.start(),
            end=m.end(),
        ))

    return entities


def _normalise_period(raw: str) -> str:
    """Normalise period string to FY format: '2023-24'."""
    raw = raw.strip()
    m = re.match(r"(?:FY\s?)?(\d{4})[-–—](\d{2,4})", raw, re.IGNORECASE)
    if m:
        year_start = m.group(1)
        year_end = m.group(2)
        if len(year_end) == 2:
            return f"{year_start}-{year_end}"
        return f"{year_start}-{year_end[-2:]}"
    m = re.match(r"(?:FY\s?)?(20\d{2})", raw, re.IGNORECASE)
    if m:
        y = int(m.group(1))
        return f"{y}-{str(y+1)[-2:]}"
    return raw


def _normalise_unit(raw: str) -> str:
    """Return canonical unit string."""
    key = raw.strip().lower().replace("  ", " ")
    return UNIT_CANONICAL.get(key, key)


def _clean_number(raw: str) -> float:
    """Parse a number string (may contain commas) to float."""
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return 0.0


# ─── Context Window Extractor ─────────────────────────────────────

def _get_sentence_context(text: str, match_start: int, match_end: int,
                          window: int = 200) -> str:
    """Extract a sentence-level context window around a match."""
    start = max(0, match_start - window)
    end   = min(len(text), match_end + window)
    snippet = text[start:end].strip()
    # Trim to sentence boundaries if possible
    first_dot = snippet.find(". ")
    last_dot  = snippet.rfind(". ")
    if first_dot > 0 and last_dot > first_dot:
        snippet = snippet[first_dot + 2:last_dot + 1]
    return snippet


# ─── Regex-Based Fact Extraction ─────────────────────────────────

def extract_numerical_facts_regex(
    text: str,
    doc_id: str,
    page_num: int = 1,
    period_hint: str = "",
    org_hint: str = "CIL",
) -> List[ExtractedFact]:
    """
    Extract numerical facts from text using regex.
    Fast, deterministic — works without LLM.

    Args:
        text:        Page or section text.
        doc_id:      Source document ID.
        page_num:    Page number (for evidence tracing).
        period_hint: FY period from document metadata.
        org_hint:    Default organization if not found in context.

    Returns:
        List of ExtractedFact objects.
    """
    facts = []
    entities = extract_entities(text)

    # Build entity index by position
    subsidiary_entities = [e for e in entities if e.entity_type == "subsidiary"]
    period_entities     = [e for e in entities if e.entity_type == "period"]
    activity_entities   = [e for e in entities if e.entity_type == "activity"]

    for m in _FACT_RE.finditer(text):
        num_str  = m.group(1)
        unit_str = m.group(2)
        value    = _clean_number(num_str)
        unit     = _normalise_unit(unit_str)

        if value == 0.0:
            continue

        match_pos = m.start()

        # Find nearest subsidiary (within 300 chars)
        org = org_hint
        best_dist = 999
        for ent in subsidiary_entities:
            dist = abs(ent.start - match_pos)
            if dist < best_dist and dist < 300:
                best_dist = dist
                org = ent.canonical

        # Find nearest period (within 500 chars)
        period = period_hint
        best_dist = 999
        for ent in period_entities:
            dist = abs(ent.start - match_pos)
            if dist < best_dist and dist < 500:
                best_dist = dist
                period = ent.canonical

        # Find nearest activity (within 150 chars)
        activity = ""
        metric   = "unknown"
        best_dist = 999
        for ent in activity_entities:
            dist = abs(ent.start - match_pos)
            if dist < best_dist and dist < 150:
                best_dist = dist
                activity = ent.value
                metric   = ent.canonical

        excerpt = _get_sentence_context(text, m.start(), m.end())
        fact = ExtractedFact(
            fact_id=str(uuid.uuid4()),
            doc_id=doc_id,
            page_num=page_num,
            organization=org,
            activity=activity,
            metric=metric,
            value=value,
            unit=unit,
            period=period,
            excerpt=excerpt,
            confidence=0.75,
            extraction_method="regex",
        )
        facts.append(fact)

    return facts


# ─── LLM-Assisted Extraction ──────────────────────────────────────

LLM_EXTRACTION_PROMPT = """You are a data extraction assistant for CMPDI/CIL coal mining documents.

Extract ALL numerical facts from the text below. For each fact return a JSON array where each item has:
- "organization": subsidiary or organization name (e.g. "CCL", "CMPDI", "CIL")
- "activity": what the number refers to (e.g. "coal production", "seismic survey", "drilling")
- "value": the numeric value (float)
- "unit": unit of measurement (e.g. "MT", "line km", "crore")
- "period": fiscal year or time period (e.g. "2023-24", "2024")
- "excerpt": the exact sentence containing the fact

If a field is unknown, use "".
Output ONLY valid JSON array, no explanation.

TEXT:
{text}

JSON:"""


def extract_facts_with_llm(
    text: str,
    doc_id: str,
    page_num: int,
    gateway,
    period_hint: str = "",
    org_hint: str = "CIL",
) -> List[ExtractedFact]:
    """
    Use LLM to extract structured facts. More accurate for complex sentences.
    Falls back to regex if LLM fails.

    Args:
        gateway: ModelGateway instance for LLM calls.
    """
    # Limit text size to avoid token overflow
    text_chunk = text[:3000]
    prompt = LLM_EXTRACTION_PROMPT.format(text=text_chunk)

    try:
        response = gateway.call(
            model_id=None,  # use default
            messages=[{"role": "user", "content": prompt}],
        )
        raw_json = _extract_json_array(response.content)
        if not raw_json:
            raise ValueError("No JSON array found")

        import json
        items = json.loads(raw_json)
        facts = []
        for item in items:
            try:
                value = float(str(item.get("value", 0)).replace(",", ""))
            except (ValueError, TypeError):
                continue
            if value == 0.0:
                continue

            unit   = _normalise_unit(str(item.get("unit", "")))
            period = item.get("period") or period_hint
            if period:
                period = _normalise_period(period)

            org = item.get("organization", org_hint) or org_hint
            # Canonicalize organization
            org_upper = org.upper()
            for code in SUBSIDIARIES:
                if code in org_upper:
                    org = code
                    break

            activity = item.get("activity", "")
            metric = ACTIVITY_TYPES.get(activity.lower(), "unknown")

            facts.append(ExtractedFact(
                fact_id=str(uuid.uuid4()),
                doc_id=doc_id,
                page_num=page_num,
                organization=org,
                activity=activity,
                metric=metric,
                value=value,
                unit=unit,
                period=period,
                excerpt=str(item.get("excerpt", ""))[:300],
                confidence=0.88,
                extraction_method="llm",
            ))

        return facts

    except Exception as e:
        print(f"[Extractor] LLM extraction failed: {e} — falling back to regex")
        return extract_numerical_facts_regex(
            text, doc_id, page_num, period_hint, org_hint
        )


def _extract_json_array(text: str) -> str:
    """Pull the first JSON array out of LLM output."""
    start = text.find("[")
    end   = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return ""


# ─── Document-Level Extraction ────────────────────────────────────

def extract_facts_from_document(
    doc,
    gateway=None,
    use_llm: bool = True,
) -> List[ExtractedFact]:
    """
    Run fact extraction over all pages of a NormalizedDocument.

    Args:
        doc:      NormalizedDocument from ingestion pipeline.
        gateway:  ModelGateway (required if use_llm=True).
        use_llm:  Whether to use LLM extraction (slower but more accurate).

    Returns:
        List of all ExtractedFact objects from the document.
    """
    all_facts = []
    period_hint = doc.metadata.get("year", "")
    if period_hint:
        period_hint = _normalise_period(period_hint)
    org_hint = doc.metadata.get("organization", "CIL")

    for page in doc.pages:
        text = page.full_text()
        if not text.strip():
            continue

        if use_llm and gateway:
            facts = extract_facts_with_llm(
                text, doc.doc_id, page.page_num,
                gateway, period_hint, org_hint,
            )
        else:
            facts = extract_numerical_facts_regex(
                text, doc.doc_id, page.page_num,
                period_hint, org_hint,
            )

        all_facts.extend(facts)

        # Also extract from tables
        for table in page.tables:
            table_text = table.to_text()
            table_facts = extract_numerical_facts_regex(
                table_text, doc.doc_id, page.page_num,
                period_hint, org_hint,
            )
            # Boost confidence for table facts (more structured)
            for f in table_facts:
                f.confidence = min(f.confidence + 0.10, 1.0)
                f.extraction_method = "table_regex"
            all_facts.extend(table_facts)

    # Deduplicate near-identical facts
    all_facts = _deduplicate_facts(all_facts)
    print(f"[Extractor] {doc.filename}: {len(all_facts)} facts extracted")
    return all_facts


def _deduplicate_facts(facts: List[ExtractedFact]) -> List[ExtractedFact]:
    """Remove exact duplicate (org, metric, value, unit, period, page) facts."""
    seen = set()
    unique = []
    for f in facts:
        key = (f.organization, f.metric, round(f.value, 3), f.unit, f.period, f.page_num)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
