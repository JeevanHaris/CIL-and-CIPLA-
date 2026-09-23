"""
ARIA → CMPDI/CIL Sovereign Document Intelligence Platform · server.py
Flask backend — Ollama local LLMs + domain-specific knowledge pipeline

New CMPDI modules:
  IngestionPipeline → KnowledgeBase → Extractor → Validator
  EvidenceEngine → ReportAgent → Analytics

Kept from ARIA:
  Gateway → Router → AgentOrchestrator → ToolRegistry → STT (query dictation)
────────────────────────────────────────────────────────────────────────
Setup:
    pip install flask flask-cors ollama pypdf python-docx python-pptx openpyxl
    pip install pytesseract Pillow pdf2image pdfplumber faiss-cpu pandas
    # Windows OCR: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki
    # Embeddings: ollama pull nomic-embed-text

Run:
    python server.py

Then open index.html in your browser.
"""

import os
import sys
import uuid
import time
import tempfile
import automation
import json
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import io

# Suppress HuggingFace symlinks warning on Windows (cosmetic only — caching still works)
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
from faster_whisper import WhisperModel

# Force UTF-8 output on Windows to avoid UnicodeEncodeError with box-drawing chars
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import ollama
except ImportError:
    print("[ERROR] ollama not installed. Run: pip install ollama")
    exit(1)

# ─── Multi-Model Architecture Imports ─────────────────
from gateway import ModelGateway, GatewayError, ModelNotFoundError
from router import ModelRouter
from orchestrator import Orchestrator, AgentOrchestrator
from sandbox import CodeSandbox
from doc_editor import apply_edit, is_edit_intent
from tool_registry import ToolRegistry
from agent_state import AgentState

# ─── CMPDI/CIL Domain Modules ─────────────────────────
from ingestion import IngestionPipeline
from knowledge_base import KnowledgeBase, chunk_document
from extractor import extract_facts_from_document
from validator import ConflictDetector
from evidence import EvidenceEngine
from report_agent import ReportAgent
import analytics as analytics_engine

# ─── App Setup ────────────────────────────────────────
app = Flask(__name__)
CORS(app)  # Allow all origins for local development

DEFAULT_MODEL = os.environ.get("ARIA_MODEL", "llama3.2")

CMPDI_SYSTEM_PROMPT = (
    "You are ARIA-CIL, an expert AI assistant for CMPDI/CIL (Coal India Limited) "
    "document intelligence. You help officials search, analyse, and summarise "
    "mining, geological, production, and exploration documents. "
    "Always cite sources and page numbers when referencing specific data. "
    "For numerical figures, use values from the knowledge base, not general knowledge. "
    "Flag any data conflicts or gaps explicitly."
)

# ─── Initialize Multi-Model Components ────────────────
gateway      = ModelGateway(default_model=DEFAULT_MODEL, keep_alive="5m")
router       = ModelRouter(default_model=DEFAULT_MODEL)
sandbox      = CodeSandbox(timeout=5)
orchestrator = Orchestrator(gateway=gateway, router=router, sandbox=sandbox)

from memory_store import MemoryStore
memory_store = MemoryStore()

# ─── v3.0: ToolRegistry + AgentOrchestrator ───────────
# doc_store and download_store are defined below; pass by reference so
# the registry always sees the live dict contents.
_doc_store:      dict[str, dict] = {}
_download_store: dict[str, dict] = {}
DOWNLOAD_TTL_SECONDS = 900  # 15 minutes

# Seed _doc_store with persistent memory docs
for m_id, m_doc in memory_store._docs.items():
    _doc_store[m_id] = {
        "filename": m_doc["filename"],
        "text": m_doc["text"],
        "bytes": b"",
    }

tool_registry = ToolRegistry(
    gateway=gateway,
    doc_store=_doc_store,
    sandbox=sandbox,
    download_store=_download_store,
)
agent_orchestrator = AgentOrchestrator(
    gateway=gateway,
    router=router,
    sandbox=sandbox,
    tool_registry=tool_registry,
)

# In-progress agent runs (for polling)
_agent_runs: dict[str, dict] = {}   # run_id → {state, synthesis, done}

print("[CMPDI] Platform initialized:")
print(f"  -> Gateway:            default={DEFAULT_MODEL}, keep_alive=5m")
print(f"  -> Router:             heuristic mode, {len(router.get_routing_table())} task types")
print(f"  -> Sandbox:            timeout=5s, Python only")
print(f"  -> Orchestrator (v2):  max_steps={Orchestrator.MAX_STEPS}")
print(f"  -> AgentOrchestrator:  max_steps={AgentOrchestrator.MAX_STEPS}, max_replans={AgentOrchestrator.MAX_REPLANS}")
print(f"  -> ToolRegistry:       {len(tool_registry.list_tools())} tools registered")

# ─── CMPDI Knowledge Layer (initialise once) ──────────
knowledge_base   = KnowledgeBase()
ingestion_pipeline = IngestionPipeline()
evidence_engine  = EvidenceEngine(knowledge_base)
conflict_detector= ConflictDetector(knowledge_base)
report_agent     = ReportAgent(gateway, knowledge_base, evidence_engine)

# In-memory report store (report_id → ReportResult)
_report_store: dict[str, object] = {}

print(f"[CMPDI] Knowledge base: {knowledge_base.stats()}")

# ─── STT: Load faster-whisper once at startup ──────────
# Kept for query dictation (mic button) — not primary UI
WHISPER_MODEL = WhisperModel(
    "base",
    device="cpu",
    compute_type="int8"
)
print("[CMPDI] faster-whisper STT loaded (query dictation only)")

# NOTE: _doc_store, _download_store, and DOWNLOAD_TTL_SECONDS
# are now initialised above (before ToolRegistry) so the registry
# holds a live reference to the same dict objects.

# Models you have pulled locally via `ollama pull <name>`.
# Now includes routing task info for the frontend.
AVAILABLE_MODELS = [
    {"id": "llama3.2",    "name": "Llama 3.2 3B",      "description": "Reasoning, planning, general - default",        "tasks": ["reasoning", "general"]},
    {"id": "qwen3:4b",    "name": "Qwen 3 4B",         "description": "Code generation and review specialist",         "tasks": ["code"]},
    {"id": "phi3",        "name": "Phi-3 Mini",         "description": "Fast fallback for simple queries",              "tasks": ["general"]},
    {"id": "llava",       "name": "LLaVA Vision",       "description": "Image understanding and visual analysis",       "tasks": ["vision"]},
]


# ─── Static Files & Assets ────────────────────────────
@app.route("/enchance_the_video_quality_int.mp4", methods=["GET"])
def serve_bg_video_enchance():
    video_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enchance_the_video_quality_int.mp4")
    return send_file(video_path, mimetype="video/mp4")


@app.route("/hf_20260405_171521_25968ba2-b594-4b32-aab7-f6b69398a6fa.mp4", methods=["GET"])
def serve_bg_video():
    video_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hf_20260405_171521_25968ba2-b594-4b32-aab7-f6b69398a6fa.mp4")
    return send_file(video_path, mimetype="video/mp4")


@app.route("/cmpdi.css", methods=["GET"])
def serve_css():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmpdi.css"), mimetype="text/css")


@app.route("/cmpdi.js", methods=["GET"])
def serve_js():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmpdi.js"), mimetype="application/javascript")


@app.route("/index.html", methods=["GET"])
def serve_index_html():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), mimetype="text/html")


# ─── Health Check & Root ──────────────────────────────
@app.route("/", methods=["GET"])
def index():
    if "text/html" in request.headers.get("Accept", ""):
        return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), mimetype="text/html")
    return jsonify({
        "status": "ok",
        "service": "ARIA-CIL Sovereign Document Intelligence Platform",
        "ollama_running": gateway.is_available(),
        "provider": "Ollama - Local, offline LLM",
        "default_model": DEFAULT_MODEL,
        "architecture": {
            "gateway": True,
            "router": "heuristic",
            "orchestrator": True,
            "sandbox": True,
        },
    })


@app.route("/api/health", methods=["GET"])
def health():
    available = gateway.is_available()
    return jsonify({
        "status":        "healthy" if available else "ollama_unreachable",
        "ollama_running": available,
        "provider":      "ollama",
        "default_model": DEFAULT_MODEL,
        "gateway_stats": gateway.stats(),
    })


# ─── Models Endpoint ──────────────────────────────────
@app.route("/api/models", methods=["GET"])
def models():
    """Return list of locally available models with routing info."""
    pulled_names = set(gateway.list_available())

    result = []
    for m in AVAILABLE_MODELS:
        entry = dict(m)
        entry["pulled"] = any(m["id"] in name for name in pulled_names) if pulled_names else None
        result.append(entry)

    return jsonify({
        "models": result,
        "routing_table": router.get_routing_table(),
    })


# ─── Main Chat Endpoint (Gateway + Router) ────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running. Start it (it usually auto-starts) or run 'ollama serve'."}), 500

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON body."}), 400

    messages      = data.get("messages", [])
    model         = data.get("model")          # None means "let Router decide"
    auto_route    = data.get("auto_route", True)
    system_prompt = data.get("system", "You are ARIA, a helpful AI voice assistant.")

    if not messages:
        return jsonify({"error": "No messages provided."}), 400

    # Validate messages format
    for msg in messages:
        if "role" not in msg or "content" not in msg:
            return jsonify({"error": "Each message must have 'role' and 'content'."}), 400
        if msg["role"] not in ("user", "assistant"):
            return jsonify({"error": f"Invalid role: {msg['role']}"}), 400

    # ─── CMPDI System Prompt injection ───
    # Use CMPDI domain prompt as default; user can override via 'system' field
    if system_prompt == "You are ARIA, a helpful AI voice assistant." or not system_prompt:
        system_prompt = CMPDI_SYSTEM_PROMPT

    try:
        # ─── Smart Routing ───
        latest_text = messages[-1]["content"] if messages else ""
        has_image = any("image" in str(msg.get("images", "")) for msg in messages)

        if auto_route and not model:
            # Let the Router pick the best model
            decision = router.route(latest_text, has_image=has_image)
        else:
            # User explicitly selected a model - bypass routing
            decision = router.route(latest_text, has_image=has_image,
                                    force_model=model or DEFAULT_MODEL)

        # ─── Memory Document Context (RAG) ───
        memory_context, memory_sources = memory_store.format_prompt_context(latest_text)
        if memory_context:
            system_prompt = (system_prompt or "") + "\n\n" + memory_context

        # ─── Call via Gateway ───
        ollama_messages = [{"role": "system", "content": system_prompt}] + messages

        gw_response = gateway.call(
            decision.model,
            ollama_messages,
        )

        return jsonify({
            "content": gw_response.content,
            "model": gw_response.model,
            "routing": decision.to_dict(),
            "memory_sources": memory_sources,
            "usage": {
                "input_tokens": gw_response.tokens_in,
                "output_tokens": gw_response.tokens_out,
            },
            "latency": round(gw_response.latency, 2),
            "stop_reason": "stop" if gw_response.done else None,
        })

    except ModelNotFoundError as e:
        return jsonify({"error": f"Model '{e.model}' not found locally. Run: ollama pull {e.model}"}), 404
    except GatewayError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


# ─── Tool Endpoint (one-shot via Gateway) ─────────────
@app.route("/api/tool", methods=["POST"])
def tool():
    """Run a one-shot tool prompt, routed through the Gateway."""
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running."}), 500

    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    model  = data.get("model")

    if not prompt:
        return jsonify({"error": "No prompt provided."}), 400

    try:
        # Route the tool prompt to the best model
        decision = router.route(prompt, force_model=model)

        gw_response = gateway.call(
            decision.model,
            [{"role": "user", "content": prompt}],
        )
        return jsonify({
            "content": gw_response.content,
            "model": gw_response.model,
            "routing": decision.to_dict(),
        })
    except GatewayError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Orchestrate Endpoint (Deep Think) ────────────────
@app.route("/api/orchestrate", methods=["POST"])
def orchestrate():
    """Multi-step plan-execute-verify workflow for complex tasks."""
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running."}), 500

    data = request.get_json(silent=True) or {}
    user_request  = data.get("request", "").strip()
    system_prompt = data.get("system")

    if not user_request:
        return jsonify({"error": "No request provided."}), 400

    try:
        memory_context, memory_sources = memory_store.format_prompt_context(user_request)
        if memory_context:
            system_prompt = (system_prompt or "") + "\n\n" + memory_context

        print(f"[Orchestrator] Starting multi-step workflow: {user_request[:80]}...")
        result = orchestrator.run(user_request, system_prompt=system_prompt)
        res_dict = result.to_dict()
        res_dict["memory_sources"] = memory_sources
        return jsonify(res_dict)

    except GatewayError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": f"Orchestrator error: {str(e)}"}), 500


# ─── Routing Info Endpoint ────────────────────────────
@app.route("/api/routing-info", methods=["GET", "POST"])
def routing_info():
    """Return routing table, recent decisions, and gateway stats."""
    info = {
        "routing_table": router.get_routing_table(),
        "recent_decisions": router.get_recent_decisions(10),
        "gateway_stats": gateway.stats(),
        "available_models": gateway.list_available(),
    }

    # If POST, also show what the router would decide for the given text
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        if text:
            decision = router.route(text)
            info["preview"] = decision.to_dict()

    return jsonify(info)


# ─── Warm-up (via Gateway) ────────────────────────────
def warm_up():
    """Pre-load the default model into VRAM via the Gateway."""
    gateway.warm_up()


# ─── Document Upload ──────────────────────────────────
def _extract_text(filename: str, file_bytes: bytes) -> str:
    """Extract plain text from PDF, DOCX, XLSX, TXT, or MD file bytes."""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(file_bytes))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages).strip()
        except ImportError:
            return "[ERROR] pypdf not installed. Run: pip install pypdf"
        except Exception as e:
            return f"[ERROR] Could not read PDF: {e}"

    if ext == ".docx":
        try:
            from docx import Document
            import io
            doc = Document(io.BytesIO(file_bytes))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
        except ImportError:
            return "[ERROR] python-docx not installed. Run: pip install python-docx"
        except Exception as e:
            return f"[ERROR] Could not read DOCX: {e}"

    if ext in (".xlsx", ".xls"):
        try:
            import pandas as pd
            df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
            return df.to_csv(index=False).strip()
        except ImportError:
            return "[ERROR] pandas/openpyxl not installed. Run: pip install pandas openpyxl"
        except Exception as e:
            return f"[ERROR] Could not read Excel file: {e}"

    if ext in (".txt", ".md", ".markdown", ".rst", ".csv"):
        try:
            return file_bytes.decode("utf-8", errors="replace").strip()
        except Exception as e:
            return f"[ERROR] Could not read file: {e}"

    return f"[ERROR] Unsupported file type: {ext}"


@app.route("/api/upload-doc", methods=["POST"])
def upload_doc():
    """Accept a file upload, extract its text, and store it for querying."""
    if "file" not in request.files:
        return jsonify({"error": "No file part in the request."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected."}), 400

    allowed_ext = {".pdf", ".txt", ".md", ".markdown", ".docx", ".rst", ".csv", ".xlsx", ".xls"}
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed_ext:
        return jsonify({"error": f"Unsupported file type '{ext}'. Allowed: PDF, TXT, MD, DOCX, XLSX, CSV"}), 415

    file_bytes = f.read()
    text = _extract_text(f.filename, file_bytes)

    if text.startswith("[ERROR]"):
        return jsonify({"error": text}), 422

    doc_id = str(uuid.uuid4())
    # Store raw bytes too so the editor can re-serialise the file after edits
    _doc_store[doc_id] = {
        "filename": f.filename,
        "text":     text,
        "bytes":    file_bytes,
    }

    print(f"[INFO] Document stored: {f.filename} ({len(text):,} chars) -- id={doc_id}")
    return jsonify({
        "doc_id":     doc_id,
        "filename":   f.filename,
        "char_count": len(text),
        "text":       text,
        "preview":    text[:300] + ("..." if len(text) > 300 else ""),
    })


@app.route("/api/doc/<doc_id>", methods=["GET"])
def get_doc_content(doc_id):
    """Retrieve full text content of an uploaded session document."""
    if not doc_id or doc_id not in _doc_store:
        return jsonify({"error": "Document not found."}), 404
    doc = _doc_store[doc_id]
    return jsonify({
        "doc_id":     doc_id,
        "filename":   doc["filename"],
        "char_count": len(doc.get("text", "")),
        "text":       doc.get("text", ""),
    })


# ─── Memory Document Endpoints (Persistent Knowledge Base) ────
@app.route("/api/memory/upload", methods=["POST"])
def memory_upload():
    """Upload a document directly to persistent memory so all chats have access."""
    if "file" not in request.files:
        return jsonify({"error": "No file part in the request."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected."}), 400

    allowed_ext = {".pdf", ".txt", ".md", ".markdown", ".docx", ".rst", ".csv"}
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed_ext:
        return jsonify({"error": f"Unsupported file type '{ext}'. Allowed: PDF, TXT, MD, DOCX, CSV"}), 415

    file_bytes = f.read()
    text = _extract_text(f.filename, file_bytes)

    if text.startswith("[ERROR]"):
        return jsonify({"error": text}), 422

    doc_meta = memory_store.save_document(f.filename, file_bytes, text)

    # Also register in _doc_store so tools and agents have access
    _doc_store[doc_meta["doc_id"]] = {
        "filename": f.filename,
        "text": text,
        "bytes": file_bytes,
    }

    return jsonify({
        "success": True,
        "document": doc_meta,
        "message": f"'{f.filename}' saved to persistent memory."
    })


@app.route("/api/memory/docs", methods=["GET"])
def memory_list_docs():
    """List all documents currently in persistent memory."""
    return jsonify({
        "documents": memory_store.list_documents()
    })


@app.route("/api/memory/docs/<doc_id>", methods=["GET"])
def memory_get_doc(doc_id):
    """Retrieve full details and text preview of a memory document."""
    doc = memory_store.get_document(doc_id)
    if not doc:
        return jsonify({"error": "Document not found."}), 404
    return jsonify({
        "document": doc
    })


@app.route("/api/memory/docs/<doc_id>", methods=["DELETE"])
def memory_delete_doc(doc_id):
    """Remove a document from persistent memory."""
    success = memory_store.delete_document(doc_id)
    _doc_store.pop(doc_id, None)
    if success:
        return jsonify({"success": True, "message": "Document removed from memory."})
    return jsonify({"error": "Document not found."}), 404


@app.route("/api/memory/promote/<doc_id>", methods=["POST"])
def memory_promote_doc(doc_id):
    """Promote a session document from _doc_store to persistent memory."""
    if doc_id not in _doc_store:
        return jsonify({"error": "Document not found in current session."}), 404

    doc = _doc_store[doc_id]
    doc_meta = memory_store.save_document(
        filename=doc["filename"],
        file_bytes=doc.get("bytes", b""),
        text=doc.get("text", "")
    )
    return jsonify({
        "success": True,
        "document": doc_meta,
        "message": f"'{doc['filename']}' added to assistant memory."
    })


@app.route("/api/memory/clear", methods=["POST"])
def memory_clear_docs():
    """Clear all documents from memory store."""
    count = memory_store.clear_documents()
    return jsonify({"success": True, "cleared_count": count})


# ─── Document Query (via Gateway) ─────────────────────
@app.route("/api/doc-query", methods=["POST"])
def doc_query():
    """Answer a user question grounded in the uploaded document text, routed via Gateway."""
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running."}), 500

    data = request.get_json(silent=True) or {}
    doc_id   = data.get("doc_id", "")
    question = data.get("question", "").strip()
    model    = data.get("model")  # None = let Router decide
    history  = data.get("history", [])  # optional prior Q&A in this doc session

    if not doc_id or doc_id not in _doc_store:
        return jsonify({"error": "Document not found. Please re-upload your file."}), 404
    if not question:
        return jsonify({"error": "No question provided."}), 400

    doc = _doc_store[doc_id]
    # Truncate to ~12 000 chars to stay inside context window of small models
    MAX_DOC_CHARS = 12_000
    doc_text = doc["text"]
    if len(doc_text) > MAX_DOC_CHARS:
        doc_text = doc_text[:MAX_DOC_CHARS] + "\n\n[...document truncated for context window...]"

    system_prompt = (
        f"You are ARIA-CIL, a CMPDI/CIL document intelligence assistant.\n"
        f"The user has uploaded a document titled '{doc['filename']}'.\n"
        f"Use ONLY the document content below to answer questions. "
        f"Cite page numbers and specific figures where possible. "
        f"If the answer is not in the document, say so clearly.\n\n"
        f"=== DOCUMENT CONTENT ===\n{doc_text}\n=== END OF DOCUMENT ==="
    )

    # Build message list: system + optional prior turns + current question
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    try:
        # Route doc queries - use user's model if specified, else auto-route
        decision = router.route(question, force_model=model)

        gw_response = gateway.call(decision.model, messages)

        return jsonify({
            "content": gw_response.content,
            "model":   gw_response.model,
            "routing": decision.to_dict(),
            "doc_id":  doc_id,
        })
    except ModelNotFoundError as e:
        return jsonify({"error": f"Model '{e.model}' not found locally. Run: ollama pull {e.model}"}), 404
    except GatewayError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


# ─── Document Edit ────────────────────────────────────
@app.route("/api/doc-edit", methods=["POST"])
def doc_edit():
    """
    Apply an AI-guided edit to an uploaded document, update the doc store,
    create a download token, and return a summary + token.
    """
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running."}), 500

    data        = request.get_json(silent=True) or {}
    doc_id      = data.get("doc_id", "")
    instruction = data.get("instruction", "").strip()
    model       = data.get("model")

    if not doc_id or doc_id not in _doc_store:
        return jsonify({"error": "Document not found. Please re-upload your file."}), 404
    if not instruction:
        return jsonify({"error": "No instruction provided."}), 400

    doc = _doc_store[doc_id]

    # Build a simple LLM callable that routes through the Gateway
    chosen_model = model or DEFAULT_MODEL
    def llm_fn(prompt: str) -> str:
        resp = gateway.call(chosen_model, [{"role": "user", "content": prompt}])
        return resp.content

    try:
        result = apply_edit(
            filename=doc["filename"],
            file_bytes=doc["bytes"],
            instruction=instruction,
            llm_fn=llm_fn,
        )
    except Exception as e:
        return jsonify({"error": f"Edit failed: {str(e)}"}), 500

    if not result.success:
        return jsonify({"error": result.error or "Edit failed.", "details": result.to_dict()}), 422

    # Update the doc store with the edited content
    _doc_store[doc_id]["bytes"] = result.new_bytes
    _doc_store[doc_id]["text"]  = result.new_text

    # Purge expired download tokens
    now = time.time()
    expired = [t for t, v in _download_store.items() if v["expires"] < now]
    for t in expired:
        del _download_store[t]

    # Create a fresh download token
    token = str(uuid.uuid4())
    _download_store[token] = {
        "doc_id":   doc_id,
        "filename": doc["filename"],
        "bytes":    result.new_bytes,
        "expires":  now + DOWNLOAD_TTL_SECONDS,
    }

    print(f"[INFO] Document edited: {doc['filename']} -- {result.summary}")
    return jsonify({
        "success":        True,
        "summary":        result.summary,
        "changes":        result.changes,
        "download_token": token,
        "filename":       doc["filename"],
        "char_count":     len(result.new_text),
        "text":           result.new_text,
    })


# ─── Document Download ────────────────────────────────
@app.route("/api/doc-download/<token>", methods=["GET"])
def doc_download(token):
    """Stream the edited document file as a download attachment."""
    entry = _download_store.get(token)
    if not entry:
        return jsonify({"error": "Download token not found or expired."}), 404
    if time.time() > entry["expires"]:
        del _download_store[token]
        return jsonify({"error": "Download token has expired. Please re-apply the edit."}), 410

    filename  = entry["filename"]
    file_bytes = entry["bytes"]

    # Determine MIME type
    ext = os.path.splitext(filename)[1].lower()
    mime_map = {
        ".csv":  "text/csv",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls":  "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt":  "text/plain",
        ".md":   "text/markdown",
        ".rst":  "text/plain",
        ".pdf":  "application/pdf",
    }
    mime = mime_map.get(ext, "application/octet-stream")

    # Use 'edited_' prefix only for doc edits; draft exports already have a proper name
    doc_id = entry.get("doc_id")
    dl_name = ("edited_" + filename) if doc_id else filename

    return send_file(
        io.BytesIO(file_bytes),
        mimetype=mime,
        as_attachment=True,
        download_name=dl_name,
    )


# ─── Image OCR ───────────────────────────────────────
@app.route("/api/ocr-image", methods=["POST"])
def ocr_image_endpoint():
    """
    Accept an image upload, extract text via Tesseract OCR.
    Falls back to LLaVA vision model if OCR yields < 20 characters.

    Accepts multipart form with field 'file'.
    Optional form field 'prompt' for a custom vision question.
    Returns: { text, method, char_count, preview }
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected."}), 400

    allowed_img = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed_img:
        return jsonify({"error": f"Unsupported image type '{ext}'. Allowed: PNG, JPG, BMP, TIFF, GIF, WEBP"}), 415

    file_bytes = f.read()
    custom_prompt = request.form.get("prompt", "").strip()

    from multimodal import ocr_image, analyze_image_with_vision

    # Step 1: Try Tesseract OCR
    ocr_result = ocr_image(file_bytes)
    method = "tesseract"
    text = ""

    if ocr_result.success and len(ocr_result.text.strip()) >= 20:
        text = ocr_result.text
    else:
        # Step 2: Fall back to LLaVA vision model
        if gateway.is_available():
            vision_prompt = custom_prompt or (
                "Please read and extract ALL text visible in this image. "
                "List everything you can see written in the image, word for word."
            )
            text = analyze_image_with_vision(file_bytes, gateway, prompt=vision_prompt)
            method = "llava-vision"
        elif ocr_result.error:
            return jsonify({"error": f"OCR failed: {ocr_result.error}"}), 500
        else:
            text = ocr_result.text or "[No text found in image]"

    print(f"[OCR] {f.filename} → method={method}, chars={len(text)}")
    return jsonify({
        "text":       text,
        "method":     method,
        "char_count": len(text),
        "preview":    text[:400] + ("..." if len(text) > 400 else ""),
        "filename":   f.filename,
    })


# ─── Export Draft (Chat Reply → PDF or DOCX) ─────────
@app.route("/api/export-draft", methods=["POST"])
def export_draft():
    """
    Convert a text draft to a downloadable PDF or DOCX file.

    Body: { "content": "...", "format": "pdf"|"docx", "title": "..." }
    Returns: { download_token, filename }
    """
    data    = request.get_json(silent=True) or {}
    content = data.get("content", "").strip()
    fmt     = data.get("format", "pdf").lower()
    title   = data.get("title", "ARIA Draft").strip() or "ARIA Draft"

    if not content:
        return jsonify({"error": "No content provided."}), 400
    if fmt not in ("pdf", "docx"):
        return jsonify({"error": "Format must be 'pdf' or 'docx'."}), 400

    import datetime
    date_str = datetime.date.today().isoformat()

    try:
        if fmt == "docx":
            from doc_generator import generate_docx
            doc_data = {
                "title": title,
                "author": "ARIA",
                "date": date_str,
                "sections": [{"heading": "", "body": content}],
                "footer": f"Generated by ARIA — {date_str}",
            }
            file_bytes = generate_docx(doc_data)
            filename   = f"{title.replace(' ', '_')}.docx"
            mime       = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        else:  # pdf
            file_bytes = _generate_pdf(title, content, date_str)
            filename   = f"{title.replace(' ', '_')}.pdf"
            mime       = "application/pdf"

    except Exception as e:
        return jsonify({"error": f"Export failed: {str(e)}"}), 500

    # Store in download_store
    now   = time.time()
    token = str(uuid.uuid4())
    _download_store[token] = {
        "doc_id":   None,
        "filename": filename,
        "bytes":    file_bytes,
        "expires":  now + DOWNLOAD_TTL_SECONDS,
    }

    print(f"[Export] '{title}' → {fmt.upper()} ({len(file_bytes):,} bytes) token={token[:8]}")
    return jsonify({
        "download_token": token,
        "filename":       filename,
        "format":         fmt,
        "byte_size":      len(file_bytes),
    })


def _generate_pdf(title: str, content: str, date_str: str) -> bytes:
    """Generate a clean, styled PDF from plain text content using reportlab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
    except ImportError:
        raise RuntimeError("reportlab not installed. Run: pip install reportlab")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )

    styles = getSampleStyleSheet()
    accent = colors.HexColor("#00e5ff")
    dark   = colors.HexColor("#0d1117")
    mid    = colors.HexColor("#555577")

    title_style = ParagraphStyle(
        "ARIATitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        textColor=dark,
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "ARIAMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=mid,
        spaceAfter=12,
        alignment=TA_LEFT,
    )
    body_style = ParagraphStyle(
        "ARIABody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        textColor=dark,
        leading=17,
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        "ARIABullet",
        parent=body_style,
        leftIndent=18,
        spaceAfter=4,
    )

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(f"Generated by ARIA &nbsp;·&nbsp; {date_str}", meta_style))
    story.append(HRFlowable(width="100%", thickness=1, color=accent, spaceAfter=14))

    # Parse content lines into paragraphs / bullets
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 6))
            continue
        # Markdown-style headings
        if stripped.startswith("### "):
            story.append(Paragraph(f"<b>{stripped[4:]}</b>", ParagraphStyle(
                "H3", parent=body_style, fontSize=12, spaceAfter=4, spaceBefore=10)))
        elif stripped.startswith("## "):
            story.append(Paragraph(f"<b>{stripped[3:]}</b>", ParagraphStyle(
                "H2", parent=body_style, fontSize=14, spaceAfter=6, spaceBefore=12)))
        elif stripped.startswith("# "):
            story.append(Paragraph(f"<b>{stripped[2:]}</b>", ParagraphStyle(
                "H1", parent=body_style, fontSize=16, spaceAfter=8, spaceBefore=14)))
        elif stripped.startswith(("- ", "* ", "• ")):
            story.append(Paragraph(f"• {stripped[2:]}", bullet_style))
        else:
            # Escape special XML chars for reportlab
            safe = stripped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, body_style))

    story.append(Spacer(1, 24))
    story.append(HRFlowable(width="100%", thickness=0.5, color=mid))
    story.append(Paragraph("ARIA — Agentic AI Desktop Assistant", meta_style))

    doc.build(story)
    return buf.getvalue()


# ─── Speech-to-Text (faster-whisper) ──────────────────
@app.route("/api/stt", methods=["POST"])
def transcribe_audio():
    """Accept an audio file upload, transcribe it with faster-whisper, return text."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided."}), 400

    audio_file = request.files["audio"]

    # Save to a temp file - faster-whisper reads from disk/path
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        segments, info = WHISPER_MODEL.transcribe(
            tmp_path,
            language="en",
            beam_size=1,        # fastest setting - good enough for command-style speech
            vad_filter=True     # skips silence automatically, improves accuracy
        )
        text = " ".join(segment.text.strip() for segment in segments)
        return jsonify({"text": text.strip(), "language": info.language})
    except Exception as e:
        return jsonify({"error": f"Transcription failed: {str(e)}"}), 500
    finally:
        os.remove(tmp_path)  # clean up temp file regardless of success/failure


# ─── Agent Run — POST /api/agent ──────────────────────
@app.route("/api/agent", methods=["POST"])
def agent_run():
    """
    Full agentic loop: Plan → Execute → Verify → Replan → Synthesize.

    Request body:
        {
          "goal":   "Analyze the inspection report and create an approval note",
          "files":  ["doc_id_1", "doc_id_2"],   # optional: pre-uploaded doc IDs
          "system": "optional system context"
        }

    Response:
        AgentState.to_dict() plus "synthesis" field.
    """
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running."}), 500

    data          = request.get_json(silent=True) or {}
    goal          = data.get("goal", "").strip()
    file_ids      = data.get("files", [])   # list of doc_ids already in _doc_store
    system_prompt = data.get("system")

    if not goal:
        return jsonify({"error": "No goal provided."}), 400

    # Build uploaded_files dict from pre-uploaded documents
    uploaded = {}
    for fid in file_ids:
        if fid in _doc_store:
            uploaded[fid] = _doc_store[fid]

    # Also include ALL currently uploaded docs if no specific files requested
    if not file_ids:
        uploaded = dict(_doc_store)

    state = AgentState(goal=goal, uploaded_files=uploaded)

    # Register this run for optional polling
    _agent_runs[state.run_id] = {"state": state, "synthesis": None, "done": False}

    try:
        print(f"[Agent] Starting run {state.run_id[:8]} — goal: {goal[:80]}")
        state, synthesis = agent_orchestrator.run(
            goal, state, system_prompt=system_prompt
        )

        # Expose generated files as download tokens in the main _download_store
        # (already done inside _store_generated via ToolRegistry, but re-sync here)
        result = state.to_dict()
        result["synthesis"] = synthesis

        _agent_runs[state.run_id]["synthesis"] = synthesis
        _agent_runs[state.run_id]["done"]      = True

        return jsonify(result)

    except GatewayError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Agent error: {str(e)}"}), 500


# ─── Agent Status Polling ─────────────────────────────
@app.route("/api/agent/status/<run_id>", methods=["GET"])
def agent_status(run_id):
    """Return current status of an agent run (for live progress updates)."""
    run = _agent_runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found."}), 404
    state: AgentState = run["state"]
    result = state.to_dict()
    result["synthesis"] = run.get("synthesis")
    result["done"]      = run["done"]
    return jsonify(result)


# ─── Agent File Download ──────────────────────────────
@app.route("/api/agent/download/<token>", methods=["GET"])
def agent_download(token):
    """Download a file generated by the agent (DOCX, PPTX, XLSX)."""
    entry = _download_store.get(token)
    if not entry:
        return jsonify({"error": "Token not found or expired."}), 404
    if time.time() > entry["expires"]:
        del _download_store[token]
        return jsonify({"error": "Token expired."}), 410

    filename   = entry["filename"]
    file_bytes = entry["bytes"]
    ext        = os.path.splitext(filename)[1].lower()
    mime_map   = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv":  "text/csv",
        ".txt":  "text/plain",
    }
    mime = mime_map.get(ext, "application/octet-stream")
    return send_file(
        io.BytesIO(file_bytes),
        mimetype=mime,
        as_attachment=True,
        download_name=filename,
    )


# ─── Tool Registry Info ───────────────────────────────
@app.route("/api/tools", methods=["GET"])
def tools_info():
    """Return list of registered tools and recent tool call log."""
    return jsonify({
        "tools":    tool_registry.list_tools(),
        "call_log": tool_registry.get_log(20),
    })


# ════════════════════════════════════════════════════════
#   CMPDI/CIL DOMAIN ENDPOINTS
# ════════════════════════════════════════════════════════

# ─── Ingest Document ──────────────────────────────────
@app.route("/api/ingest", methods=["POST"])
def ingest_document():
    """
    Upload + fully ingest a document into the CMPDI knowledge base.
    Runs: ingestion → fact extraction → chunk indexing → conflict check.

    Multipart form: field 'file'
    Optional form fields: 'use_llm' (true/false), 'organization', 'period'
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected."}), 400

    allowed = {".pdf", ".docx", ".xlsx", ".xls", ".csv",
               ".txt", ".md", ".png", ".jpg", ".jpeg", ".tiff"}
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in allowed:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 415

    file_bytes = f.read()
    use_llm    = request.form.get("use_llm", "false").lower() == "true"

    try:
        # Step 1: Ingest
        doc = ingestion_pipeline.ingest(f.filename, file_bytes)
        if not doc.success:
            return jsonify({"error": f"Ingestion failed: {doc.error}"}), 422

        # Override metadata if provided
        if request.form.get("organization"):
            doc.metadata["organization"] = request.form.get("organization")
        if request.form.get("period"):
            doc.metadata["year"] = request.form.get("period")

        # Step 2: Store in knowledge base
        knowledge_base.add_document(doc.to_summary_dict())

        # Step 3: Extract facts
        facts = extract_facts_from_document(
            doc,
            gateway=gateway if use_llm else None,
            use_llm=use_llm,
        )
        facts_added = knowledge_base.add_facts(facts)

        # Step 4: Chunk + index for vector search
        chunks = chunk_document(doc)
        chunks_added = knowledge_base.add_chunks(doc.doc_id, chunks, gateway=gateway)
        knowledge_base.mark_indexed(doc.doc_id)

        # Step 5: Quick conflict check
        conflicts = conflict_detector.detect_all()
        new_conflicts = [c for c in conflicts
                         if any(f.doc_id == doc.doc_id for f in [] )]

        print(f"[Ingest] {f.filename}: {facts_added} facts, {chunks_added} chunks")
        return jsonify({
            "success":      True,
            "doc_id":       doc.doc_id,
            "filename":     f.filename,
            "page_count":   doc.page_count,
            "char_count":   doc.char_count,
            "ocr_applied":  doc.ocr_applied,
            "facts_extracted": facts_added,
            "chunks_indexed":  chunks_added,
            "metadata":     doc.metadata,
            "ingestion_time_s": doc.ingestion_time_s,
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Ingestion error: {str(e)}"}), 500


# ─── List Documents ────────────────────────────────────
@app.route("/api/documents", methods=["GET"])
def list_documents():
    """List all documents indexed in the CMPDI knowledge base."""
    docs = knowledge_base.list_documents()
    stats = knowledge_base.stats()
    return jsonify({"documents": docs, "stats": stats})


# ─── Delete Document ───────────────────────────────────
@app.route("/api/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    """Remove a document and all its facts/chunks from the knowledge base."""
    success = knowledge_base.delete_document(doc_id)
    if success:
        return jsonify({"success": True, "doc_id": doc_id})
    return jsonify({"error": "Document not found."}), 404


# ─── CMPDI Query (Hybrid RAG + Evidence) ──────────────
@app.route("/api/query", methods=["POST"])
def cmpdi_query():
    """
    Answer a mining/geological query using hybrid RAG (BM25 + vector)
    with evidence tracing and conflict detection.

    Body: { "query": str, "organization": str, "period": str }
    """
    data         = request.get_json(silent=True) or {}
    query        = data.get("query", "").strip()
    organization = data.get("organization", "")
    period       = data.get("period", "")

    if not query:
        return jsonify({"error": "No query provided."}), 400

    # ── Graceful degradation: serve KB facts even without Ollama ──
    if not gateway.is_available():
        try:
            chunks = knowledge_base.hybrid_search(query, gateway=None, top_k=5)
            facts  = knowledge_base.query_facts(
                organization=organization or None,
                period=period or None,
                min_confidence=0.5,
                limit=10,
            )
            fact_lines = []
            for f in facts:
                fact_lines.append(
                    f"• {f.get('organization','?')} — {f.get('activity') or f.get('metric','?')}: "
                    f"{f.get('value','')} {f.get('unit','')} ({f.get('period','')})"
                )
            chunk_lines = [c.get("text","")[:300] for c in chunks[:3]]
            answer = (
                "[Ollama offline — showing knowledge base facts only]\n\n"
                + ("\n".join(fact_lines) if fact_lines else "No matching facts found.")
                + ("\n\nRelevant passages:\n" + "\n---\n".join(chunk_lines) if chunk_lines else "")
            )
            sources = list({c.get("filename","") for c in chunks if c.get("filename")})
            return jsonify({
                "answer":   answer,
                "model":    "knowledge_base_fallback",
                "evidence": {"evidence": [], "claim": query},
                "conflicts": [],
                "sources":  sources,
                "latency":  0.0,
                "offline":  True,
            })
        except Exception as e:
            return jsonify({"error": f"Ollama offline, KB fallback also failed: {e}"}), 500

    try:
        # Hybrid retrieval
        chunks = knowledge_base.hybrid_search(query, gateway=gateway, top_k=8)
        facts  = knowledge_base.query_facts(
            organization=organization or None,
            period=period or None,
            min_confidence=0.5,
            limit=20,
        )

        # Build context for LLM
        context_parts = []
        for c in chunks[:6]:
            context_parts.append(
                f"[{c.get('filename','?')} p{c.get('page_num','')}] {c.get('text','')[:500]}"
            )
        for f in facts[:8]:
            context_parts.append(
                f"[FACT] {f.get('organization','')} / {f.get('activity','')} / "
                f"{f.get('value','')} {f.get('unit','')} / {f.get('period','')}"
            )

        context_str = "\n\n".join(context_parts) or "No relevant data found in the knowledge base."
        prompt = (
            f"{CMPDI_SYSTEM_PROMPT}\n\n"
            f"=== KNOWLEDGE BASE CONTEXT ===\n{context_str}\n"
            f"=== END CONTEXT ===\n\n"
            f"Question: {query}\n\n"
            f"Answer concisely with specific figures and source citations where available:"
        )

        decision    = router.route(query)
        gw_response = gateway.call(
            decision.model,
            [{"role": "user", "content": prompt}],
        )

        # Evidence tracing
        evidence_bundle = evidence_engine.find_evidence_for_answer(
            gw_response.content, query
        )

        # Conflict detection
        from extractor import extract_entities, _normalise_period
        from ontology import ACTIVITY_TYPES
        entities = extract_entities(query)
        conflicts = []
        for ent in entities:
            if ent.entity_type == "activity":
                period_hints = [e.canonical for e in entities if e.entity_type == "period"]
                org_hints    = [e.canonical for e in entities if e.entity_type == "subsidiary"]
                for ph in (period_hints or [""]):
                    c = conflict_detector.detect_for_query(
                        metric=ent.canonical,
                        period=ph,
                        organization=org_hints[0] if org_hints else None,
                    )
                    conflicts.extend(c)

        return jsonify({
            "answer":    gw_response.content,
            "model":     gw_response.model,
            "evidence":  evidence_bundle.to_dict(),
            "conflicts": [c.to_dict() for c in conflicts],
            "sources":   evidence_bundle.primary_sources(),
            "latency":   round(gw_response.latency, 2),
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Query error: {str(e)}"}), 500


# ─── Parliamentary Query Agent ─────────────────────────
@app.route("/api/parliamentary", methods=["POST"])
def parliamentary_query():
    """
    Agentic parliamentary query handler.
    Decomposes complex multi-part queries, retrieves per-sub-query,
    validates data, assembles a structured response.

    Body: { "query": str, "organization": str, "period_range": [start_year, end_year] }
    """
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running."}), 500

    data         = request.get_json(silent=True) or {}
    query        = data.get("query", "").strip()
    organization = data.get("organization", "")
    period_range = data.get("period_range", [])

    if not query:
        return jsonify({"error": "No query provided."}), 400

    system = (
        CMPDI_SYSTEM_PROMPT +
        "\n\nThis is a parliamentary / high-priority query. "
        "Provide a complete, factual, evidence-backed response. "
        "Structure your answer with: (1) Direct Answer, (2) Supporting Data Table, "
        "(3) Source Citations, (4) Data Gaps or Caveats."
    )

    try:
        # Use the full AgentOrchestrator for parliamentary queries
        state = __import__("agent_state").AgentState(
            goal=query,
            uploaded_files={},
        )
        _agent_runs[state.run_id] = {"state": state, "synthesis": None, "done": False}

        state, synthesis = agent_orchestrator.run(query, state, system_prompt=system)

        # Evidence trace the synthesis
        evidence_bundle = evidence_engine.find_evidence_for_answer(synthesis, query)

        _agent_runs[state.run_id]["synthesis"] = synthesis
        _agent_runs[state.run_id]["done"]      = True

        return jsonify({
            "response":  synthesis,
            "evidence":  evidence_bundle.to_dict(),
            "run_id":    state.run_id,
            "steps":     len(state.step_results) if hasattr(state, "step_results") else 0,
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Parliamentary query error: {str(e)}"}), 500


# ─── Generate Report ───────────────────────────────────
@app.route("/api/report", methods=["POST"])
def generate_report():
    """
    Generate a DOCX report using the Report Agent.

    Body: { "request": str, "title": str, "organization": str, "period": str }
    Returns: { report_id, download_token, issues, sources_used }
    """
    if not gateway.is_available():
        return jsonify({"error": "Ollama service is not running."}), 500

    data         = request.get_json(silent=True) or {}
    req_text     = data.get("request", "").strip()
    title        = data.get("title", "").strip()
    organization = data.get("organization", "")
    period       = data.get("period", "")

    if not req_text:
        return jsonify({"error": "No report request provided."}), 400

    try:
        result = report_agent.generate(
            request=req_text,
            title=title or None,
            organization=organization or None,
            period=period or None,
        )

        _report_store[result.report_id] = result

        # Create download token
        if result.success:
            now   = time.time()
            token = str(uuid.uuid4())
            _download_store[token] = {
                "doc_id":   result.report_id,
                "filename": f"{result.title.replace(' ','_')}.docx",
                "bytes":    result.docx_bytes,
                "expires":  now + DOWNLOAD_TTL_SECONDS,
            }
            result_dict = result.to_summary_dict()
            result_dict["download_token"] = token
        else:
            result_dict = result.to_summary_dict()

        return jsonify(result_dict)

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Report generation error: {str(e)}"}), 500


# ─── List Reports ──────────────────────────────────────
@app.route("/api/reports", methods=["GET"])
def list_reports():
    """List all generated reports in this session."""
    reports = [
        r.to_summary_dict() for r in _report_store.values()
        if hasattr(r, "to_summary_dict")
    ]
    return jsonify({"reports": sorted(reports, key=lambda x: x.get("generated_at",""), reverse=True)})


# ─── Conflicts / Data Quality ──────────────────────────
@app.route("/api/conflicts", methods=["GET"])
def list_conflicts():
    """Run full conflict scan and return all detected contradictions."""
    try:
        conflicts = conflict_detector.detect_all()
        from validator import ValidationReport
        report = ValidationReport(conflicts)
        return jsonify(report.to_dict())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Knowledge Facts Browser ───────────────────────────
@app.route("/api/knowledge/facts", methods=["GET"])
def browse_facts():
    """Browse extracted structured facts with optional filters."""
    organization = request.args.get("organization")
    metric       = request.args.get("metric")
    period       = request.args.get("period")
    limit        = int(request.args.get("limit", 100))

    facts = knowledge_base.query_facts(
        organization=organization,
        metric=metric,
        period=period,
        min_confidence=0.5,
        limit=limit,
    )
    summary = knowledge_base.facts_summary()
    return jsonify({"facts": facts, "summary": summary})


# ─── Analytics: Word Cloud ─────────────────────────────
@app.route("/api/analytics/wordcloud", methods=["POST"])
def wordcloud():
    """Generate word cloud frequency data from the document corpus."""
    data    = request.get_json(silent=True) or {}
    doc_ids = data.get("doc_ids")  # None = use all docs
    top_n   = int(data.get("top_n", 80))
    try:
        result = analytics_engine.generate_word_cloud_data(
            knowledge_base, doc_ids=doc_ids, top_n=top_n
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Analytics: Topics ────────────────────────────────
@app.route("/api/analytics/topics", methods=["POST"])
def topics():
    """Identify dominant topics across the indexed document corpus."""
    data    = request.get_json(silent=True) or {}
    doc_ids = data.get("doc_ids")
    try:
        result = analytics_engine.identify_topics(knowledge_base, doc_ids=doc_ids)
        return jsonify({"topics": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Analytics: Historical Trend ──────────────────────
@app.route("/api/analytics/trend", methods=["POST"])
def trend():
    """
    Extract time-series trend for a metric.
    Body: { "metric": str, "organization": str, "year_start": int, "year_end": int }
    """
    data         = request.get_json(silent=True) or {}
    metric       = data.get("metric", "coal_production")
    organization = data.get("organization")
    year_start   = int(data.get("year_start", 2015))
    year_end     = int(data.get("year_end", 2025))
    try:
        result = analytics_engine.extract_trend(
            knowledge_base, metric=metric,
            organization=organization,
            year_start=year_start, year_end=year_end,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Analytics: Subsidiary Comparison ─────────────────
@app.route("/api/analytics/compare", methods=["POST"])
def compare():
    """
    Compare a metric across subsidiaries for a given period.
    Body: { "metric": str, "period": str, "organizations": [str] }
    """
    data          = request.get_json(silent=True) or {}
    metric        = data.get("metric", "coal_production")
    period        = data.get("period", "")
    organizations = data.get("organizations")
    try:
        result = analytics_engine.compare_organizations(
            knowledge_base, metric=metric,
            period=period, organizations=organizations,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Knowledge Base Stats ──────────────────────────────
@app.route("/api/kb/stats", methods=["GET"])
def kb_stats():
    """Return knowledge base statistics."""
    return jsonify(knowledge_base.stats())


# ─── Run ──────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    ollama_status = "RUNNING" if gateway.is_available() else "NOT REACHABLE"
    kb = knowledge_base.stats()
    print(f"""
+---------------------------------------------------------------+
|  CMPDI/CIL Sovereign Document Intelligence Platform           |
|  Powered by ARIA AI Core (Local, On-Premise)                  |
+---------------------------------------------------------------+
|  URL:      http://localhost:{port}                               |
|  Ollama:   {ollama_status:<52}|
|  Model:    {DEFAULT_MODEL:<52}|
+---------------------------------------------------------------+
|  Knowledge Base:                                              |
|    Documents: {kb['documents']:<49}|
|    Facts:     {kb['facts']:<49}|
|    Vectors:   {kb['vectors']:<49}|
+---------------------------------------------------------------+
|  CMPDI Endpoints:                                             |
|    POST /api/ingest              -> ingest document           |
|    GET  /api/documents           -> list indexed documents    |
|    DELETE /api/documents/<id>    -> remove document           |
|    POST /api/query               -> hybrid RAG query + evidence|
|    POST /api/parliamentary       -> parliamentary query agent  |
|    POST /api/report              -> generate DOCX report      |
|    GET  /api/reports             -> list generated reports    |
|    GET  /api/conflicts           -> data conflict scan        |
|    GET  /api/knowledge/facts     -> browse extracted facts    |
|    POST /api/analytics/wordcloud -> word cloud data           |
|    POST /api/analytics/topics    -> topic identification      |
|    POST /api/analytics/trend     -> historical trend          |
|    POST /api/analytics/compare   -> subsidiary comparison     |
|    GET  /api/kb/stats            -> knowledge base stats      |
|  ARIA Core (kept):                                            |
|    POST /api/chat  /api/agent  /api/stt  /api/orchestrate     |
+---------------------------------------------------------------+
""")
    if gateway.is_available():
        warm_up()
    else:
        print("[WARNING] Could not reach Ollama. Is it installed and running?")
        print("[WARNING] Install: https://ollama.com/download  |  Then: ollama pull llama3.2")

    app.run(host="0.0.0.0", port=port, debug=True)