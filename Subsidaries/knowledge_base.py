"""
CMPDI/CIL — Knowledge Base
────────────────────────────
Dual-layer storage:
  1. SQLite   — structured facts (queryable by org/metric/period)
  2. FAISS    — vector embeddings for semantic search
  3. BM25     — keyword search over text chunks

On first run:
  - Creates `data/knowledge/facts.db` (SQLite)
  - Creates `data/vector_index/` (FAISS)

Dependencies:
  pip install faiss-cpu  (or faiss-gpu for GPU systems)
  Ollama with nomic-embed-text: ollama pull nomic-embed-text
"""

import os
import json
import uuid
import time
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any

# ─── Paths ────────────────────────────────────────────────────────
_BASE_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH      = os.path.join(_BASE_DIR, "knowledge", "facts.db")
VECTOR_DIR   = os.path.join(_BASE_DIR, "vector_index")
DOCS_DIR     = os.path.join(_BASE_DIR, "documents")

EMBED_MODEL  = "nomic-embed-text"   # Ollama embedding model
EMBED_DIM    = 768                  # nomic-embed-text output dimension
CHUNK_SIZE   = 400                  # characters per text chunk


# ─── SQLite Schema ────────────────────────────────────────────────

_CREATE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    doc_type    TEXT,
    title       TEXT,
    organization TEXT,
    year        TEXT,
    doc_type_hint TEXT,
    upload_time TEXT,
    page_count  INTEGER DEFAULT 0,
    char_count  INTEGER DEFAULT 0,
    table_count INTEGER DEFAULT 0,
    ocr_applied INTEGER DEFAULT 0,
    indexed     INTEGER DEFAULT 0,
    raw_text_path TEXT,
    metadata    TEXT DEFAULT '{}'
);
"""

_CREATE_FACTS = """
CREATE TABLE IF NOT EXISTS facts (
    id           TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    page_num     INTEGER DEFAULT 1,
    organization TEXT,
    activity     TEXT,
    metric       TEXT,
    value        REAL,
    unit         TEXT,
    period       TEXT,
    excerpt      TEXT,
    confidence   REAL DEFAULT 0.8,
    extraction_method TEXT DEFAULT 'regex',
    created_at   TEXT,
    FOREIGN KEY (doc_id) REFERENCES documents(id)
);
"""

_CREATE_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    page_num    INTEGER DEFAULT 1,
    chunk_index INTEGER DEFAULT 0,
    text        TEXT,
    vector_id   INTEGER DEFAULT -1,
    FOREIGN KEY (doc_id) REFERENCES documents(id)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_facts_org       ON facts(organization);",
    "CREATE INDEX IF NOT EXISTS idx_facts_metric    ON facts(metric);",
    "CREATE INDEX IF NOT EXISTS idx_facts_period    ON facts(period);",
    "CREATE INDEX IF NOT EXISTS idx_facts_doc       ON facts(doc_id);",
    "CREATE INDEX IF NOT EXISTS idx_chunks_doc      ON chunks(doc_id);",
]


# ─── Knowledge Base ───────────────────────────────────────────────

class KnowledgeBase:
    """
    Manages structured fact storage (SQLite) and semantic search (FAISS).

    Usage:
        kb = KnowledgeBase()
        kb.add_document(doc_summary)
        kb.add_facts(facts_list)
        kb.add_chunks(doc_id, chunks)
        results = kb.query_facts(period="2023-24", organization="CCL")
        hits    = kb.vector_search("seismic survey line km", top_k=5)
    """

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        os.makedirs(VECTOR_DIR, exist_ok=True)
        os.makedirs(DOCS_DIR, exist_ok=True)

        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

        # FAISS index (lazy-loaded)
        self._faiss_index  = None
        self._chunk_ids: List[str] = []   # maps FAISS vector_id → chunk.id
        self._load_faiss()

    # ── Schema Init ──────────────────────────────────────────────

    def _init_schema(self):
        cur = self._conn.cursor()
        cur.execute(_CREATE_DOCUMENTS)
        cur.execute(_CREATE_FACTS)
        cur.execute(_CREATE_CHUNKS)
        for idx_sql in _CREATE_INDEXES:
            cur.execute(idx_sql)
        self._conn.commit()

    # ── Document CRUD ────────────────────────────────────────────

    def add_document(self, doc_summary: dict) -> bool:
        """
        Insert document metadata into the knowledge base.

        Args:
            doc_summary: Result of NormalizedDocument.to_summary_dict()
        """
        sql = """
        INSERT OR REPLACE INTO documents
            (id, filename, doc_type, title, organization, year, doc_type_hint,
             upload_time, page_count, char_count, table_count, ocr_applied, metadata)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        meta = doc_summary.get("metadata", {})
        try:
            self._conn.execute(sql, (
                doc_summary["doc_id"],
                doc_summary["filename"],
                doc_summary["doc_type"],
                meta.get("title", doc_summary["filename"]),
                meta.get("organization", ""),
                meta.get("year", ""),
                meta.get("doc_type_hint", "other"),
                doc_summary.get("upload_time", datetime.utcnow().isoformat()),
                doc_summary.get("page_count", 0),
                doc_summary.get("char_count", 0),
                doc_summary.get("table_count", 0),
                1 if doc_summary.get("ocr_applied") else 0,
                json.dumps(meta),
            ))
            self._conn.commit()
            return True
        except Exception as e:
            print(f"[KB] add_document error: {e}")
            return False

    def list_documents(self, limit: int = 100) -> List[dict]:
        rows = self._conn.execute(
            """
            SELECT d.*,
                   COUNT(DISTINCT f.id) AS facts_count,
                   COUNT(DISTINCT c.id) AS chunk_count
            FROM documents d
            LEFT JOIN facts  f ON f.doc_id = d.id
            LEFT JOIN chunks c ON c.doc_id = d.id
            GROUP BY d.id
            ORDER BY d.upload_time DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, doc_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_document(self, doc_id: str) -> bool:
        try:
            # Check if doc exists by id or filename
            doc = self.get_document(doc_id)
            if not doc:
                row = self._conn.execute("SELECT * FROM documents WHERE filename=?", (doc_id,)).fetchone()
                if row:
                    doc = dict(row)
                    doc_id = doc["id"]
                else:
                    return False

            self._conn.execute("DELETE FROM facts  WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self._conn.commit()
            # Rebuild FAISS after deletion while preserving remaining vectors
            self._rebuild_faiss()
            return True
        except Exception as e:
            print(f"[KB] delete_document error: {e}")
            return False

    def mark_indexed(self, doc_id: str):
        self._conn.execute(
            "UPDATE documents SET indexed=1 WHERE id=?", (doc_id,)
        )
        self._conn.commit()

    # ── Fact CRUD ────────────────────────────────────────────────

    def add_facts(self, facts: list) -> int:
        """Insert a list of ExtractedFact objects. Returns count inserted."""
        sql = """
        INSERT OR IGNORE INTO facts
            (id, doc_id, page_num, organization, activity, metric,
             value, unit, period, excerpt, confidence, extraction_method, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        now = datetime.utcnow().isoformat()
        inserted = 0
        for f in facts:
            try:
                d = f.to_dict() if hasattr(f, "to_dict") else f
                self._conn.execute(sql, (
                    d.get("fact_id", str(uuid.uuid4())),
                    d["doc_id"],
                    d.get("page_num", 1),
                    d.get("organization", ""),
                    d.get("activity", ""),
                    d.get("metric", ""),
                    float(d.get("value", 0)),
                    d.get("unit", ""),
                    d.get("period", ""),
                    d.get("excerpt", "")[:300],
                    float(d.get("confidence", 0.8)),
                    d.get("extraction_method", "regex"),
                    now,
                ))
                inserted += 1
            except Exception as e:
                print(f"[KB] add_fact error: {e}")
        self._conn.commit()
        return inserted

    def query_facts(
        self,
        organization: str = None,
        metric: str = None,
        period: str = None,
        doc_id: str = None,
        min_confidence: float = 0.5,
        limit: int = 200,
    ) -> List[dict]:
        """Query structured facts with filters."""
        conditions = ["confidence >= ?"]
        params: list = [min_confidence]

        if organization:
            conditions.append("organization = ?")
            params.append(organization)
        if metric:
            conditions.append("metric = ?")
            params.append(metric)
        if period:
            conditions.append("period = ?")
            params.append(period)
        if doc_id:
            conditions.append("doc_id = ?")
            params.append(doc_id)

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM facts WHERE {where} ORDER BY confidence DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_facts_for_conflict_check(
        self,
        metric: str,
        period: str,
        organization: str = None,
    ) -> List[dict]:
        """Fetch all facts with same metric + period (for contradiction detection)."""
        if organization:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE metric=? AND period=? AND organization=?",
                (metric, period, organization)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM facts WHERE metric=? AND period=?",
                (metric, period)
            ).fetchall()
        return [dict(r) for r in rows]

    def facts_summary(self) -> dict:
        """Return count statistics for the facts table."""
        total  = self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        orgs   = self._conn.execute("SELECT DISTINCT organization FROM facts").fetchall()
        periods= self._conn.execute("SELECT DISTINCT period FROM facts ORDER BY period DESC").fetchall()
        return {
            "total_facts": total,
            "organizations": [r[0] for r in orgs if r[0]],
            "periods": [r[0] for r in periods if r[0]],
        }

    # ── Text Chunk CRUD ──────────────────────────────────────────

    def add_chunks(self, doc_id: str, chunks: List[dict],
                   gateway=None) -> int:
        """
        Store text chunks and (optionally) embed them into FAISS.

        Args:
            doc_id:  Document ID
            chunks:  List of {"text": str, "page_num": int, "chunk_index": int}
            gateway: ModelGateway for embedding (None = skip vector indexing)

        Returns:
            Number of chunks stored.
        """
        sql = """
        INSERT OR IGNORE INTO chunks (id, doc_id, page_num, chunk_index, text, vector_id)
        VALUES (?,?,?,?,?,?)
        """
        stored = 0
        for chunk in chunks:
            chunk_id   = str(uuid.uuid4())
            vector_id  = -1

            if gateway:
                vec = self._embed_text(chunk["text"], gateway)
                if vec is not None:
                    vector_id = self._add_to_faiss(vec, chunk_id)

            try:
                self._conn.execute(sql, (
                    chunk_id,
                    doc_id,
                    chunk.get("page_num", 1),
                    chunk.get("chunk_index", 0),
                    chunk["text"][:2000],
                    vector_id,
                ))
                stored += 1
            except Exception as e:
                print(f"[KB] add_chunk error: {e}")

        self._conn.commit()
        if gateway:
            self._save_faiss()
        return stored

    def get_chunks_by_doc(self, doc_id: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM chunks WHERE doc_id=? ORDER BY page_num, chunk_index",
            (doc_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def bm25_search(self, query: str, top_k: int = 10) -> List[dict]:
        """
        BM25-style keyword search over stored chunks.
        Filters common stopwords so high-frequency words like "the" don't
        pollute results. Ranks by term-hit count (approximates IDF weighting
        without requiring FTS5).
        """
        _STOPWORDS = {
            "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
            "but", "not", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "this", "that", "these",
            "those", "it", "its", "with", "as", "by", "from", "into",
        }
        tokens = [
            t for t in query.lower().split()
            if len(t) > 1 and t not in _STOPWORDS
        ]
        if not tokens:
            return []

        # Build a CASE-expression that counts how many tokens appear so we can
        # rank results instead of returning an arbitrary unordered slice.
        score_expr = " + ".join(
            [f"(CASE WHEN LOWER(c.text) LIKE ? THEN 1 ELSE 0 END)" for _ in tokens]
        )
        where_clause = " OR ".join(["LOWER(c.text) LIKE ?" for _ in tokens])

        like_params = [f"%{t}%" for t in tokens]

        sql = (
            f"SELECT c.*, d.filename, ({score_expr}) AS bm25_score "
            f"FROM chunks c "
            f"JOIN documents d ON c.doc_id = d.id "
            f"WHERE {where_clause} "
            f"ORDER BY bm25_score DESC "
            f"LIMIT ?"
        )
        # Parameters: score_expr needs one set of LIKE params, WHERE clause needs another
        params = like_params + like_params + [top_k * 3]  # fetch more then re-rank
        rows = self._conn.execute(sql, params).fetchall()
        results = [dict(r) for r in rows]
        # Sort by score descending and return top_k
        results.sort(key=lambda r: r.get("bm25_score", 0), reverse=True)
        return results[:top_k]

    # ── Vector (FAISS) Operations ────────────────────────────────

    def _load_faiss(self):
        """Load FAISS index from disk if it exists."""
        try:
            import faiss
            import numpy as np
            idx_path = os.path.join(VECTOR_DIR, "index.faiss")
            ids_path = os.path.join(VECTOR_DIR, "chunk_ids.json")
            if os.path.exists(idx_path) and os.path.exists(ids_path):
                self._faiss_index = faiss.read_index(idx_path)
                with open(ids_path) as f:
                    self._chunk_ids = json.load(f)
                print(f"[KB] FAISS index loaded: {self._faiss_index.ntotal} vectors")
            else:
                self._faiss_index = faiss.IndexFlatIP(EMBED_DIM)  # inner product = cosine with normalized vecs
                self._chunk_ids = []
        except ImportError:
            print("[KB] faiss not installed — vector search disabled. Run: pip install faiss-cpu")
            self._faiss_index = None
        except Exception as e:
            print(f"[KB] FAISS load error: {e}")
            self._faiss_index = None

    def _save_faiss(self):
        """Persist FAISS index to disk."""
        if self._faiss_index is None:
            return
        try:
            import faiss
            idx_path = os.path.join(VECTOR_DIR, "index.faiss")
            ids_path = os.path.join(VECTOR_DIR, "chunk_ids.json")
            faiss.write_index(self._faiss_index, idx_path)
            with open(ids_path, "w") as f:
                json.dump(self._chunk_ids, f)
        except Exception as e:
            print(f"[KB] FAISS save error: {e}")

    def _rebuild_faiss(self):
        """Reconstruct FAISS index retaining only live chunks, preserving vector embeddings."""
        if self._faiss_index is None:
            return
        try:
            import faiss
            import numpy as np

            # Fetch chunk IDs still present in the DB
            rows = self._conn.execute("SELECT id FROM chunks").fetchall()
            live_ids = {r[0] for r in rows}

            if not self._chunk_ids:
                return

            keep_indices = [i for i, cid in enumerate(self._chunk_ids) if cid in live_ids]

            if len(keep_indices) == len(self._chunk_ids):
                return  # No vectors removed

            new_index = faiss.IndexFlatIP(EMBED_DIM)
            if keep_indices and self._faiss_index.ntotal > 0:
                all_vectors = self._faiss_index.reconstruct_n(0, self._faiss_index.ntotal)
                kept_vectors = all_vectors[keep_indices]
                new_index.add(kept_vectors)
                new_chunk_ids = [self._chunk_ids[i] for i in keep_indices]
                self._faiss_index = new_index
                self._chunk_ids = new_chunk_ids

                # Update vector_id in SQLite chunks to reflect new positions
                for new_vid, cid in enumerate(new_chunk_ids):
                    self._conn.execute("UPDATE chunks SET vector_id = ? WHERE id = ?", (new_vid, cid))
                self._conn.commit()
            else:
                self._faiss_index = new_index
                self._chunk_ids = []

            self._save_faiss()
            print(f"[KB] FAISS index rebuilt: {self._faiss_index.ntotal} vectors retained after document deletion.")
        except Exception as e:
            print(f"[KB] _rebuild_faiss error: {e}")

    def _embed_text(self, text: str, gateway) -> Optional[list]:
        """Get embedding vector from Ollama."""
        try:
            import ollama
            import numpy as np
            resp = ollama.embeddings(model=EMBED_MODEL, prompt=text[:1000])
            vec = resp.get("embedding") or resp.get("embeddings")
            if not vec:
                return None
            arr = np.array(vec, dtype="float32")
            # Normalize for cosine similarity
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr /= norm
            return arr.tolist()
        except Exception as e:
            print(f"[KB] Embedding error: {e}")
            return None

    def _add_to_faiss(self, vector: list, chunk_id: str) -> int:
        """Add a vector to FAISS, return its integer ID."""
        if self._faiss_index is None:
            return -1
        try:
            import numpy as np
            arr = np.array([vector], dtype="float32")
            self._faiss_index.add(arr)
            vid = len(self._chunk_ids)
            self._chunk_ids.append(chunk_id)
            return vid
        except Exception as e:
            print(f"[KB] FAISS add error: {e}")
            return -1

    def vector_search(self, query: str, gateway, top_k: int = 10) -> List[dict]:
        """
        Semantic search using FAISS.

        Returns list of chunk dicts with added "score" field.
        Falls back to BM25 if FAISS unavailable.
        """
        if self._faiss_index is None or self._faiss_index.ntotal == 0:
            return self.bm25_search(query, top_k)

        vec = self._embed_text(query, gateway)
        if vec is None:
            return self.bm25_search(query, top_k)

        try:
            import numpy as np
            arr = np.array([vec], dtype="float32")
            distances, indices = self._faiss_index.search(arr, top_k)

            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0 or idx >= len(self._chunk_ids):
                    continue
                chunk_id = self._chunk_ids[idx]
                row = self._conn.execute(
                    "SELECT c.*, d.filename FROM chunks c "
                    "JOIN documents d ON c.doc_id=d.id WHERE c.id=?",
                    (chunk_id,)
                ).fetchone()
                if row:
                    r = dict(row)
                    r["score"] = float(dist)
                    results.append(r)
            return results
        except Exception as e:
            print(f"[KB] vector_search error: {e}")
            return self.bm25_search(query, top_k)

    def hybrid_search(self, query: str, gateway=None, top_k: int = 10) -> List[dict]:
        """
        Combine BM25 + vector search results, deduplicated and ranked.
        """
        bm25_results   = self.bm25_search(query, top_k)
        vector_results = self.vector_search(query, gateway, top_k) if gateway else []

        # Merge by chunk id, giving higher weight to vector results
        seen = {}
        for r in vector_results:
            seen[r["id"]] = dict(r, hybrid_score=r.get("score", 0) * 2.0)
        for r in bm25_results:
            if r["id"] not in seen:
                seen[r["id"]] = dict(r, hybrid_score=1.0)
            else:
                seen[r["id"]]["hybrid_score"] += 1.0

        ranked = sorted(seen.values(), key=lambda x: x["hybrid_score"], reverse=True)
        return ranked[:top_k]

    # ── Statistics ───────────────────────────────────────────────

    def stats(self) -> dict:
        doc_count   = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        fact_count  = self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        chunk_count = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vector_count= self._faiss_index.ntotal if self._faiss_index else 0
        return {
            "documents": doc_count,
            "facts":     fact_count,
            "chunks":    chunk_count,
            "vectors":   vector_count,
            "db_path":   DB_PATH,
        }

    def close(self):
        self._conn.close()


# ─── Chunking Helper ─────────────────────────────────────────────

def chunk_document(doc, chunk_size: int = CHUNK_SIZE) -> List[dict]:
    """
    Split a NormalizedDocument into text chunks for vector indexing.

    Returns list of {"text", "page_num", "chunk_index"} dicts.
    """
    chunks = []
    for page in doc.pages:
        text = page.full_text()
        if not text.strip():
            continue
        # Split by sentences / paragraphs
        parts = [s.strip() for s in text.replace("\n\n", "\n").split("\n") if s.strip()]
        current = ""
        chunk_idx = 0
        for part in parts:
            if len(current) + len(part) > chunk_size and current:
                chunks.append({
                    "text": current,
                    "page_num": page.page_num,
                    "chunk_index": chunk_idx,
                })
                chunk_idx += 1
                current = part
            else:
                current += (" " if current else "") + part
        if current:
            chunks.append({
                "text": current,
                "page_num": page.page_num,
                "chunk_index": chunk_idx,
            })
    return chunks
