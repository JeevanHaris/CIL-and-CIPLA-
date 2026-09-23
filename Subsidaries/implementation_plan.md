# ARIA Multi-Model Architecture — Implementation Plan

Transform ARIA from a single-model Ollama chat wrapper into a multi-model orchestrated AI system with Gateway, Router, Orchestrator, and Code Sandbox.

## Current State

ARIA v2.0 is a Flask app ([server.py](file:///d:/Soverign%20Local%20AI/ollama/server.py)) that:
- Proxies chat to a single Ollama model (default: `llama3.2`)
- Has a rich frontend ([index.html](file:///d:/Soverign%20Local%20AI/ollama/index.html) + [app.js](file:///d:/Soverign%20Local%20AI/ollama/app.js)) with voice, tools, docs, memory
- Has offline STT via faster-whisper
- Has desktop automation via [automation.py](file:///d:/Soverign%20Local%20AI/ollama/automation.py)
- All Ollama calls are direct `ollama.chat()` calls scattered across endpoints

## User Review Required

> [!IMPORTANT]
> **Breaking change to chat endpoint**: The `/api/chat` endpoint will be wired through the Gateway+Router, so model selection becomes automatic (the Router picks the best model per request). The user can still override via the model dropdown, but the default behavior changes from "always use selected model" to "let the Router decide."

> [!IMPORTANT]
> **New `qwen2.5-coder` model required**: The routing table expects `qwen2.5-coder:3b` for code tasks. You'll need to run `ollama pull qwen2.5-coder:3b` before the code routing works. If it's not pulled, the Gateway falls back gracefully to the default model.

> [!WARNING]
> **VRAM management**: With 6GB VRAM, the Gateway uses short `keep_alive` windows (`"5m"`) and unloads models aggressively. Only one model is hot at a time. This means the first request after a model switch has ~5-10s cold-start latency.

## Open Questions

> [!IMPORTANT]
> **Router strategy**: Should we use the **keyword/heuristic router** (fast, zero-cost, no extra inference) or the **classifier router** (asks a small model to classify the task type, more robust but adds ~1-2s latency per request)? The plan below implements the heuristic router first with a flag to switch to classifier mode.

> [!IMPORTANT]
> **Orchestrator mode**: Should the orchestrator's plan-execute-verify loop be enabled **by default for all requests**, or only when the user explicitly triggers it (e.g., via a "Deep Think" button or `/plan` prefix)? The plan below makes it opt-in via a new `/api/orchestrate` endpoint + a UI toggle, keeping the fast single-shot `/api/chat` path unchanged.

## Proposed Changes

### Model Gateway

New module providing a single interface for all Ollama interactions. Every component calls Gateway instead of `ollama.chat()` directly. Tracks loaded models and manages VRAM via `keep_alive`.

#### [NEW] [gateway.py](file:///d:/Soverign%20Local%20AI/ollama/gateway.py)

```python
# Single interface for all model interactions
class ModelGateway:
    def call(model_id, messages, **kwargs) -> str
    def list_available() -> list[str]
    def is_loaded(model_id) -> bool
```

- Wraps `ollama.chat()` with error handling and model-not-found fallback
- `keep_alive` defaults to `"5m"` (configurable) to manage VRAM
- Logs which model was called and response time for debugging
- Falls back to `DEFAULT_MODEL` if requested model isn't pulled

---

### Smart Model Router

Decides which model handles each request based on task type.

#### [NEW] [router.py](file:///d:/Soverign%20Local%20AI/ollama/router.py)

```python
# Heuristic router (default) + optional classifier mode
class ModelRouter:
    def route(task_text, has_image=False) -> str  # returns model ID
    def get_routing_table() -> dict               # for the UI to display
```

Routing table:
| Task Type | Model | Signals |
|-----------|-------|---------|
| Code gen/review | `qwen2.5-coder:3b` | `def `, `function`, `bug`, `error`, `` ``` ``, `write code`, `debug`, `implement` |
| Reasoning/planning | `llama3.2` | `why`, `explain`, `compare`, `reason`, `plan`, `analyze` |
| Vision/images | `llava` | `has_image=True` |
| General/default | `llama3.2` | everything else |

---

### AI Orchestrator

Plan → Execute → Verify → Synthesize loop for complex multi-step tasks.

#### [NEW] [orchestrator.py](file:///d:/Soverign%20Local%20AI/ollama/orchestrator.py)

```python
class Orchestrator:
    def run(user_request) -> OrchestratorResult
    # 1. Plan: ask router-selected model to break into steps
    # 2. Execute: route each step to the best model
    # 3. Verify: if step produces code, run it in sandbox
    # 4. Synthesize: combine all step results into final answer
```

- Each step gets independently routed (code step → qwen, reasoning step → llama)
- Returns structured result with plan, per-step outputs, and synthesis
- Max 10 steps per plan to prevent runaway loops
- Retry-on-failure: if sandbox returns error, re-prompt the model with the error (1 retry)

---

### Code Execution Sandbox

Isolated execution of AI-generated code with timeout and restricted environment.

#### [NEW] [sandbox.py](file:///d:/Soverign%20Local%20AI/ollama/sandbox.py)

```python
class CodeSandbox:
    def run(code: str, language="python", timeout=5) -> SandboxResult
    def extract_code(text: str) -> str | None  # extracts code from markdown blocks
```

- Uses `subprocess.run()` with restricted `env` (PATH only, no network env vars)
- 5-second timeout, kills process on expiry
- Captures stdout + stderr
- Temp files cleaned up in `finally` block
- Returns structured result: `{success, output, error, timed_out}`

---

### Server Integration

Wire the new components into the existing Flask server.

#### [MODIFY] [server.py](file:///d:/Soverign%20Local%20AI/ollama/server.py)

1. **Import and initialize** Gateway, Router, Orchestrator, Sandbox at startup
2. **Refactor `/api/chat`**: Route through Gateway instead of direct `ollama.chat()`. The Router auto-selects the model unless the user has explicitly chosen one
3. **Refactor `/api/tool`**: Route through Gateway
4. **Refactor `/api/doc-query`**: Route through Gateway
5. **New endpoint `/api/orchestrate`**: For multi-step plan-execute workflows
6. **New endpoint `/api/routing-info`**: Returns the Router's decision for transparency (which model was picked and why)
7. **Update `/api/models`**: Include routing info (which task types each model handles)
8. **Update warm-up**: Warm the default model via Gateway

---

### Frontend Updates

Add orchestrator UI and routing transparency to the chat interface.

#### [MODIFY] [app.js](file:///d:/Soverign%20Local%20AI/ollama/app.js)

1. **Add `ORCHESTRATE_URL`** constant pointing to `/api/orchestrate`
2. **Add "Deep Think" toggle** in the chat input area — when enabled, requests go to `/api/orchestrate` instead of `/api/chat`
3. **Show routing badge** on assistant messages (e.g., "via qwen2.5-coder" or "via llama3.2") so users can see multi-model routing in action
4. **Update `callAI()`** to optionally call orchestrator endpoint
5. **Handle orchestrator response format** (show plan steps, per-step model badges, final synthesis)

#### [MODIFY] [index.html](file:///d:/Soverign%20Local%20AI/ollama/index.html)

1. **Add Deep Think toggle button** next to the send button in the chat input area
2. **Update model dropdown** in settings to show routing info

#### [MODIFY] [style.css](file:///d:/Soverign%20Local%20AI/ollama/style.css)

1. **Routing badge styles** (small pill showing model name on each message)
2. **Deep Think toggle** button styles
3. **Orchestrator step visualization** (numbered steps with model badges)

---

### Updated Models List

#### [MODIFY] [server.py](file:///d:/Soverign%20Local%20AI/ollama/server.py) — `AVAILABLE_MODELS`

```python
AVAILABLE_MODELS = [
    {"id": "llama3.2",          "name": "Llama 3.2 3B",      "description": "Reasoning, planning, general — default", "tasks": ["reasoning", "general"]},
    {"id": "qwen2.5-coder:3b",  "name": "Qwen 2.5 Coder 3B", "description": "Code generation and review specialist",  "tasks": ["code"]},
    {"id": "phi3",              "name": "Phi-3 Mini",         "description": "Fast fallback for simple queries",       "tasks": ["general"]},
    {"id": "llava",             "name": "LLaVA Vision",       "description": "Image understanding and OCR",            "tasks": ["vision"]},
]
```

## Verification Plan

### Automated Tests

```bash
# 1. Test Gateway can reach Ollama and call a model
python -c "from gateway import ModelGateway; g = ModelGateway(); print(g.call('llama3.2', [{'role':'user','content':'hello'}]))"

# 2. Test Router classifies correctly
python -c "from router import ModelRouter; r = ModelRouter(); assert r.route('write a python function') == 'qwen2.5-coder:3b'; assert r.route('explain quantum physics') == 'llama3.2'; print('Router OK')"

# 3. Test Sandbox executes safely
python -c "from sandbox import CodeSandbox; s = CodeSandbox(); print(s.run('print(2+2)'))"

# 4. Test Sandbox timeout
python -c "from sandbox import CodeSandbox; s = CodeSandbox(); print(s.run('import time; time.sleep(10)'))"

# 5. Start server and test /api/chat with routing
python server.py
# In another terminal:
curl -X POST http://localhost:5000/api/chat -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"write a python fibonacci function"}]}'
# Should return response with "routed_to": "qwen2.5-coder:3b"
```

### Manual Verification

- Open `index.html` in browser, send a code question → verify routing badge shows "qwen2.5-coder"
- Send a reasoning question → verify routing badge shows "llama3.2"  
- Enable "Deep Think" toggle, ask a complex question → verify multi-step plan+execute flow
- Check server console logs for Gateway/Router activity
