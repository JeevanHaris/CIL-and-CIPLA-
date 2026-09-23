"""
ARIA v3.0 — Tool Registry
──────────────────────────
Central dispatch layer for all agent tools.
Every tool is registered with a name, description, and parameter schema.
The planner sees the tool list; the orchestrator executes them by name.

Usage:
    registry = ToolRegistry(gateway=gateway, doc_store=_doc_store)
    result   = registry.execute("calculate", expression="12.5 - 8.2")
    tools    = registry.list_tools()   # inject into planner prompt
"""

import time
import uuid
import os
import io


# ─── Tool Result ───────────────────────────────────────────────
class ToolResult:
    def __init__(self, tool_name, output=None, error=None,
                 file_bytes=None, file_mime=None, file_name=None):
        self.tool_name  = tool_name
        self.output     = output        # text output (string)
        self.error      = error         # error message (string | None)
        self.file_bytes = file_bytes    # binary file output (bytes | None)
        self.file_mime  = file_mime     # MIME type of file (str | None)
        self.file_name  = file_name     # suggested filename (str | None)

    @property
    def success(self):
        return self.error is None

    def text_for_agent(self, max_chars=2000) -> str:
        """Return a text representation suitable for injecting into the model context."""
        if self.error:
            return f"[Tool Error] {self.tool_name}: {self.error}"
        if self.file_name:
            return f"[File Generated] {self.file_name} ({self.file_mime})"
        text = str(self.output or "")
        return text[:max_chars] + ("…" if len(text) > max_chars else "")

    def to_dict(self):
        return {
            "tool":    self.tool_name,
            "success": self.success,
            "output":  str(self.output)[:500] if self.output is not None else None,
            "error":   self.error,
            "file":    self.file_name if self.file_name else None,
        }


# ─── Tool Registry ─────────────────────────────────────────────
class ToolRegistry:
    """
    Manages registration and execution of all ARIA agent tools.

    Pass a reference to the shared `_doc_store` dict and the
    running `ModelGateway` so tools can access them.
    """

    def __init__(self, gateway=None, doc_store: dict = None,
                 sandbox=None, download_store: dict = None):
        self.gateway        = gateway
        self.doc_store      = doc_store or {}
        self.sandbox        = sandbox
        self.download_store = download_store or {}
        self._tools: dict[str, dict] = {}
        self._call_log: list[dict]   = []

        # Register all built-in tools
        self._register_builtins()

    # ── Registration ─────────────────────────────────────────
    def register(self, name: str, fn, description: str,
                 parameters: list[dict] = None):
        """
        Register a tool.

        Args:
            name:        Unique tool identifier (snake_case).
            fn:          Callable that implements the tool.
                         fn(**kwargs) → ToolResult
            description: One-sentence description for the planner.
            parameters:  List of {name, type, description, required} dicts.
        """
        self._tools[name] = {
            "name":        name,
            "description": description,
            "parameters":  parameters or [],
            "fn":          fn,
        }

    def execute(self, tool_name: str, agent_state=None, **kwargs) -> ToolResult:
        """
        Execute a registered tool by name.

        Args:
            tool_name:   Name of the tool to call.
            agent_state: Optional AgentState for context sharing.
            **kwargs:    Tool-specific arguments.

        Returns:
            ToolResult
        """
        if tool_name not in self._tools:
            result = ToolResult(
                tool_name=tool_name,
                error=f"Unknown tool '{tool_name}'. "
                      f"Available: {', '.join(self._tools.keys())}",
            )
            self._log(tool_name, kwargs, result)
            return result

        t0 = time.time()
        try:
            # Inject agent_state if the tool accepts it
            tool_fn = self._tools[tool_name]["fn"]
            import inspect
            sig = inspect.signature(tool_fn)
            if "agent_state" in sig.parameters:
                result = tool_fn(agent_state=agent_state, **kwargs)
            else:
                result = tool_fn(**kwargs)
        except Exception as e:
            result = ToolResult(tool_name=tool_name, error=str(e))

        latency = time.time() - t0
        self._log(tool_name, kwargs, result, latency)
        print(f"[ToolRegistry] {tool_name}({list(kwargs.keys())}) "
              f"→ {'OK' if result.success else 'ERROR'} ({latency:.1f}s)")
        return result

    def list_tools(self) -> list[dict]:
        """Return tool list for injecting into the planner prompt."""
        return [
            {
                "name":        t["name"],
                "description": t["description"],
                "parameters":  t["parameters"],
            }
            for t in self._tools.values()
        ]

    def tool_prompt_block(self) -> str:
        """Format tool list as a compact text block for the LLM planner."""
        lines = ["AVAILABLE TOOLS (use tool_name(param=value) syntax in your plan):"]
        for t in self._tools.values():
            params = ", ".join(
                p["name"] + ("?" if not p.get("required") else "")
                for p in t["parameters"]
            )
            lines.append(f"  {t['name']}({params})  — {t['description']}")
        return "\n".join(lines)

    # ── Logging ───────────────────────────────────────────────
    def _log(self, tool_name, kwargs, result, latency=0.0):
        self._call_log.append({
            "tool":    tool_name,
            "kwargs":  {k: str(v)[:50] for k, v in kwargs.items()},
            "success": result.success,
            "latency": round(latency, 2),
        })
        if len(self._call_log) > 200:
            self._call_log = self._call_log[-200:]

    def get_log(self, n=20) -> list[dict]:
        return self._call_log[-n:]

    # ══════════════════════════════════════════════════════════
    # Built-in Tool Implementations
    # ══════════════════════════════════════════════════════════

    def _register_builtins(self):
        # ── read_file ─────────────────────────────────────────
        self.register(
            name="read_file",
            fn=self._tool_read_file,
            description="Read an uploaded file and return its text content. "
                        "Automatically detects if OCR is needed.",
            parameters=[
                {"name": "filename", "type": "str",
                 "description": "Filename or doc_id of the uploaded file.",
                 "required": True},
            ],
        )

        # ── ocr_file ──────────────────────────────────────────
        self.register(
            name="ocr_file",
            fn=self._tool_ocr_file,
            description="Run OCR on a scanned PDF or image file to extract text.",
            parameters=[
                {"name": "filename", "type": "str",
                 "description": "Filename or doc_id to OCR.", "required": True},
            ],
        )

        # ── vision_analyze ────────────────────────────────────
        self.register(
            name="vision_analyze",
            fn=self._tool_vision_analyze,
            description="Analyze an image using the vision model. "
                        "Returns observations, labels, measurements, and defects.",
            parameters=[
                {"name": "filename", "type": "str",
                 "description": "Filename or doc_id of the image.", "required": True},
                {"name": "prompt",   "type": "str",
                 "description": "Optional analysis prompt.", "required": False},
            ],
        )

        # ── search_documents ──────────────────────────────────
        self.register(
            name="search_documents",
            fn=self._tool_search_documents,
            description="Search uploaded documents for sections relevant to a query. "
                        "Returns top matching excerpts.",
            parameters=[
                {"name": "query", "type": "str",
                 "description": "Search query.", "required": True},
                {"name": "top_k", "type": "int",
                 "description": "Max results (default 3).", "required": False},
            ],
        )

        # ── calculate ─────────────────────────────────────────
        self.register(
            name="calculate",
            fn=self._tool_calculate,
            description="Safely evaluate a math expression. "
                        "Supports arithmetic, algebra, sqrt, sin, cos, log, pi. "
                        "Example: calculate(expression='pressure_drop = 12.5 - 8.2')",
            parameters=[
                {"name": "expression", "type": "str",
                 "description": "Math expression to evaluate.", "required": True},
            ],
        )

        # ── run_code ──────────────────────────────────────────
        self.register(
            name="run_code",
            fn=self._tool_run_code,
            description="Execute Python code in an isolated sandbox. "
                        "Returns stdout output.",
            parameters=[
                {"name": "code",    "type": "str",
                 "description": "Python code to execute.", "required": True},
                {"name": "timeout", "type": "int",
                 "description": "Timeout in seconds (default 10).", "required": False},
            ],
        )

        # ── list_files ────────────────────────────────────────
        self.register(
            name="list_files",
            fn=self._tool_list_files,
            description="List all uploaded files available to the agent.",
            parameters=[],
        )

        # ── generate_docx ─────────────────────────────────────
        self.register(
            name="generate_docx",
            fn=self._tool_generate_docx,
            description="Generate a DOCX Word document from structured data. "
                        "Data must be a JSON object with title, sections list.",
            parameters=[
                {"name": "data", "type": "dict",
                 "description": "Document structure JSON.", "required": True},
                {"name": "filename", "type": "str",
                 "description": "Output filename (default: output.docx).", "required": False},
            ],
        )

        # ── generate_pptx ─────────────────────────────────────
        self.register(
            name="generate_pptx",
            fn=self._tool_generate_pptx,
            description="Generate a PPTX PowerPoint presentation from structured data. "
                        "Data must be a JSON object with title, slides list.",
            parameters=[
                {"name": "data", "type": "dict",
                 "description": "Presentation structure JSON.", "required": True},
                {"name": "filename", "type": "str",
                 "description": "Output filename (default: output.pptx).", "required": False},
            ],
        )

        # ── generate_xlsx ─────────────────────────────────────
        self.register(
            name="generate_xlsx",
            fn=self._tool_generate_xlsx,
            description="Generate an XLSX Excel workbook from structured data. "
                        "Data must be a JSON object with sheets list.",
            parameters=[
                {"name": "data", "type": "dict",
                 "description": "Workbook structure JSON.", "required": True},
                {"name": "filename", "type": "str",
                 "description": "Output filename (default: output.xlsx).", "required": False},
            ],
        )

        # ── write_note ────────────────────────────────────────
        self.register(
            name="write_note",
            fn=self._tool_write_note,
            description="Write a text note or summary to the agent's memory "
                        "for use in later steps.",
            parameters=[
                {"name": "key",   "type": "str",
                 "description": "Note key/label.", "required": True},
                {"name": "value", "type": "str",
                 "description": "Content to store.", "required": True},
            ],
        )

    # ══════════════════════════════════════════════════════════
    # Tool Implementations
    # ══════════════════════════════════════════════════════════

    def _tool_read_file(self, filename: str, agent_state=None) -> ToolResult:
        doc = self._find_doc(filename, agent_state)
        if not doc:
            return ToolResult("read_file",
                              error=f"File '{filename}' not found in uploaded files.")
        text = doc.get("ocr_text") or doc.get("text") or ""
        if not text:
            return ToolResult("read_file",
                              error=f"File '{filename}' has no extractable text. "
                                    "Try ocr_file() if it is scanned.")
        preview = text[:3000]
        return ToolResult("read_file",
                          output=f"File: {doc.get('filename')}\n"
                                 f"Length: {len(text):,} chars\n\n{preview}")

    def _tool_ocr_file(self, filename: str, agent_state=None) -> ToolResult:
        from multimodal import smart_read_pdf, ocr_image, classify_file_modality
        doc = self._find_doc(filename, agent_state)
        if not doc:
            return ToolResult("ocr_file",
                              error=f"File '{filename}' not found.")
        modality = classify_file_modality(doc.get("filename", ""))
        file_bytes = doc.get("bytes", b"")
        if not file_bytes:
            return ToolResult("ocr_file", error="No file bytes available.")

        if modality == "pdf":
            result = smart_read_pdf(file_bytes)
        elif modality == "image":
            result = ocr_image(file_bytes)
        else:
            return ToolResult("ocr_file",
                              error=f"OCR not supported for file type '{modality}'.")

        if not result.success:
            return ToolResult("ocr_file", error=result.error)

        # Cache OCR text back in the doc_store
        doc["ocr_text"] = result.text
        if agent_state:
            doc_id = next(
                (k for k, v in agent_state.uploaded_files.items()
                 if v is doc), None
            )
            if doc_id and doc_id in self.doc_store:
                self.doc_store[doc_id]["ocr_text"] = result.text

        return ToolResult("ocr_file",
                          output=f"OCR complete: {result.pages} page(s), "
                                 f"{len(result.text):,} chars extracted.\n\n"
                                 f"{result.text[:2000]}")

    def _tool_vision_analyze(self, filename: str, prompt: str = None,
                             agent_state=None) -> ToolResult:
        if not self.gateway:
            return ToolResult("vision_analyze", error="Gateway not available.")
        from multimodal import analyze_image_with_vision
        doc = self._find_doc(filename, agent_state)
        if not doc:
            return ToolResult("vision_analyze",
                              error=f"File '{filename}' not found.")
        file_bytes = doc.get("bytes", b"")
        if not file_bytes:
            return ToolResult("vision_analyze", error="No image bytes available.")
        analysis = analyze_image_with_vision(file_bytes, self.gateway, prompt)
        return ToolResult("vision_analyze", output=analysis)

    def _tool_search_documents(self, query: str, top_k: int = 3,
                               agent_state=None) -> ToolResult:
        from search_engine import search_documents
        store = self.doc_store.copy()
        # Also include agent-state uploaded files if present
        if agent_state:
            store.update(agent_state.uploaded_files)
        results = search_documents(query, store, top_k=top_k)
        if not results:
            return ToolResult("search_documents",
                              output="No relevant documents found for the query.")
        lines = [f"Found {len(results)} result(s) for '{query}':\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.filename} (score={r.score:.3f})\n{r.excerpt}\n")
        return ToolResult("search_documents", output="\n".join(lines))

    def _tool_calculate(self, expression: str) -> ToolResult:
        from calculator import calculate
        result = calculate(expression)
        if not result.success:
            return ToolResult("calculate", error=result.error)
        return ToolResult("calculate",
                          output=f"{expression} = {result.result}\n"
                                 + ("\nSteps:\n" + "\n".join(result.steps)
                                    if result.steps else ""))

    def _tool_run_code(self, code: str, timeout: int = 10) -> ToolResult:
        if not self.sandbox:
            return ToolResult("run_code", error="Sandbox not available.")
        result = self.sandbox.run(code, timeout=timeout)
        if result.success:
            return ToolResult("run_code", output=result.output or "(no output)")
        return ToolResult("run_code", error=result.error or "Code execution failed.")

    def _tool_list_files(self, agent_state=None) -> ToolResult:
        store = dict(self.doc_store)
        if agent_state:
            store.update(agent_state.uploaded_files)
        if not store:
            return ToolResult("list_files", output="No files uploaded.")
        lines = [f"Uploaded files ({len(store)}):"]
        for doc_id, doc in store.items():
            fname = doc.get("filename", "unknown")
            chars = len(doc.get("ocr_text") or doc.get("text") or "")
            has_ocr = "✓ OCR" if "ocr_text" in doc else ""
            lines.append(f"  • {fname} ({chars:,} chars) {has_ocr} [id={doc_id[:8]}]")
        return ToolResult("list_files", output="\n".join(lines))

    def _tool_generate_docx(self, data: dict, filename: str = "output.docx",
                            agent_state=None) -> ToolResult:
        from doc_generator import generate_docx
        try:
            file_bytes = generate_docx(data)
        except Exception as e:
            return ToolResult("generate_docx", error=str(e))
        token = self._store_generated(filename, file_bytes,
                                      "application/vnd.openxmlformats-officedocument"
                                      ".wordprocessingml.document",
                                      agent_state)
        return ToolResult("generate_docx",
                          output=f"DOCX generated: {filename} ({len(file_bytes):,} bytes)",
                          file_bytes=file_bytes,
                          file_mime="application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document",
                          file_name=filename)

    def _tool_generate_pptx(self, data: dict, filename: str = "output.pptx",
                            agent_state=None) -> ToolResult:
        from doc_generator import generate_pptx
        try:
            file_bytes = generate_pptx(data)
        except Exception as e:
            return ToolResult("generate_pptx", error=str(e))
        token = self._store_generated(filename, file_bytes,
                                      "application/vnd.openxmlformats-officedocument"
                                      ".presentationml.presentation",
                                      agent_state)
        return ToolResult("generate_pptx",
                          output=f"PPTX generated: {filename} ({len(file_bytes):,} bytes)",
                          file_bytes=file_bytes,
                          file_mime="application/vnd.openxmlformats-officedocument"
                                    ".presentationml.presentation",
                          file_name=filename)

    def _tool_generate_xlsx(self, data: dict, filename: str = "output.xlsx",
                            agent_state=None) -> ToolResult:
        from doc_generator import generate_xlsx
        try:
            file_bytes = generate_xlsx(data)
        except Exception as e:
            return ToolResult("generate_xlsx", error=str(e))
        token = self._store_generated(filename, file_bytes,
                                      "application/vnd.openxmlformats-officedocument"
                                      ".spreadsheetml.sheet",
                                      agent_state)
        return ToolResult("generate_xlsx",
                          output=f"XLSX generated: {filename} ({len(file_bytes):,} bytes)",
                          file_bytes=file_bytes,
                          file_mime="application/vnd.openxmlformats-officedocument"
                                    ".spreadsheetml.sheet",
                          file_name=filename)

    def _tool_write_note(self, key: str, value: str,
                         agent_state=None) -> ToolResult:
        if agent_state:
            agent_state.tool_outputs[f"note:{key}"] = value
        return ToolResult("write_note",
                          output=f"Note '{key}' stored ({len(value)} chars).")

    # ── Helpers ───────────────────────────────────────────────
    def _find_doc(self, identifier: str, agent_state=None) -> dict | None:
        """Find a document by doc_id or filename across stores."""
        # Agent state first
        if agent_state:
            doc = agent_state.get_file(identifier)
            if doc:
                return doc
        # Main doc store
        if identifier in self.doc_store:
            return self.doc_store[identifier]
        for doc in self.doc_store.values():
            if doc.get("filename", "").lower() == identifier.lower():
                return doc
        return None

    def _store_generated(self, filename: str, file_bytes: bytes,
                         mime: str, agent_state=None) -> str:
        """Create a download token and store file in agent state + download store."""
        token = str(uuid.uuid4())
        import time as _time
        self.download_store[token] = {
            "filename": filename,
            "bytes":    file_bytes,
            "expires":  _time.time() + 900,
        }
        if agent_state:
            agent_state.store_generated(filename, file_bytes, mime,
                                        download_token=token)
        return token
