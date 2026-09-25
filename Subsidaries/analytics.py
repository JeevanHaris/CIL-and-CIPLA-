"""
CMPDI/CIL — Analytics Engine
──────────────────────────────
Provides three types of analytics:
  1. Word Cloud — keyword frequency from document corpus
  2. Topic Identification — cluster documents by dominant themes
  3. Historical Trends — time-series of metrics from the knowledge base

All outputs are JSON-serialisable for use with Plotly / Chart.js on the frontend.

Dependencies:
  pip install wordcloud matplotlib
  (optional) pip install scikit-learn  ← for LDA topic modeling
"""

import re
import json
import collections
from typing import List, Dict, Optional, Tuple


# ─── CMPDI Stop-words ─────────────────────────────────────────────
# Mining-domain stop-words that add noise to word clouds / topics.
STOP_WORDS = {
    # Generic English
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
    "but", "not", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "this", "that", "these",
    "those", "it", "its", "with", "as", "by", "from", "up", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "each", "no", "any", "both", "all", "more", "most",
    "other", "such", "than", "too", "very", "just", "also",
    # Document boilerplate
    "page", "section", "chapter", "table", "figure", "annexure",
    "appendix", "refer", "report", "reports", "annual",
    # Numbers (will handle separately)
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}

# Important CMPDI domain terms that SHOULD appear in word clouds
DOMAIN_BOOST_TERMS = {
    "production", "exploration", "drilling", "seismic", "geological",
    "resources", "reserves", "dispatch", "mining", "coal", "coking",
    "subsidiary", "target", "achievement", "safety", "environmental",
    "geophysical", "borehole", "washery", "colliery", "opencast",
    "underground", "mechanisation", "captive", "linkage",
}


# ─── Text Preprocessing ───────────────────────────────────────────

def _preprocess_text(text: str) -> List[str]:
    """Tokenise + clean text for frequency analysis."""
    # Lowercase and remove punctuation
    text = re.sub(r"[^a-zA-Z\s]", " ", text.lower())
    tokens = text.split()
    # Filter stop-words and short tokens
    tokens = [t for t in tokens if len(t) > 3 and t not in STOP_WORDS]
    return tokens


def _get_corpus_tokens(knowledge_base, doc_ids: List[str] = None) -> List[str]:
    """Pull chunk text from KB and tokenise the whole corpus."""
    if doc_ids:
        chunks = []
        for doc_id in doc_ids:
            chunks.extend(knowledge_base.get_chunks_by_doc(doc_id))
    else:
        chunks = knowledge_base._conn.execute(
            "SELECT text FROM chunks LIMIT 5000"
        ).fetchall()
        chunks = [{"text": r[0]} for r in chunks]

    all_tokens = []
    for chunk in chunks:
        all_tokens.extend(_preprocess_text(chunk.get("text", "")))
    return all_tokens


# ─── Word Cloud ───────────────────────────────────────────────────

def generate_word_cloud_data(
    knowledge_base,
    doc_ids: List[str] = None,
    top_n: int = 80,
) -> dict:
    """
    Compute word frequency data for the frontend word cloud.

    Returns:
        {
          "words": [{"text": "production", "value": 342}, ...],
          "total_tokens": 15000,
          "doc_count": 12
        }
    """
    tokens = _get_corpus_tokens(knowledge_base, doc_ids)
    if not tokens:
        return {"words": [], "total_tokens": 0, "doc_count": 0}

    freq = collections.Counter(tokens)

    # Do NOT artificially inflate domain terms — real frequency is the signal.
    # Boosting by 1.5x distorts the cloud and misrepresents document content.
    # DOMAIN_BOOST_TERMS are already prominent if genuinely frequent.

    top = freq.most_common(top_n)
    words = [{"text": word, "value": count} for word, count in top]

    doc_count = knowledge_base._conn.execute(
        "SELECT COUNT(*) FROM documents"
    ).fetchone()[0]

    return {
        "words":        words,
        "total_tokens": len(tokens),
        "doc_count":    doc_count,
    }


# ─── Topic Identification ─────────────────────────────────────────

TOPIC_SEEDS = {
    "Coal Production & Dispatch": [
        "production", "dispatch", "output", "offtake", "coal", "coking",
        "non-coking", "pit-head", "target", "achievement",
    ],
    "Geological Exploration": [
        "exploration", "geological", "geology", "survey", "seismic",
        "borehole", "drilling", "stratigraphy", "resources", "reserves",
        "measured", "indicated", "inferred",
    ],
    "Mining Operations": [
        "mining", "mine", "opencast", "underground", "colliery", "block",
        "mechanisation", "overburden", "oc", "ul", "washery",
    ],
    "Safety & Environment": [
        "safety", "accident", "fatality", "rescue", "environment",
        "reclamation", "afforestation", "rehabilitation", "pollution",
        "emission", "dust", "ventilation",
    ],
    "Financial Performance": [
        "profit", "turnover", "revenue", "expenditure", "capex", "crore",
        "cost", "earnings", "dividend", "budget", "realisation",
    ],
    "Infrastructure & Projects": [
        "project", "infrastructure", "railway", "siding", "road",
        "township", "hospital", "school", "power", "capacity",
    ],
    "Manpower & HR": [
        "employees", "manpower", "worker", "officer", "staff",
        "recruitment", "training", "welfare", "pension",
    ],
}


def identify_topics(
    knowledge_base,
    doc_ids: List[str] = None,
) -> List[dict]:
    """
    Keyword-based topic identification over the document corpus.

    Returns list of:
        {
          "topic": "Coal Production & Dispatch",
          "score": 1240,
          "top_keywords": ["production", "dispatch", "coal", ...],
          "doc_count": 8
        }
    """
    tokens = _get_corpus_tokens(knowledge_base, doc_ids)
    if not tokens:
        return []

    token_counter = collections.Counter(tokens)
    topics = []

    for topic_name, keywords in TOPIC_SEEDS.items():
        score = sum(token_counter.get(kw, 0) for kw in keywords)
        present_kws = [kw for kw in keywords if token_counter.get(kw, 0) > 0]
        topics.append({
            "topic":        topic_name,
            "score":        score,
            "top_keywords": present_kws[:8],
        })

    # Sort by score descending
    topics.sort(key=lambda x: x["score"], reverse=True)

    # Per-topic document count: count documents that contain at least one
    # of the topic's keywords, not the total document count.
    all_docs = knowledge_base._conn.execute(
        "SELECT DISTINCT doc_id FROM chunks"
    ).fetchall()
    doc_ids = [r[0] for r in all_docs]

    for t in topics:
        kw_set = set(TOPIC_SEEDS.get(t["topic"], []))
        if not kw_set or not doc_ids:
            t["doc_count"] = 0
            continue
        # Build OR conditions for topic keywords
        conditions = " OR ".join(["LOWER(text) LIKE ?" for _ in kw_set])
        params = [f"%{kw}%" for kw in kw_set]
        try:
            count = knowledge_base._conn.execute(
                f"SELECT COUNT(DISTINCT doc_id) FROM chunks WHERE {conditions}",
                params,
            ).fetchone()[0]
            t["doc_count"] = count
        except Exception:
            t["doc_count"] = 0

    return topics


# ─── Historical Trend Extraction ─────────────────────────────────

def extract_trend(
    knowledge_base,
    metric: str,
    organization: str = None,
    year_start: int = 2015,
    year_end: int = 2025,
) -> dict:
    """
    Build a time-series for a metric across fiscal years.

    Returns Plotly-compatible data:
        {
          "metric": "coal_production",
          "organization": "CIL",
          "years": ["2015-16", "2016-17", ..., "2024-25"],
          "values": [550.0, 554.0, ...],
          "units": "MT",
          "missing_years": ["2017-18"],
          "sources": [...]
        }
    """
    years = []
    for y in range(year_start, year_end):
        years.append(f"{y}-{str(y+1)[-2:]}")

    values = []
    units  = []
    sources= []
    missing = []

    for period in years:
        facts = knowledge_base.query_facts(
            metric=metric,
            period=period,
            organization=organization,
            min_confidence=0.5,
            limit=5,
        )
        if facts:
            # Take highest-confidence fact
            best = max(facts, key=lambda f: f.get("confidence", 0))
            values.append(best.get("value"))
            units.append(best.get("unit", ""))
            sources.append(best.get("doc_id", ""))
        else:
            values.append(None)
            units.append("")
            sources.append("")
            missing.append(period)

    # Best unit across non-null
    unit = next((u for u in units if u), "")

    return {
        "metric":       metric,
        "organization": organization or "CIL",
        "years":        years,
        "values":       values,
        "unit":         unit,
        "missing_years":missing,
        "sources":      sources,
        "chart_title":  f"{metric.replace('_',' ').title()} — {organization or 'CIL'}",
    }


def compare_organizations(
    knowledge_base,
    metric: str,
    period: str,
    organizations: List[str] = None,
) -> dict:
    """
    Compare a metric across subsidiaries for a given period.

    Returns bar-chart data:
        {
          "metric": "coal_production",
          "period": "2023-24",
          "labels": ["ECL","BCCL","CCL",...],
          "values": [38.5, 27.1, 72.3, ...],
          "unit": "MT",
          "sources": [...]
        }
    """
    from ontology import SUBSIDIARIES
    orgs = organizations or list(SUBSIDIARIES.keys())

    labels = []
    values = []
    units  = []
    sources= []

    for org in orgs:
        facts = knowledge_base.query_facts(
            metric=metric,
            period=period,
            organization=org,
            min_confidence=0.5,
            limit=3,
        )
        if facts:
            best = max(facts, key=lambda f: f.get("confidence", 0))
            labels.append(org)
            values.append(best.get("value"))
            units.append(best.get("unit", ""))
            sources.append(best.get("doc_id", ""))

    unit = next((u for u in units if u), "")

    return {
        "metric":  metric,
        "period":  period,
        "labels":  labels,
        "values":  values,
        "unit":    unit,
        "sources": sources,
        "chart_title": f"{metric.replace('_',' ').title()} by Subsidiary ({period})",
    }
