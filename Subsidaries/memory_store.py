"""
ARIA v3.0 — Memory Store
─────────────────────────
Persistent storage and RAG context retrieval for memory documents.
Stores documents in `data/memory_docs/` with persistent JSON metadata so
they survive server restarts and reloads.
Provides BM25-based relevant excerpt retrieval to augment LLM chat contexts.
"""

import os
import io
import json
import uuid
import time
from datetime import datetime
from search_engine import search_documents, _tokenise, _bm25_score, _best_excerpt, SearchResult

class MemoryStore:
    """Manages persistent document memories and query-based context retrieval."""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "memory_docs")
        self.base_dir = base_dir
        self.meta_file = os.path.join(self.base_dir, "metadata.json")
        self._docs: dict[str, dict] = {} # doc_id -> {filename, text, ...}
        self._meta: dict[str, dict] = {} # doc_id -> metadata
        self._ensure_dir()
        self._load_from_disk()

    def _ensure_dir(self):
        os.makedirs(self.base_dir, exist_ok=True)

    def _load_from_disk(self):
        """Load stored metadata and document texts from disk."""
        if not os.path.exists(self.meta_file):
            self._meta = {}
            self._docs = {}
            return

        try:
            with open(self.meta_file, "r", encoding="utf-8") as f:
                self._meta = json.load(f)
        except Exception as e:
            print(f"[MemoryStore] Error reading metadata: {e}")
            self._meta = {}

        self._docs = {}
        for doc_id, meta in list(self._meta.items()):
            text_file = os.path.join(self.base_dir, f"{doc_id}.txt")
            if os.path.exists(text_file):
                try:
                    with open(text_file, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                    self._docs[doc_id] = {
                        "filename": meta.get("filename", "unknown"),
                        "text": text,
                        "uploaded_at": meta.get("uploaded_at"),
                        "char_count": len(text)
                    }
                except Exception as e:
                    print(f"[MemoryStore] Error loading text for {doc_id}: {e}")
            else:
                # Text file missing, remove stale entry
                self._meta.pop(doc_id, None)

        self._save_meta()
        print(f"[MemoryStore] Initialized. Loaded {len(self._docs)} memory documents from disk.")

    def _save_meta(self):
        """Persist metadata dictionary to disk."""
        try:
            with open(self.meta_file, "w", encoding="utf-8") as f:
                json.dump(self._meta, f, indent=2)
        except Exception as e:
            print(f"[MemoryStore] Error writing metadata: {e}")

    def save_document(self, filename: str, file_bytes: bytes, text: str) -> dict:
        """
        Store a document in memory with its raw file and extracted text.
        Returns metadata dict of the saved document.
        """
        doc_id = str(uuid.uuid4())
        clean_filename = os.path.basename(filename)
        safe_ext = os.path.splitext(clean_filename)[1].lower()

        # Save raw binary file if bytes provided
        if file_bytes:
            raw_path = os.path.join(self.base_dir, f"{doc_id}{safe_ext}")
            try:
                with open(raw_path, "wb") as f:
                    f.write(file_bytes)
            except Exception as e:
                print(f"[MemoryStore] Warning: Could not save binary file: {e}")

        # Save text file for fast reading
        text_path = os.path.join(self.base_dir, f"{doc_id}.txt")
        with open(text_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)

        # Create brief summary/snippet
        clean_preview = " ".join(text[:250].split())
        summary = clean_preview + ("..." if len(text) > 250 else "")

        meta_entry = {
            "doc_id": doc_id,
            "filename": clean_filename,
            "char_count": len(text),
            "size_bytes": len(file_bytes) if file_bytes else len(text.encode("utf-8")),
            "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary,
        }

        self._meta[doc_id] = meta_entry
        self._docs[doc_id] = {
            "filename": clean_filename,
            "text": text,
            "uploaded_at": meta_entry["uploaded_at"],
            "char_count": len(text)
        }
        self._save_meta()

        print(f"[MemoryStore] Saved memory document: {clean_filename} ({len(text):,} chars) id={doc_id}")
        return meta_entry

    def list_documents(self) -> list[dict]:
        """Return list of all memory documents sorted newest first."""
        docs = list(self._meta.values())
        docs.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
        return docs

    def get_document(self, doc_id: str) -> dict | None:
        """Get document details and full text."""
        if doc_id in self._docs and doc_id in self._meta:
            result = dict(self._meta[doc_id])
            result["text"] = self._docs[doc_id]["text"]
            return result
        return None

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document from memory store and disk."""
        if doc_id not in self._meta:
            return False

        meta = self._meta.pop(doc_id, None)
        self._docs.pop(doc_id, None)
        self._save_meta()

        # Remove files from disk
        ext = os.path.splitext(meta.get("filename", ""))[1].lower()
        paths_to_delete = [
            os.path.join(self.base_dir, f"{doc_id}.txt"),
            os.path.join(self.base_dir, f"{doc_id}{ext}"),
        ]
        for p in paths_to_delete:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    print(f"[MemoryStore] Warning: Could not remove {p}: {e}")

        print(f"[MemoryStore] Deleted document id={doc_id}")
        return True

    def clear_documents(self) -> int:
        """Delete all documents from memory store."""
        count = len(self._meta)
        for doc_id in list(self._meta.keys()):
            self.delete_document(doc_id)
        return count

    def get_relevant_context(self, query: str, top_k: int = 3, min_score: float = 0.05) -> list[dict]:
        """
        Search memory documents for chunks/excerpts relevant to the query.
        Splits documents into overlapping chunks for granular paragraph retrieval.
        Returns list of {doc_id, filename, excerpt, score}.
        """
        if not query or not self._docs:
            return []

        query_tokens = _tokenise(query)
        if not query_tokens:
            return []

        # Build chunked corpus across all memory docs
        chunk_size = 600
        overlap = 150
        chunks_corpus: list[tuple[str, str, list[str], str]] = [] # (doc_id, filename, tokens, chunk_text)

        for doc_id, doc in self._docs.items():
            text = doc.get("text", "")
            filename = doc.get("filename", "unknown")
            if not text:
                continue

            # If text is small, treat as single chunk
            if len(text) <= chunk_size:
                tokens = _tokenise(text)
                chunks_corpus.append((doc_id, filename, tokens, text))
            else:
                step = chunk_size - overlap
                for i in range(0, len(text), step):
                    chunk = text[i:i + chunk_size].strip()
                    if chunk:
                        tokens = _tokenise(chunk)
                        chunks_corpus.append((doc_id, filename, tokens, chunk))

        if not chunks_corpus:
            return []

        N = len(chunks_corpus)
        avg_dl = sum(len(c[2]) for c in chunks_corpus) / N

        from collections import Counter
        df: dict[str, int] = Counter()
        for _, _, tokens, _ in chunks_corpus:
            for term in set(tokens):
                df[term] += 1

        scored = []
        for doc_id, filename, tokens, chunk_text in chunks_corpus:
            score = _bm25_score(query_tokens, tokens, df, N, avg_dl)
            if score >= min_score:
                if len(chunk_text) <= 800:
                    excerpt = chunk_text.strip()
                else:
                    excerpt = _best_excerpt(chunk_text, query_tokens, window=500)
                scored.append({
                    "doc_id": doc_id,
                    "filename": filename,
                    "excerpt": excerpt,
                    "score": round(score, 3),
                })

        # Sort descending by score
        scored.sort(key=lambda r: r["score"], reverse=True)

        # De-duplicate: avoid duplicate identical excerpts from overlapping chunks
        unique_results = []
        seen_snippets = set()
        for item in scored:
            lead = item["excerpt"][:80].strip()
            if lead not in seen_snippets:
                seen_snippets.add(lead)
                unique_results.append(item)
            if len(unique_results) >= top_k:
                break

        return unique_results

    def format_prompt_context(self, query: str) -> tuple[str, list[dict]]:
        """
        Generate contextual prompt text to inject into the LLM system prompt.
        Returns (context_string, list_of_matched_sources).
        """
        if not self._docs:
            return "", []

        # 1. Check for relevant chunks
        matches = self.get_relevant_context(query, top_k=3, min_score=0.08)

        # 2. Check if user is asking about their uploaded files or memory in general
        query_lower = query.lower()
        is_asking_about_docs = any(phrase in query_lower for phrase in [
            "what document", "what file", "which document", "which file", "my document",
            "uploaded", "my file", "in memory", "what is in", "what do you know",
            "summarize the file", "summarize my", "read the file", "check the document"
        ])

        parts = []

        # If relevant excerpts found:
        if matches:
            parts.append("\n## Context From Memory Documents:")
            parts.append("The user has uploaded knowledge documents into your memory. Relevant excerpts for this query:")
            for m in matches:
                parts.append(f"\n[Source: {m['filename']}]\n\"\"\"\n{m['excerpt']}\n\"\"\"")
            parts.append("\nInstruction: Ground your response in the above document excerpts when answering. You can cite the document name.")

        # Always include a brief index of memory files if user is inquiring about docs or files exist
        if is_asking_about_docs or (not matches and len(self._docs) <= 5):
            parts.append("\n## Uploaded Knowledge Documents in Memory:")
            for meta in self.list_documents()[:5]:
                parts.append(f"- **{meta['filename']}** ({meta['char_count']:,} characters, uploaded {meta['uploaded_at']}): {meta['summary']}")

        return "\n".join(parts), matches
