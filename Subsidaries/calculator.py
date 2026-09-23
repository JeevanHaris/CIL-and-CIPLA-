"""
ARIA v3.0 — Calculator Tool
────────────────────────────
Safe math expression evaluator.
Primary:  sympy.sympify() for symbolic + numeric evaluation.
Fallback: AST-based eval restricted to math operations only.

Supports: arithmetic, algebra, percentages, unit-free engineering math.
Does NOT support: arbitrary Python, imports, function definitions.
"""

import re
import math


# ─── Result ────────────────────────────────────────────────────
class CalculatorResult:
    def __init__(self, expression, result=None, steps=None, error=None):
        self.expression = expression
        self.result     = result          # numeric or symbolic string
        self.steps      = steps or []     # list of intermediate step strings
        self.error      = error

    @property
    def success(self):
        return self.error is None

    def to_dict(self):
        return {
            "expression": self.expression,
            "result":     str(self.result) if self.result is not None else None,
            "steps":      self.steps,
            "error":      self.error,
            "success":    self.success,
        }

    def __str__(self):
        if self.success:
            return f"{self.expression} = {self.result}"
        return f"Error: {self.error}"


# ─── Main Entry ────────────────────────────────────────────────
def calculate(expression: str) -> CalculatorResult:
    """
    Evaluate a math expression safely.

    Accepts expressions like:
        "12.5 - 8.2"
        "pressure_drop = 12.5 - 8.2"
        "area = pi * (0.5**2)"
        "sin(30 * pi / 180)"
        "sqrt(144)"

    Assignment forms ("x = expr") are supported — the RHS is evaluated
    and the variable name is noted in the steps.

    Returns CalculatorResult with result, steps, and error (if any).
    """
    raw = expression.strip()
    if not raw:
        return CalculatorResult(raw, error="Empty expression.")

    # Normalise: strip trailing semicolons, replace ^ with **
    expr = raw.rstrip(";").replace("^", "**")

    # Detect assignment  (e.g. "x = 2 + 3")
    variable = None
    if "=" in expr and not any(op in expr for op in ["==", "!=", "<=", ">="]):
        parts = expr.split("=", 1)
        candidate = parts[0].strip()
        # Simple identifier: letters/digits/underscore, starts with letter
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", candidate):
            variable = candidate
            expr = parts[1].strip()

    steps = []
    if variable:
        steps.append(f"Variable: {variable}")
        steps.append(f"Expression: {expr}")

    # ── Try sympy ──────────────────────────────────────────────
    try:
        import sympy
        # Use sympy's safe parser
        parsed = sympy.sympify(expr, evaluate=True)
        numeric = complex(parsed)
        # Use real part if imaginary is negligible
        if abs(numeric.imag) < 1e-10:
            result_val = round(float(numeric.real), 10)
            # Clean up trailing zeros
            result_str = (
                str(int(result_val))
                if result_val == int(result_val)
                else f"{result_val:.6g}"
            )
        else:
            result_str = str(numeric)

        steps.append(f"Evaluated using sympy: {parsed}")
        if variable:
            steps.append(f"Result: {variable} = {result_str}")
        return CalculatorResult(raw, result=result_str, steps=steps)

    except ImportError:
        pass  # sympy not installed, fall through to AST evaluator
    except Exception as e:
        steps.append(f"sympy failed ({e}), trying AST evaluator…")

    # ── Fallback: restricted AST evaluator ────────────────────
    try:
        result_val = _ast_eval(expr)
        result_str = (
            str(int(result_val))
            if isinstance(result_val, float) and result_val == int(result_val)
            else f"{result_val:.6g}" if isinstance(result_val, float)
            else str(result_val)
        )
        steps.append(f"Evaluated using AST evaluator.")
        if variable:
            steps.append(f"Result: {variable} = {result_str}")
        return CalculatorResult(raw, result=result_str, steps=steps)

    except Exception as e:
        return CalculatorResult(raw, steps=steps, error=f"Could not evaluate: {e}")


# ─── Restricted AST Evaluator ──────────────────────────────────
import ast
import operator as _op

_SAFE_OPS = {
    ast.Add:      _op.add,
    ast.Sub:      _op.sub,
    ast.Mult:     _op.mul,
    ast.Div:      _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod:      _op.mod,
    ast.Pow:      _op.pow,
    ast.USub:     _op.neg,
    ast.UAdd:     _op.pos,
}

_SAFE_FUNCS = {
    "abs":   abs,
    "round": round,
    "sqrt":  math.sqrt,
    "sin":   math.sin,
    "cos":   math.cos,
    "tan":   math.tan,
    "log":   math.log,
    "log10": math.log10,
    "exp":   math.exp,
    "floor": math.floor,
    "ceil":  math.ceil,
    "pi":    math.pi,
    "e":     math.e,
}


def _ast_eval(expr: str):
    """Evaluate a math expression using a whitelist-only AST walker."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Syntax error: {e}") from e
    return _eval_node(tree.body)


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")

    if isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCS:
            return _SAFE_FUNCS[node.id]
        raise ValueError(f"Unknown name: '{node.id}'")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left  = _eval_node(node.left)
        right = _eval_node(node.right)
        return _SAFE_OPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _eval_node(node.operand)
        return _SAFE_OPS[op_type](operand)

    if isinstance(node, ast.Call):
        fn = _eval_node(node.func)
        if not callable(fn):
            raise ValueError("Not a function.")
        args = [_eval_node(a) for a in node.args]
        return fn(*args)

    raise ValueError(f"Unsupported expression type: {type(node).__name__}")


# ─── CLI test ──────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        "2 + 2",
        "12.5 - 8.2",
        "pressure_drop = 12.5 - 8.2",
        "area = pi * (0.5**2)",
        "sqrt(144)",
        "sin(30 * pi / 180)",
        "100 * 1.18",   # GST calculation
        "1500 / 3.14159",
    ]
    for t in tests:
        print(calculate(t))
