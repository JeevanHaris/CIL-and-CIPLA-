"""
ARIA v2.0 — Code Execution Sandbox
───────────────────────────────────
Isolated execution of AI-generated code with timeout, restricted
environment, and structured result reporting.
"""

import os
import re
import subprocess
import tempfile


class CodeSandbox:
    """Execute code snippets in an isolated subprocess."""

    def __init__(self, timeout=5, max_output_chars=5000):
        self.timeout = timeout
        self.max_output_chars = max_output_chars

    def run(self, code, language="python", timeout=None):
        """
        Execute a code snippet and return the result.

        Args:
            code:      The code string to execute.
            language:  Programming language ('python' supported).
            timeout:   Seconds before killing the process (default: self.timeout).

        Returns:
            SandboxResult with success, output, error, and metadata.
        """
        timeout = timeout or self.timeout

        if language != "python":
            return SandboxResult(
                success=False,
                output="",
                error=f"Language '{language}' not supported. Only Python is available.",
                timed_out=False,
            )

        if not code or not code.strip():
            return SandboxResult(
                success=False,
                output="",
                error="No code provided.",
                timed_out=False,
            )

        # Write code to a temp file
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, mode="w", encoding="utf-8"
            ) as f:
                f.write(code)
                tmp_path = f.name

            # Restricted environment: PATH only, no network env vars,
            # no API keys, no home directory leaks
            safe_env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
            }

            result = subprocess.run(
                ["python", tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=safe_env,
                cwd=tempfile.gettempdir(),  # run in temp dir, not project dir
            )

            stdout = result.stdout[:self.max_output_chars] if result.stdout else ""
            stderr = result.stderr[:self.max_output_chars] if result.stderr else ""

            return SandboxResult(
                success=(result.returncode == 0),
                output=stdout,
                error=stderr if result.returncode != 0 else "",
                timed_out=False,
                return_code=result.returncode,
            )

        except subprocess.TimeoutExpired:
            print(f"[Sandbox] Execution timed out after {timeout}s")
            return SandboxResult(
                success=False,
                output="",
                error=f"Execution timed out after {timeout} seconds.",
                timed_out=True,
            )

        except Exception as e:
            print(f"[Sandbox] Execution error: {e}")
            return SandboxResult(
                success=False,
                output="",
                error=f"Sandbox error: {str(e)}",
                timed_out=False,
            )

        finally:
            # Always clean up the temp file
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def extract_code(text):
        """
        Extract the first Python code block from markdown-formatted text.

        Looks for ```python ... ``` blocks, then bare ``` ... ``` blocks.
        Returns the code string, or None if no code block found.
        """
        # Try ```python blocks first
        match = re.search(
            r'```(?:python|py)\s*\n(.*?)```',
            text, re.DOTALL | re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()

        # Try bare ``` blocks
        match = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
        if match:
            code = match.group(1).strip()
            # Heuristic: does it look like Python?
            if any(kw in code for kw in [
                "def ", "import ", "print(", "class ", "for ", "if ",
                "while ", "return ", "from ", "with ",
            ]):
                return code

        return None

    @staticmethod
    def looks_like_code(text):
        """
        Quick heuristic: does this text contain a code block?
        Used by the Orchestrator to decide whether to sandbox-verify a step.
        """
        return bool(re.search(r'```(?:python|py)?\s*\n', text, re.IGNORECASE))


class SandboxResult:
    """Structured result from a sandbox execution."""

    def __init__(self, success, output, error="", timed_out=False,
                 return_code=None):
        self.success = success
        self.output = output
        self.error = error
        self.timed_out = timed_out
        self.return_code = return_code

    def to_dict(self):
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "timed_out": self.timed_out,
        }

    def __str__(self):
        if self.success:
            return self.output or "(no output)"
        if self.timed_out:
            return "⏱ Execution timed out"
        return f"Error: {self.error}"
