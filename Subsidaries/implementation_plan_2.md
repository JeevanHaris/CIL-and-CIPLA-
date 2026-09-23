# ARIA v3.0 — Full Agentic Architecture

Transform ARIA from a multi-model chat system into a **true autonomous agent** with a ToolRegistry, adaptive execution, verification loop, real document generation, and multimodal input routing — exactly as described in the architecture blueprint.

## Background

ARIA already has solid foundations (v2.0):
- `gateway.py` — unified Ollama interface
- `router.py` — heuristic model selector
- `orchestrator.py` — plan → execute → verify loop (basic)
- `sandbox.py` — isolated Python execution
- `doc_editor.py` — AI-guided document edits
- `server.py` — Flask backend with 10+ endpoints
- `automation.py` — desktop automation commands
- Rich frontend (`index.html`, `app.js`, `style.css`)

**What's missing** (the blueprint delta):
1. A formal **ToolRegistry** — the agent currently decides steps in natural language but can't call named, typed tools
2. **Multimodal routing** for scanned PDFs (OCR), handwriting, engineering drawings
3. **Document generators** for DOCX/PPTX/XLSX from structured JSON (not just editing existing files)
4. **Verification + replan loop** — current orchestrator doesn't loop back to fix failures
5. **Agent state management** — no shared state object across steps
6. **Calculator / expression evaluator** tool
7. **RAG / internal document search** (search_documents)
8. A richer **UI** showing: live plan steps, tool call badges, verification pass/fail, downloadable outputs

---

## User Review Required

> [!IMPORTANT]
> **New Python dependencies** will be required. The install command will be printed at server startup. Key additions: `pytesseract` (OCR), `Pillow` (image processing), `python-pptx` (PPTX generation), `openpyxl` (XLSX), `sympy` (calculator). Tesseract OCR binary also needs to be installed on the system (Windows installer provided in the README update).

> [!IMPORTANT]
> **Agent mode is opt-in**: The existing `/api/chat` and `/api/orchestrate` endpoints are **not changed**. The new agent loop runs under a new `/api/agent` endpoint, triggered by a new "Agent Mode" toggle in the UI. This is a safe, non-breaking addition.

> [!WARNING]
> **OCR performance**: `pytesseract` is CPU-only and can be slow on large PDFs (3–10s/page). For scanned PDFs the agent will OCR page by page and cache results in the session.

> [!WARNING]
> **Sandbox security**: The sandbox still uses `subprocess` (not Docker, which requires Docker Desktop). It is restricted by environment scrubbing and timeouts. A note is added to the README about upgrading to Docker for production use.

---

## Open Questions

> [!IMPORTANT]
> **RAG backend**: The `search_documents()` tool can either (a) do simple keyword/BM25 search across uploaded documents, or (b) use a vector store (e.g., ChromaDB). For now the plan uses **BM25 keyword search** (zero extra dependencies) across all documents in `_doc_store`. Upgrade path to ChromaDB is left as a follow-on.

> [!IMPORTANT]
> **PPTX generation**: `python-pptx` generates real PowerPoint files. The agent will use a simple 2-column slide layout. Should the design match ARIA's dark theme, or use a neutral professional template?

---

## Proposed Changes

### 1. ToolRegistry (`tool_registry.py`) — NEW

The central tool dispatch layer. Every tool is a registered function with a typed schema. The agent picks tools by name; the registry validates and executes them.

#### [NEW] `tool_registry.py`

```python
class ToolRegistry:
    tools: dict[str, ToolDefinition]
    
    def register(name, fn, description, parameters_schema)
    def execute(tool_name: str, **kwargs) -> ToolResult
    def list_tools() -> list[dict]   # for the planner prompt
```

**Built-in tools registered at startup:**

| Tool name | Backend | What it does |
|-----------|---------|--------------|
| `read_file` | existing `_extract_text()` | Read PDF/DOCX/TXT → text, tables, metadata |
| `write_file` | new | Write text/bytes to session store |
| `ocr_file` | pytesseract | OCR scanned PDF/image → extracted text |
| `vision_analyze` | llava via gateway | Analyze image → observations, labels |
| `search_documents` | BM25 on `_doc_store` | Search uploaded docs for relevant sections |
| `calculate` | sympy/eval | Evaluate math expressions safely |
| `run_code` | existing sandbox | Execute Python in sandbox, return stdout |
| `generate_docx` | python-docx | Generate a DOCX from structured JSON |
| `generate_pptx` | python-pptx | Generate a PPTX from structured JSON |
| `generate_xlsx` | openpyxl | Generate an XLSX from structured JSON |
| `list_files` | session store | List uploaded files available to the agent |

---

### 2. Agent State (`agent_state.py`) — NEW

A shared state object that lives for the duration of one agent run. The orchestrator passes it through each step so tools can read/write shared data.

#### [NEW] `agent_state.py`

```python
@dataclass
class AgentState:
    run_id: str
    original_goal: str
    plan: list[str]
    step_results: list[StepResult]
    tool_outputs: dict[str, any]   # keyed by tool call id
    uploaded_files: dict[str, dict] # name → {bytes, text, ocr_text}
    generated_files: dict[str, dict] # name → {bytes, mime}
    verification_log: list[dict]
    status: str   # planning | executing | verifying | done | failed
```

---

### 3. Upgraded Orchestrator (`orchestrator.py`) — MODIFY

Replace the current simple loop with a full **Plan → Execute → Observe → Verify → Replan** loop that:
- Uses the ToolRegistry to execute tool calls (not just LLM prompts)
- Detects when steps require tool calls vs. pure LLM reasoning
- Runs the verification step after each tool call
- Replans (up to `MAX_REPLANS=3`) when verification fails

**Key change**: The planner prompt now includes the tool list so the model can output structured tool calls:

```
PLAN (use tool names where appropriate):
1. read_file(filename="inspection_report.pdf")
2. ocr_file(filename="inspection_report.pdf")  ← agent decides OCR needed
3. search_documents(query="pump vibration SOP")
4. calculate("pressure_drop = 12.5 - 8.2")
5. generate_docx(data={...})
```

#### [MODIFY] `orchestrator.py`
- Add `tool_registry` and `agent_state` parameters to `__init__`
- Add `_parse_tool_call(step_text)` → extracts tool name + kwargs if step looks like a tool call
- Add `_verify_step(step_result, agent_state)` → checks outputs meet requirements
- Add replan loop: if verification fails, generate a corrective sub-plan
- Expose `agent_state` in `OrchestratorResult.to_dict()`

---

### 4. Document Generators (`doc_generator.py`) — NEW

Pure-generation (not editing) of DOCX, PPTX, XLSX from structured JSON data. The LLM outputs JSON; this module renders the actual files.

#### [NEW] `doc_generator.py`

```python
def generate_docx(data: dict) -> bytes   # data: {title, sections:[{heading, body, table?}]}
def generate_pptx(data: dict) -> bytes   # data: {title, slides:[{title, bullets, notes?}]}
def generate_xlsx(data: dict) -> bytes   # data: {sheets:[{name, headers, rows, charts?}]}
```

Schema examples match those in the blueprint (approval note, analysis report, etc.)

---

### 5. OCR / Multimodal Tools (`multimodal.py`) — NEW

Handles the multimodal input routing described in sections 5 and 7 of the blueprint.

#### [NEW] `multimodal.py`

```python
def ocr_pdf(file_bytes: bytes) -> str          # pytesseract page-by-page
def ocr_image(file_bytes: bytes) -> str        # single image OCR
def is_scanned_pdf(text: str) -> bool          # heuristic: <50 chars/page = scanned
def analyze_image_with_vision(image_bytes, gateway) -> str  # llava call
```

---

### 6. Search Tool (`search_engine.py`) — NEW

BM25-style keyword search across the in-memory document store.

#### [NEW] `search_engine.py`

```python
def search_documents(query: str, doc_store: dict, top_k=3) -> list[dict]
# Returns: [{doc_id, filename, excerpt, score}]
```

---

### 7. Calculator Tool (`calculator.py`) — NEW

Safe math expression evaluator using `sympy`. No `eval()` of arbitrary code.

#### [NEW] `calculator.py`

```python
def calculate(expression: str) -> CalculatorResult
# Supports: arithmetic, algebra, unit expressions
# Returns: {result, steps, error}
```

---

### 8. Server — New `/api/agent` endpoint (`server.py`) — MODIFY

New endpoint that runs the full agentic loop.

#### [MODIFY] `server.py`

```python
POST /api/agent
{
  "goal": "Analyze this inspection report...",
  "files": ["doc_id_1", "doc_id_2"],   # already-uploaded docs
  "system": "optional system context"
}
→ {
  "run_id": "...",
  "plan": [...],
  "steps": [...],   # each with tool_calls, outputs, verification
  "generated_files": [...],   # download tokens for DOCX/PPTX/XLSX
  "synthesis": "...",
  "status": "done | failed",
  "total_time": 12.3
}
```

Also: register all tools in `ToolRegistry` at startup, wire `doc_store` reference into registry.

---

### 9. Frontend — Agent Mode UI (`app.js`, `index.html`, `style.css`) — MODIFY

Add a visually distinct "Agent Mode" panel that shows the agentic workflow in real-time.

#### [MODIFY] `app.js`
- New `AgentMode` class: sends to `/api/agent`, renders the plan steps panel
- Render each step with: step number, tool name badge, model badge, status icon (✓/✗/⟳)
- Show verification pass/fail per step
- Show generated files as download cards (DOCX/PPTX/XLSX with icons)
- Live streaming: use polling (`/api/agent/status/<run_id>`) to show progress

#### [MODIFY] `index.html`
- "Agent Mode" toggle button (distinct from existing "Deep Think")
- Agent panel: collapsible step-by-step execution view
- Generated files section: download cards with file type icons

#### [MODIFY] `style.css`
- Agent panel styles: step cards, tool badges, verification indicators
- File generation cards: DOCX (blue), PPTX (orange), XLSX (green) themed

---

## File Summary

| File | Action | Purpose |
|------|--------|---------|
| `tool_registry.py` | **NEW** | Central tool dispatch with schema validation |
| `agent_state.py` | **NEW** | Shared state across agent steps |
| `doc_generator.py` | **NEW** | DOCX/PPTX/XLSX generation from JSON |
| `multimodal.py` | **NEW** | OCR (PDF + image), vision analysis routing |
| `search_engine.py` | **NEW** | BM25 keyword search across doc store |
| `calculator.py` | **NEW** | Safe math expression evaluator |
| `orchestrator.py` | **MODIFY** | Add tool call parsing, verify+replan loop |
| `server.py` | **MODIFY** | New `/api/agent` endpoint, tool registry wiring |
| `app.js` | **MODIFY** | Agent mode UI, step rendering, file downloads |
| `index.html` | **MODIFY** | Agent mode toggle, agent panel, generated files section |
| `style.css` | **MODIFY** | Agent panel, tool badges, file cards |
| `README.md` | **MODIFY** | Update install instructions (tesseract, new deps) |

---

## Verification Plan

### Automated Checks (run after implementation)
```bash
# 1. Tool registry smoke test
python -c "from tool_registry import ToolRegistry; r = ToolRegistry(); print(r.list_tools())"

# 2. Calculator tool
python -c "from calculator import calculate; print(calculate('12.5 - 8.2'))"

# 3. Doc generator — DOCX
python -c "from doc_generator import generate_docx; b = generate_docx({'title':'Test','sections':[{'heading':'Intro','body':'Hello'}]}); open('test.docx','wb').write(b)"

# 4. Multimodal — is_scanned_pdf heuristic
python -c "from multimodal import is_scanned_pdf; print(is_scanned_pdf('abc'))"

# 5. Search engine
python -c "from search_engine import search_documents; print(search_documents('pump vibration', {'d1':{'filename':'sop.txt','text':'pump vibration maintenance procedure section 3.2'}}))"

# 6. Full server start
python server.py

# 7. POST /api/agent with a real goal
curl -X POST http://localhost:5000/api/agent -H "Content-Type: application/json" \
  -d '{"goal":"Calculate 12.5 minus 8.2 and write the result in a Word document."}'
```

### Manual Verification
- Upload a PDF in ARIA, enable Agent Mode, ask it to "analyze this document and create an approval note" — verify DOCX download appears
- Upload a scanned PDF — verify the agent automatically calls `ocr_file`
- Ask a calculation question — verify `calculate` tool is used
- Observe the step-by-step UI panel showing each tool call with pass/fail badges
