"""
ARIA v2.0 — Smart Model Router
───────────────────────────────
Decides which Ollama model handles each request based on task signals.
Supports heuristic (keyword) routing and optional classifier mode.
"""

import re


# ─── Routing Table ─────────────────────────────────────────────
# Each entry: (task_type, model_id, signal_keywords, description)
ROUTING_TABLE = [
    # ── CMPDI / Mining domain (highest priority) ───────────────
    {
        "task": "parliamentary_query",
        "model": "llama3.2",
        "signals": [
            "parliamentary", "parliament", "lok sabha", "rajya sabha",
            "ministry", "minister", "starred question", "unstarred",
            "briefing note", "note for", "response to",
        ],
        "description": "Parliamentary and Ministry query responses",
    },
    {
        "task": "report_generation",
        "model": "llama3.2",
        "signals": [
            "prepare report", "generate report", "draft report",
            "compile report", "write report", "create report",
            "status report", "annual report summary",
        ],
        "description": "Automated CMPDI/CIL report generation",
    },
    {
        "task": "document_query",
        "model": "llama3.2",
        "signals": [
            # CMPDI/CIL entities
            "coal", "cil", "cmpdi", "ecl", "bccl", "ccl", "ncl",
            "wcl", "secl", "mcl", "nec", "colliery", "washery",
            # Mining activities
            "production", "dispatch", "exploration", "drilling",
            "seismic", "geological", "resources", "reserves",
            "survey", "borehole", "opencast", "underground",
            # Metrics
            "target", "achievement", "offtake", "lakh", "crore",
            "million tonnes", "line km", "mt ",
            # Time references
            "2020", "2021", "2022", "2023", "2024", "2025",
            "fy", "quarter", "yearly", "annual",
        ],
        "description": "CMPDI/CIL document knowledge queries",
    },
    {
        "task": "analytics",
        "model": "llama3.2",
        "signals": [
            "trend", "compare", "comparison", "growth", "decline",
            "word cloud", "topics", "historical", "year over year",
            "chart", "graph", "visualize", "analytics", "statistics",
        ],
        "description": "Mining data analytics and trend analysis",
    },
    # ── General AI tasks ───────────────────────────────────────
    {
        "task": "code",
        "model": "qwen3:4b",
        "signals": [
            "def ", "class ", "function", "bug", "error", "```",
            "write code", "debug", "implement", "refactor", "syntax",
            "compile", "runtime", "traceback", "exception", "import ",
            "variable", "algorithm", "api", "endpoint", "sql",
            "html", "css", "javascript", "python", "java", "code",
            "script", "program", "fix this", "fix the",
        ],
        "description": "Code generation, review, and debugging",
    },
    {
        "task": "reasoning",
        "model": "llama3.2",
        "signals": [
            "why", "explain", "compare", "reason", "logic", "plan",
            "analyze", "think", "evaluate", "assess", "argument",
            "pros and cons", "difference between", "how does",
            "what causes", "step by step", "break down",
        ],
        "description": "Reasoning, planning, and explanation",
    },
    {
        "task": "vision",
        "model": "llava",
        "signals": [],  # triggered by has_image flag, not keywords
        "description": "Image understanding and visual analysis",
    },
    {
        "task": "general",
        "model": "llama3.2",
        "signals": [],  # default fallback
        "description": "General conversation and queries",
    },
]


class ModelRouter:
    """Routes tasks to the best available model based on content analysis."""

    def __init__(self, default_model="llama3.2"):
        self.default_model = default_model
        self._routing_table = ROUTING_TABLE
        self._route_log = []   # last N routing decisions for debugging

    def route(self, task_text, has_image=False, force_model=None):
        """
        Determine which model should handle this task.

        Args:
            task_text:    The user's message or task description.
            has_image:    True if the request includes an image attachment.
            force_model:  If set, bypasses routing and uses this model.

        Returns:
            RoutingDecision with model, task_type, confidence, and reason.
        """
        # If user explicitly picked a model, respect that
        if force_model:
            decision = RoutingDecision(
                model=force_model,
                task_type="user_selected",
                confidence=1.0,
                reason=f"User explicitly selected {force_model}",
            )
            self._log_decision(task_text, decision)
            return decision

        # Vision routing — highest priority
        if has_image:
            decision = RoutingDecision(
                model="llava",
                task_type="vision",
                confidence=1.0,
                reason="Request contains an image attachment",
            )
            self._log_decision(task_text, decision)
            return decision

        # Keyword/heuristic routing
        text_lower = task_text.lower()
        best_match = None
        best_score = 0

        for entry in self._routing_table:
            if not entry["signals"]:
                continue

            # Count how many signal keywords appear in the text
            hits = sum(1 for s in entry["signals"] if s in text_lower)
            if hits > best_score:
                best_score = hits
                best_match = entry

        if best_match and best_score > 0:
            # Confidence scales with number of signal hits (capped at 1.0)
            confidence = min(best_score / 3.0, 1.0)
            matched_signals = [s for s in best_match["signals"]
                               if s in text_lower]
            decision = RoutingDecision(
                model=best_match["model"],
                task_type=best_match["task"],
                confidence=confidence,
                reason=f"Matched signals: {', '.join(matched_signals[:5])}",
            )
        else:
            # Default to general model
            decision = RoutingDecision(
                model=self.default_model,
                task_type="general",
                confidence=0.5,
                reason="No specific signals detected, using default model",
            )

        self._log_decision(task_text, decision)
        return decision

    def get_routing_table(self):
        """Return the routing table for display in the UI."""
        return [
            {
                "task": entry["task"],
                "model": entry["model"],
                "description": entry["description"],
            }
            for entry in self._routing_table
        ]

    def get_recent_decisions(self, n=10):
        """Return the last N routing decisions for debugging."""
        return self._route_log[-n:]

    def _log_decision(self, task_text, decision):
        """Keep a rolling log of routing decisions."""
        self._route_log.append({
            "input_preview": task_text[:80],
            "model": decision.model,
            "task_type": decision.task_type,
            "confidence": decision.confidence,
            "reason": decision.reason,
        })
        # Keep only last 50
        if len(self._route_log) > 50:
            self._route_log = self._route_log[-50:]

        print(f"[Router] {decision.task_type} -> {decision.model} "
              f"(conf={decision.confidence:.1f}) -- {decision.reason}")


class RoutingDecision:
    """The result of a routing decision."""

    def __init__(self, model, task_type, confidence=0.5, reason=""):
        self.model = model
        self.task_type = task_type
        self.confidence = confidence
        self.reason = reason

    def to_dict(self):
        return {
            "model": self.model,
            "task_type": self.task_type,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
        }
