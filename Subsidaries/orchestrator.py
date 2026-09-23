"""
ARIA v3.0 — AI Orchestrator
────────────────────────────
Full agentic loop: Plan → Execute → Observe → Verify → Replan.

Key upgrades over v2.0:
  - ToolRegistry integration: steps can call named tools directly
  - Tool-call parsing: detects tool_name(kwarg=val) syntax in plan steps
  - Verify+Replan loop: fails gracefully, re-plans on missing/bad output
  - AgentState threading: shared state across all steps
  - JSON extraction: pulls structured data from LLM output for doc generation

The original `/api/orchestrate` endpoint (plan-execute-synthesize) is
unchanged. The new `/api/agent` endpoint uses this module's AgentOrchestrator.
"""

import re
import json
import time


# ─── Original Orchestrator (v2.0 — unchanged) ─────────────────

class Orchestrator:
    """Multi-step task orchestrator with plan-execute-verify loop (v2.0 API)."""

    MAX_STEPS   = 10
    MAX_RETRIES = 1

    def __init__(self, gateway, router, sandbox):
        self.gateway = gateway
        self.router  = router
        self.sandbox = sandbox

    def run(self, user_request, system_prompt=None):
        """
        Execute a multi-step orchestrated workflow.
        Returns OrchestratorResult (v2.0 compatible).
        """
        t0     = time.time()
        result = OrchestratorResult(original_request=user_request)

        # ── PLAN ─────────────────────────────────────────────
        plan_decision = self.router.route("plan " + user_request)
        plan_prompt = (
            "Break this task into ordered steps. "
            "Output ONLY a numbered list (1. 2. 3. etc). "
            "Each step should be a single, actionable instruction. "
            "Maximum 7 steps. Be concise.\n\n"
            f"Task: {user_request}"
        )

        try:
            plan_response = self.gateway.call(
                plan_decision.model,
                [{"role": "user", "content": plan_prompt}],
            )
            result.plan_raw   = plan_response.content
            result.plan_model = plan_decision.model
            steps = self._parse_steps(plan_response.content)
        except Exception as e:
            result.error      = f"Planning failed: {e}"
            result.total_time = time.time() - t0
            return result

        if not steps:
            steps = [user_request]
        steps         = steps[:self.MAX_STEPS]
        result.planned_steps = steps

        # ── EXECUTE & VERIFY ──────────────────────────────────
        for i, step_text in enumerate(steps):
            step_result = StepResult(index=i + 1, instruction=step_text)
            decision    = self.router.route(step_text)
            step_result.model         = decision.model
            step_result.task_type     = decision.task_type
            step_result.routing_reason = decision.reason

            context_messages = self._build_step_context(
                user_request, steps, result.step_results,
                step_text, system_prompt,
            )

            try:
                response = self.gateway.call(decision.model, context_messages)
                step_result.output  = response.content
                step_result.latency = response.latency

                if self.sandbox.looks_like_code(response.content):
                    code = self.sandbox.extract_code(response.content)
                    if code:
                        sandbox_result    = self.sandbox.run(code)
                        step_result.code_output = sandbox_result

                        if not sandbox_result.success and self.MAX_RETRIES > 0:
                            retry_response = self._retry_with_error(
                                decision.model, context_messages,
                                response.content, sandbox_result,
                            )
                            if retry_response:
                                step_result.output  = retry_response.content
                                step_result.retried = True
                                retry_code = self.sandbox.extract_code(
                                    retry_response.content
                                )
                                if retry_code:
                                    step_result.code_output = self.sandbox.run(
                                        retry_code
                                    )
            except Exception as e:
                step_result.error = str(e)

            result.step_results.append(step_result)

        # ── SYNTHESIZE ────────────────────────────────────────
        try:
            result.synthesis = self._synthesize(user_request, result.step_results)
        except Exception:
            result.synthesis = "\n\n".join(
                f"**Step {s.index}:** {s.output}"
                for s in result.step_results if s.output
            )

        result.total_time = time.time() - t0
        return result

    def _parse_steps(self, plan_text):
        steps = re.findall(r'^\s*\d+[\.\)]\s*(.+?)$', plan_text, re.MULTILINE)
        return [s.strip() for s in steps if len(s.strip()) > 5]

    def _build_step_context(self, original_request, all_steps,
                            completed_steps, current_step, system_prompt):
        messages = []
        sys_content = (
            system_prompt or
            "You are ARIA, an AI assistant executing a multi-step plan."
        )
        sys_content += (
            f"\n\nOriginal user request: {original_request}\n"
            f"You are now working on step: {current_step}\n"
        )
        if completed_steps:
            prior = completed_steps[-3:]
            sys_content += "\nPrevious step results:\n"
            for s in prior:
                sys_content += f"- Step {s.index}: {s.output[:300]}\n"

        messages.append({"role": "system", "content": sys_content})
        messages.append({"role": "user",   "content": current_step})
        return messages

    def _retry_with_error(self, model, original_messages, original_output,
                          sandbox_result):
        retry_messages = original_messages + [
            {"role": "assistant", "content": original_output},
            {
                "role": "user",
                "content": (
                    f"The code produced an error:\n"
                    f"```\n{sandbox_result.error or sandbox_result.output}\n```\n"
                    f"Please fix the code and try again. "
                    f"Output the corrected code in a ```python block."
                ),
            },
        ]
        try:
            return self.gateway.call(model, retry_messages)
        except Exception:
            return None

    def _synthesize(self, original_request, step_results):
        if len(step_results) == 1 and step_results[0].output:
            return step_results[0].output

        steps_summary = ""
        for s in step_results:
            output = s.output or s.error or "(no output)"
            if s.code_output and s.code_output.success:
                output += f"\n\nCode execution result:\n{s.code_output.output}"
            steps_summary += f"\n### Step {s.index}: {s.instruction}\n{output}\n"

        synth_prompt = (
            f"You completed a multi-step task. The original request was:\n"
            f"\"{original_request}\"\n\n"
            f"Here are the results from each step:\n{steps_summary}\n\n"
            f"Now write a clear, cohesive final response that answers the "
            f"original request. Combine and summarize the step results. "
            f"Do NOT mention 'steps' or 'plan' — write as if answering directly."
        )

        decision  = self.router.route("synthesize " + original_request)
        response  = self.gateway.call(
            decision.model,
            [{"role": "user", "content": synth_prompt}],
        )
        return response.content


# ─── v3.0 Agent Orchestrator ───────────────────────────────────

class AgentOrchestrator:
    """
    Full agentic orchestrator for /api/agent.

    Loop:
        1. PLAN   — planner generates steps with tool call syntax
        2. EXECUTE — for each step: call tool OR call LLM
        3. VERIFY  — check step output meets requirements
        4. REPLAN  — if verification fails, generate corrective steps
        5. SYNTHESIZE — produce final narrative answer
    """

    MAX_STEPS   = 15
    MAX_REPLANS = 3
    MAX_RETRIES = 1

    def __init__(self, gateway, router, sandbox, tool_registry):
        self.gateway       = gateway
        self.router        = router
        self.sandbox       = sandbox
        self.tool_registry = tool_registry

    # ── Public entry point ────────────────────────────────────
    def run(self, goal: str, agent_state, system_prompt: str = None):
        """
        Run the full agent loop.

        Args:
            goal:         The user's high-level goal.
            agent_state:  AgentState instance for this run.
            system_prompt: Optional system context.

        Returns:
            The mutated agent_state (also returns it for convenience).
        """
        from agent_state import (STATUS_PLANNING, STATUS_EXECUTING,
                                 STATUS_VERIFYING, STATUS_REPLANNING,
                                 STATUS_DONE, STATUS_FAILED)

        agent_state.status = STATUS_PLANNING
        print(f"[AgentOrchestrator] Starting run {agent_state.run_id[:8]}: {goal[:80]}")

        # ── Step 1: PLAN ──────────────────────────────────────
        plan = self._create_plan(goal, agent_state, system_prompt)
        if plan is None:
            agent_state.status = STATUS_FAILED
            agent_state.error  = "Planning failed."
            return agent_state

        agent_state.plan   = plan
        agent_state.status = STATUS_EXECUTING

        # ── Step 2-4: EXECUTE → VERIFY → REPLAN loop ─────────
        step_queue = list(plan)   # mutable queue
        executed   = 0

        while step_queue and executed < self.MAX_STEPS:
            step_text = step_queue.pop(0)
            step_rec  = agent_state.start_step(step_text)

            t_step = time.time()

            # Detect if this step is a tool call
            tool_call = _parse_tool_call(step_text)

            if tool_call:
                # ── Tool execution path ───────────────────────
                tool_name = tool_call["tool"]
                kwargs    = tool_call["kwargs"]
                step_rec.task_type = "tool:" + tool_name
                step_rec.model     = "tool_registry"

                from agent_state import ToolCall
                tc = ToolCall(
                    call_id=str(time.time_ns()),
                    tool_name=tool_name,
                    kwargs=kwargs,
                )
                t_tool = time.time()
                tool_result = self.tool_registry.execute(
                    tool_name, agent_state=agent_state, **kwargs
                )
                tc.latency = time.time() - t_tool
                tc.result  = tool_result
                tc.success = tool_result.success
                tc.error   = tool_result.error or ""
                step_rec.tool_calls.append(tc)

                if tool_result.success:
                    step_rec.output = tool_result.text_for_agent()
                    # Store raw result in agent state
                    agent_state.tool_outputs[tc.call_id] = tool_result
                else:
                    step_rec.error  = tool_result.error
                    step_rec.output = f"Tool failed: {tool_result.error}"

            else:
                # ── LLM reasoning path ────────────────────────
                decision         = self.router.route(step_text)
                step_rec.model   = decision.model
                step_rec.task_type = decision.task_type

                messages = self._build_context(
                    goal, agent_state, step_text, system_prompt
                )
                try:
                    response       = self.gateway.call(decision.model, messages)
                    step_rec.output = response.content
                    step_rec.latency = response.latency

                    # Sandbox code if present
                    if self.sandbox.looks_like_code(response.content):
                        code = self.sandbox.extract_code(response.content)
                        if code:
                            sb_result = self.sandbox.run(code, timeout=10)
                            if sb_result.success:
                                step_rec.output += (
                                    f"\n\n[Code Output]\n{sb_result.output}"
                                )
                            elif self.MAX_RETRIES > 0:
                                fix = self._retry_code(
                                    decision.model, messages,
                                    response.content, sb_result
                                )
                                if fix:
                                    step_rec.output  = fix.content
                                    step_rec.retried = True

                except Exception as e:
                    step_rec.error  = str(e)
                    step_rec.output = f"[LLM Error] {e}"

            step_rec.latency = time.time() - t_step
            executed += 1

            # ── VERIFY ───────────────────────────────────────
            agent_state.status = STATUS_VERIFYING
            ver = self._verify_step(step_rec, agent_state)

            if not ver.passed and agent_state.can_replan():
                agent_state.status = STATUS_REPLANNING
                agent_state.replan_count += 1
                corrective = self._corrective_steps(
                    goal, step_rec, ver, agent_state
                )
                if corrective:
                    print(f"[AgentOrchestrator] Replanning: adding "
                          f"{len(corrective)} corrective step(s)")
                    step_queue = corrective + step_queue
                agent_state.status = STATUS_EXECUTING

        # ── Step 5: SYNTHESIZE ────────────────────────────────
        synthesis = self._synthesize(goal, agent_state)

        # Store synthesis as the last step output for display
        agent_state.status = STATUS_DONE
        return agent_state, synthesis

    # ── Planning ─────────────────────────────────────────────
    def _create_plan(self, goal: str, agent_state, system_prompt=None) -> list[str]:
        tool_block = self.tool_registry.tool_prompt_block()
        file_list  = self._file_list_str(agent_state)

        plan_prompt = (
            f"You are an expert AI agent planner. Create a step-by-step plan "
            f"to accomplish the following goal:\n\n"
            f"GOAL: {goal}\n\n"
            f"{tool_block}\n\n"
            f"{file_list}"
            f"RULES:\n"
            f"1. Output ONLY a numbered list (1. 2. 3. ...)\n"
            f"2. Use tool_name(param='value') syntax when a tool is appropriate\n"
            f"3. Use plain English for reasoning/analysis steps\n"
            f"4. Maximum {self.MAX_STEPS} steps\n"
            f"5. Be specific — include filenames, expressions, and values\n"
            f"6. End with a generate_docx/pptx/xlsx call if output files are needed\n\n"
            f"Example good steps:\n"
            f"  read_file(filename='inspection_report.pdf')\n"
            f"  search_documents(query='pump vibration SOP')\n"
            f"  calculate(expression='pressure_drop = 12.5 - 8.2')\n"
            f"  Analyze findings and compare with SOP limits\n"
            f"  generate_docx(data={{...}}, filename='Approval_Note.docx')\n"
        )

        decision = self.router.route("plan " + goal)
        try:
            resp  = self.gateway.call(
                decision.model,
                [{"role": "user", "content": plan_prompt}],
            )
            steps = _parse_numbered_steps(resp.content)
            print(f"[AgentOrchestrator] Plan: {len(steps)} step(s)")
            for i, s in enumerate(steps, 1):
                print(f"  {i}. {s[:80]}")
            return steps or [goal]
        except Exception as e:
            print(f"[AgentOrchestrator] Planning error: {e}")
            return None

    # ── Verification ─────────────────────────────────────────
    def _verify_step(self, step_rec, agent_state) -> "VerificationRecord":
        from agent_state import VerificationRecord

        # Rule-based checks (fast, no LLM needed)
        if step_rec.error and not step_rec.output:
            rec = VerificationRecord(
                step_index=step_rec.index,
                passed=False,
                reason=f"Step failed with error: {step_rec.error}",
                action="replan",
            )
            agent_state.verification_log.append(rec)
            return rec

        if not step_rec.output and not step_rec.tool_calls:
            rec = VerificationRecord(
                step_index=step_rec.index,
                passed=False,
                reason="Step produced no output.",
                action="replan",
            )
            agent_state.verification_log.append(rec)
            return rec

        # Tool-specific checks
        for tc in step_rec.tool_calls:
            if not tc.success:
                rec = VerificationRecord(
                    step_index=step_rec.index,
                    passed=False,
                    reason=f"Tool '{tc.tool_name}' failed: {tc.error}",
                    action="replan" if agent_state.can_replan() else "continue",
                )
                agent_state.verification_log.append(rec)
                return rec

        # All checks passed
        rec = VerificationRecord(
            step_index=step_rec.index,
            passed=True,
            reason="Output present and tools succeeded.",
            action="continue",
        )
        agent_state.verification_log.append(rec)
        step_rec.verified = True
        return rec

    # ── Corrective Replanning ─────────────────────────────────
    def _corrective_steps(self, goal: str, failed_step, ver,
                          agent_state) -> list[str]:
        """Generate 1-3 corrective steps to fix a verification failure."""
        context = self._summarise_state(agent_state)
        prompt = (
            f"A step in an agent plan failed verification.\n\n"
            f"GOAL: {goal}\n"
            f"FAILED STEP: {failed_step.instruction}\n"
            f"FAILURE REASON: {ver.reason}\n"
            f"CURRENT STATE:\n{context}\n\n"
            f"Generate 1-3 corrective steps to fix this failure "
            f"and continue toward the goal.\n"
            f"Output ONLY a numbered list. Use tool syntax where appropriate.\n"
            f"{self.tool_registry.tool_prompt_block()}"
        )
        decision = self.router.route("replan " + goal)
        try:
            resp  = self.gateway.call(
                decision.model,
                [{"role": "user", "content": prompt}],
            )
            return _parse_numbered_steps(resp.content)[:3]
        except Exception:
            return []

    # ── Synthesis ─────────────────────────────────────────────
    def _synthesize(self, goal: str, agent_state) -> str:
        steps_summary = ""
        for s in agent_state.step_results:
            out = s.output or s.error or "(no output)"
            steps_summary += f"\n### Step {s.index}: {s.instruction}\n{out[:500]}\n"

        gen_files = list(agent_state.generated_files.keys())
        file_note = (
            f"\n\nGenerated files: {', '.join(gen_files)}" if gen_files else ""
        )

        synth_prompt = (
            f"You are ARIA, an AI agent. You just completed a task.\n"
            f"GOAL: {goal}\n\n"
            f"STEP RESULTS:\n{steps_summary}{file_note}\n\n"
            f"Write a concise, professional final summary that:\n"
            f"1. Confirms what was accomplished\n"
            f"2. Lists key findings or results\n"
            f"3. Mentions any generated files\n"
            f"Write in first person as ARIA. Do not say 'Step 1', 'Step 2' etc."
        )
        decision = self.router.route("synthesize " + goal)
        try:
            resp = self.gateway.call(
                decision.model,
                [{"role": "user", "content": synth_prompt}],
            )
            return resp.content
        except Exception:
            return "Task completed. " + "\n".join(
                s.output[:100] for s in agent_state.step_results if s.output
            )

    # ── Context builders ─────────────────────────────────────
    def _build_context(self, goal: str, agent_state,
                       current_step: str, system_prompt=None) -> list[dict]:
        sys = system_prompt or "You are ARIA, an expert AI agent."
        sys += f"\n\nGoal: {goal}\nCurrent step: {current_step}"

        # Last 3 step outputs
        prior = agent_state.step_results[-3:]
        if prior:
            sys += "\n\nPrior step outputs:\n"
            for s in prior:
                sys += f"- Step {s.index} ({s.instruction[:60]}): {s.output[:200]}\n"

        # Tool outputs summary
        notes = {k: str(v)[:100] for k, v in agent_state.tool_outputs.items()
                 if k.startswith("note:")}
        if notes:
            sys += "\n\nAgent notes:\n" + "\n".join(
                f"  {k[5:]}: {v}" for k, v in notes.items()
            )

        return [
            {"role": "system", "content": sys},
            {"role": "user",   "content": current_step},
        ]

    def _summarise_state(self, agent_state) -> str:
        lines = []
        lines.append(f"Steps completed: {len(agent_state.step_results)}")
        lines.append(f"Files available: {[d.get('filename') for d in agent_state.uploaded_files.values()]}")
        lines.append(f"Generated files: {list(agent_state.generated_files.keys())}")
        if agent_state.step_results:
            last = agent_state.step_results[-1]
            lines.append(f"Last step ({last.instruction[:60]}): {last.output[:200]}")
        return "\n".join(lines)

    def _file_list_str(self, agent_state) -> str:
        files = agent_state.list_files()
        if not files:
            return ""
        names = [f["filename"] for f in files]
        return f"UPLOADED FILES: {', '.join(names)}\n\n"

    def _retry_code(self, model, original_messages, original_output, sandbox_result):
        retry = original_messages + [
            {"role": "assistant", "content": original_output},
            {
                "role": "user",
                "content": (
                    f"The code produced an error:\n"
                    f"```\n{sandbox_result.error or sandbox_result.output}\n```\n"
                    f"Please fix it and output corrected code in a ```python block."
                ),
            },
        ]
        try:
            return self.gateway.call(model, retry)
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════
# Tool Call Parser
# ══════════════════════════════════════════════════════════════

# Pattern: tool_name(arg1='val1', arg2=val2)
_TOOL_CALL_RE = re.compile(
    r"^(?:\d+[\.\)]?\s*)?"           # optional leading "1. " or "2)"
    r"([a-z_][a-z0-9_]*)"            # tool name
    r"\s*\(([^)]*)\)\s*$",           # (args)
    re.IGNORECASE,
)

# Argument parser: key=value pairs
_ARG_RE = re.compile(
    r"""([a-z_][a-z0-9_]*)\s*=\s*('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|\{[^}]*\}|\[[^\]]*\]|-?\d+(?:\.\d+)?|True|False|None)""",
    re.IGNORECASE,
)


def _parse_tool_call(step_text: str) -> dict | None:
    """
    Try to parse a step as a tool call.

    Returns {"tool": str, "kwargs": dict} or None if not a tool call.
    """
    text = step_text.strip()
    # Skip obviously long natural-language steps
    if len(text) > 300 or "\n" in text:
        return None

    m = _TOOL_CALL_RE.match(text)
    if not m:
        return None

    tool_name = m.group(1).lower()
    args_str  = m.group(2).strip()

    kwargs = {}
    for arg_match in _ARG_RE.finditer(args_str):
        key = arg_match.group(1)
        raw = arg_match.group(2)
        kwargs[key] = _parse_arg_value(raw)

    # If no named args but there's content, try positional for single-arg tools
    if not kwargs and args_str:
        # Treat whole thing as first argument
        kwargs["expression"] = args_str.strip("'\"")

    return {"tool": tool_name, "kwargs": kwargs}


def _parse_arg_value(raw: str):
    """Parse a raw argument string to its Python type."""
    raw = raw.strip()
    if raw in ("True", "true"):
        return True
    if raw in ("False", "false"):
        return False
    if raw == "None":
        return None
    if raw.startswith(("'", '"')):
        return raw[1:-1].replace("\\'", "'").replace('\\"', '"')
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    if raw.startswith("["):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_numbered_steps(text: str) -> list[str]:
    """Extract ordered steps from a numbered list."""
    steps = re.findall(r'^\s*\d+[\.\)]\s*(.+?)$', text, re.MULTILINE)
    return [s.strip() for s in steps if len(s.strip()) > 3]


# ══════════════════════════════════════════════════════════════
# v2.0 Data Classes (unchanged — keep API compatibility)
# ══════════════════════════════════════════════════════════════

class StepResult:
    def __init__(self, index, instruction):
        self.index          = index
        self.instruction    = instruction
        self.model          = None
        self.task_type      = None
        self.routing_reason = None
        self.output         = None
        self.error          = None
        self.latency        = 0.0
        self.code_output    = None
        self.retried        = False

    def to_dict(self):
        d = {
            "step":          self.index,
            "instruction":   self.instruction,
            "model":         self.model,
            "task_type":     self.task_type,
            "routing_reason": self.routing_reason,
            "output":        self.output,
            "error":         self.error,
            "latency":       round(self.latency, 2),
            "retried":       self.retried,
        }
        if self.code_output:
            d["code_execution"] = self.code_output.to_dict()
        return d


class OrchestratorResult:
    def __init__(self, original_request):
        self.original_request = original_request
        self.plan_raw         = None
        self.plan_model       = None
        self.planned_steps    = []
        self.step_results     = []
        self.synthesis        = None
        self.error            = None
        self.total_time       = 0.0

    def to_dict(self):
        return {
            "original_request": self.original_request,
            "plan": {
                "raw":   self.plan_raw,
                "model": self.plan_model,
                "steps": self.planned_steps,
            },
            "steps":      [s.to_dict() for s in self.step_results],
            "synthesis":  self.synthesis,
            "error":      self.error,
            "total_time": round(self.total_time, 2),
        }


# Import VerificationRecord for type hint (avoid circular import)
try:
    from agent_state import VerificationRecord
except ImportError:
    pass
