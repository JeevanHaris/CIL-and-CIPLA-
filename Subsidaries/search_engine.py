"""
ARIA v3.0 — Document Search Engine
────────────────────────────────────
BM25-style keyword search across the in-memory document store.
Zero extra dependencies — pure Python TF-IDF/BM25 approximation.

Usage:
    from search_engine import search_documents
    results = search_documents("pump vibration maintenance", doc_store, top_k=3)
"""

import re
import math
from collections import Counter


# ─── Result ────────────────────────────────────────────────────
class SearchResult:
    def __init__(self, doc_id, filename, excerpt, score, page_hint=None):
        self.doc_id    = doc_id
        self.filename  = filename
        self.excerpt   = excerpt     # relevant snippet (~300 chars)
        self.score     = score       # BM25 relevance score
        self.page_hint = page_hint   # optional page number if known

    def to_dict(self):
        return {
            "doc_id":    self.doc_id,
            "filename":  self.filename,
            "excerpt":   self.excerpt,
            "score":     round(self.score, 4),
            "page_hint": self.page_hint,
        }

    def __repr__(self):
        return f"<SearchResult doc={self.filename!r} score={self.score:.3f}>"


# ─── Tokeniser ─────────────────────────────────────────────────
def _tokenise(text: str) -> list[str]:
    """Lowercase, remove punctuation, split on whitespace."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [t for t in text.split() if len(t) > 1]


# ─── BM25 Scorer ───────────────────────────────────────────────
# Parameters from the original BM25 paper (Okapi BM25)
_K1 = 1.5   # term saturation
_B  = 0.75  # length normalisation


def _bm25_score(query_tokens: list[str],
                doc_tokens: list[str],
                df: dict[str, int],
                N: int,
                avg_dl: float) -> float:
    """Compute the BM25 score for one document."""
    dl  = len(doc_tokens)
    tf  = Counter(doc_tokens)
    score = 0.0
    for term in query_tokens:
        if term not in tf:
            continue
        f   = tf[term]
        idf = math.log((N - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1)
        tfn = (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * (dl / max(avg_dl, 1))))
        score += idf * tfn
    return score


# ─── Excerpt Extractor ─────────────────────────────────────────
def _best_excerpt(text: str, query_tokens: list[str],
                  window: int = 300) -> str:
    """
    Find the window of `window` chars that contains the most query terms.
    Falls back to the first `window` chars if no match.
    """
    text_lower = text.lower()
    best_start = 0
    best_hits  = 0

    # Slide a window through the text
    step = max(window // 4, 50)
    for start in range(0, max(len(text) - window, 1), step):
        chunk = text_lower[start:start + window]
        hits  = sum(1 for t in query_tokens if t in chunk)
        if hits > best_hits:
            best_hits  = hits
            best_start = start

    if best_start + window >= len(text):
        excerpt = text[best_start:].strip()
    else:
        excerpt = text[best_start:best_start + window].strip()
        # Trim to sentence boundary if possible, avoiding decimals like 48.5
        sentence_ends = [m.end() for m in re.finditer(r"\.[ \n\r\t]+", excerpt) if m.end() > window // 2]
        if sentence_ends:
            excerpt = excerpt[:sentence_ends[-1]].strip()
    return excerpt or text[:window]


# ─── Main API ──────────────────────────────────────────────────
def search_documents(query: str,
                     doc_store: dict,
                     top_k: int = 3,
                     min_score: float = 0.01) -> list[SearchResult]:
    """
    Search uploaded documents for content relevant to `query`.

    Args:
        query:     Natural-language search query.
        doc_store: The server's in-memory document store
                   {doc_id: {"filename": str, "text": str, ...}}
        top_k:     Maximum number of results to return.
        min_score: Minimum BM25 score to include a result.

    Returns:
        List of SearchResult objects sorted by relevance (highest first).
    """
    if not query or not doc_store:
        return []

    query_tokens = _tokenise(query)
    if not query_tokens:
        return []

    # ── Build corpus ──────────────────────────────────────────
    # Each document gets its full text tokenised.
    # We also check for "ocr_text" field (set by multimodal.py after OCR).
    corpus: list[tuple[str, str, list[str]]] = []   # (doc_id, filename, tokens)
    for doc_id, doc in doc_store.items():
        text = doc.get("ocr_text") or doc.get("text") or ""
        if not text:
            continue
        tokens = _tokenise(text)
        corpus.append((doc_id, doc.get("filename", "unknown"), tokens, text))

    if not corpus:
        return []

    N      = len(corpus)
    avg_dl = sum(len(c[2]) for c in corpus) / N

    # Document frequency table
    df: dict[str, int] = Counter()
    for _, _, tokens, _ in corpus:
        for term in set(tokens):
            df[term] += 1

    # ── Score each document ───────────────────────────────────
    scored = []
    for doc_id, filename, tokens, raw_text in corpus:
        score = _bm25_score(query_tokens, tokens, df, N, avg_dl)
        if score >= min_score:
            excerpt = _best_excerpt(raw_text, query_tokens)
            scored.append(SearchResult(
                doc_id=doc_id,
                filename=filename,
                excerpt=excerpt,
                score=score,
            ))

    # Sort descending by score, take top_k
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:top_k]


# ─── Convenience: search a single text blob ───────────────────
def search_text(query: str, text: str, top_k: int = 3) -> list[dict]:
    """
    Search within a single text string.
    Returns a list of {excerpt, score} dicts.
    Useful for splitting large documents into sections.
    """
    # Split into ~500-char chunks and treat each as a "document"
    chunk_size = 500
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    pseudo_store = {
        str(i): {"filename": f"chunk_{i}", "text": chunk}
        for i, chunk in enumerate(chunks)
    }
    results = search_documents(query, pseudo_store, top_k=top_k)
    return [r.to_dict() for r in results]


# ─── CLI test ──────────────────────────────────────────────────
if __name__ == "__main__":
    _test_store = {
        "doc1": {
            "filename": "maintenance_sop.txt",
            "text": (
                "Section 3.2 — Pump Vibration Maintenance\n"
                "Abnormal vibration in pump P-101 should not exceed 4.5 mm/s RMS. "
                "When vibration exceeds this limit, perform bearing inspection. "
                "Replace bearing if wear exceeds 0.2 mm."
            ),
        },
        "doc2": {
            "filename": "inspection_report.txt",
            "text": (
                "Inspection findings for Pump P-101:\n"
                "- Vibration measured at 6.2 mm/s RMS (limit: 4.5 mm/s)\n"
                "- Bearing clearance: 0.28 mm (limit: 0.2 mm)\n"
                "- Recommendation: Replace bearings immediately."
            ),
        },
        "doc3": {
            "filename": "general_notes.txt",
            "text": "Meeting notes from Q2 review. Budget approved for maintenance.",
        },
    }
    results = search_documents("pump vibration bearing maintenance", _test_store, top_k=3)
    for r in results:
        print(r.to_dict())
