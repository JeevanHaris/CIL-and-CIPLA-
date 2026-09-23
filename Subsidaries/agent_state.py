"""
ARIA v3.0 — Agent State
────────────────────────
Shared mutable state for one agent run.
The orchestrator creates one AgentState per /api/agent call,
passes it to every tool and step, and returns it in the final result.

This allows:
 - Tools to read files uploaded earlier in the same run
 - Verification steps to see all prior outputs
 - The planner to replan with full context
 - The frontend to poll incremental progress
"""

import uuid
import time
from dataclasses import dataclass, field
from typing import Any


# ─── Status Constants ──────────────────────────────────────────
STATUS_PLANNING   = "planning"
STATUS_EXECUTING  = "executing"
STATUS_VERIFYING  = "verifying"
STATUS_REPLANNING = "replanning"
STATUS_DONE       = "done"
STATUS_FAILED     = "failed"


# ─── Tool Call Record ──────────────────────────────────────────
@dataclass
class ToolCall:
    """Records one tool invocation within a step."""
    call_id:    str
    tool_name:  str
    kwargs:     dict
    result:     Any     = None   # ToolResult or None
    success:    bool    = False
    error:      str     = ""
    latency:    float   = 0.0

    def to_dict(self):
        return {
            "call_id":   self.call_id,
            "tool":      self.tool_name,
            "kwargs":    {k: _safe_serialize(v) for k, v in self.kwargs.items()},
            "success":   self.success,
            "error":     self.error,
            "latency":   round(self.latency, 2),
            "result_preview": (
                str(self.result)[:200]
                if self.result is not None else None
            ),
        }


# ─── Verification Record ───────────────────────────────────────
@dataclass
class VerificationRecord:
    step_index: int
    passed:     bool
    reason:     str
    action:     str = ""    # "continue" | "replan" | "fix"

    def to_dict(self):
        return {
            "step":   self.step_index,
            "passed": self.passed,
            "reason": self.reason,
            "action": self.action,
        }


# ─── Agent State ───────────────────────────────────────────────
class AgentState:
    """
    Shared state for a single agent run.

    Attributes:
        run_id:           Unique ID for this agent run (for polling).
        original_goal:    The user's high-level request.
        plan:             Ordered list of step descriptions.
        current_step:     Index of the step currently being executed.
        status:           One of the STATUS_* constants.
        step_results:     List of StepRecord objects (one per executed step).
        tool_outputs:     {call_id: any} — raw tool outputs, keyed by call_id.
        uploaded_files:   {doc_id: {filename, text, bytes, ocr_text?}} — from /api/upload-doc.
        generated_files:  {name: {bytes, mime, download_token?}} — files the agent created.
        verification_log: List of VerificationRecord objects.
        error:            Terminal error message if status == "failed".
        created_at:       Unix timestamp when this run started.
        replan_count:     How many times the planner has been called.
    """

    MAX_REPLANS = 3

    def __init__(self, goal: str, uploaded_files: dict = None):
        self.run_id           = str(uuid.uuid4())
        self.original_goal    = goal
        self.plan: list[str]  = []
        self.current_step     = 0
        self.status           = STATUS_PLANNING
        self.step_results: list[StepRecord] = []
        self.tool_outputs: dict[str, Any]   = {}
        self.uploaded_files: dict           = uploaded_files or {}
        self.generated_files: dict          = {}
        self.verification_log: list[VerificationRecord] = []
        self.error: str | None              = None
        self.created_at                     = time.time()
        self.replan_count                   = 0

    # ── File helpers ──────────────────────────────────────────
    def get_file(self, identifier: str) -> dict | None:
        """
        Get an uploaded file by doc_id or filename (case-insensitive).
        Returns the file dict or None if not found.
        """
        # Try exact doc_id match first
        if identifier in self.uploaded_files:
            return self.uploaded_files[identifier]
        # Try filename match
        for doc in self.uploaded_files.values():
            if doc.get("filename", "").lower() == identifier.lower():
                return doc
        return None

    def list_files(self) -> list[dict]:
        """Return a summary list of all uploaded files."""
        return [
            {
                "doc_id":    doc_id,
                "filename":  doc.get("filename", "unknown"),
                "char_count": len(doc.get("text") or doc.get("ocr_text") or ""),
                "has_ocr":   "ocr_text" in doc,
            }
            for doc_id, doc in self.uploaded_files.items()
        ]

    def store_generated(self, name: str, file_bytes: bytes, mime: str,
                        download_token: str = None):
        """Store a generated file (DOCX, PPTX, XLSX) in the state."""
        self.generated_files[name] = {
            "bytes":          file_bytes,
            "mime":           mime,
            "download_token": download_token,
            "size":           len(file_bytes),
        }

    def get_text(self, doc_id: str) -> str | None:
        """Get the best available text for a document (OCR > extracted text)."""
        doc = self.uploaded_files.get(doc_id)
        if not doc:
            return None
        return doc.get("ocr_text") or doc.get("text") or None

    # ── Step helpers ──────────────────────────────────────────
    def start_step(self, instruction: str) -> "StepRecord":
        step = StepRecord(
            index=self.current_step + 1,
            instruction=instruction,
        )
        self.step_results.append(step)
        self.current_step += 1
        return step

    def add_verification(self, step_index: int, passed: bool,
                         reason: str, action: str = "continue"):
        rec = VerificationRecord(
            step_index=step_index,
            passed=passed,
            reason=reason,
            action=action,
        )
        self.verification_log.append(rec)
        return rec

    def can_replan(self) -> bool:
        return self.replan_count < self.MAX_REPLANS

    def elapsed(self) -> float:
        return round(time.time() - self.created_at, 2)

    # ── Serialisation ─────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "run_id":          self.run_id,
            "goal":            self.original_goal,
            "status":          self.status,
            "plan":            self.plan,
            "current_step":    self.current_step,
            "steps":           [s.to_dict() for s in self.step_results],
            "verification":    [v.to_dict() for v in self.verification_log],
            "generated_files": [
                {
                    "name":           name,
                    "size":           info["size"],
                    "mime":           info["mime"],
                    "download_token": info.get("download_token"),
                }
                for name, info in self.generated_files.items()
            ],
            "uploaded_files": self.list_files(),
            "error":          self.error,
            "elapsed":        self.elapsed(),
            "replan_count":   self.replan_count,
        }


# ─── Step Record ───────────────────────────────────────────────
@dataclass
class StepRecord:
    """Result of one executed agent step."""
    index:       int
    instruction: str
    model:       str        = ""
    task_type:   str        = ""
    output:      str        = ""
    error:       str        = ""
    tool_calls:  list       = field(default_factory=list)  # list[ToolCall]
    latency:     float      = 0.0
    retried:     bool       = False
    verified:    bool | None = None   # None = not yet verified

    def to_dict(self):
        return {
            "step":        self.index,
            "instruction": self.instruction,
            "model":       self.model,
            "task_type":   self.task_type,
            "output":      self.output,
            "error":       self.error,
            "tool_calls":  [tc.to_dict() for tc in self.tool_calls],
            "latency":     round(self.latency, 2),
            "retried":     self.retried,
            "verified":    self.verified,
        }


# ─── Helper ────────────────────────────────────────────────────
def _safe_serialize(val) -> Any:
    """Convert a kwarg value to something JSON-safe for logging."""
    if isinstance(val, (str, int, float, bool, type(None))):
        return val
    if isinstance(val, bytes):
        return f"<bytes len={len(val)}>"
    return str(val)[:100]
