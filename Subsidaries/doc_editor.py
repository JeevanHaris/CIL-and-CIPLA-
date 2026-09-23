"""
ARIA v2.0 — Document Editor
────────────────────────────────────────────────────────────
Applies AI-guided edits to uploaded documents and returns
the modified file bytes plus a plain-language summary.

Supported formats:
  CSV  / XLSX / XLS  → pandas (row/column operations)
  DOCX               → python-docx (text find-replace, paragraph delete)
  TXT / MD / RST     → string operations (line / section edits)

Dependencies:
  pip install pandas openpyxl python-docx
"""

import io
import os
import re
import json


# ─── Edit Result ──────────────────────────────────────────────
class EditResult:
    """Return value from apply_edit()."""

    def __init__(self, new_bytes: bytes, new_text: str,
                 summary: str, changes: list[str], success: bool = True, error: str = ""):
        self.new_bytes  = new_bytes   # serialised modified document
        self.new_text   = new_text    # plain-text extraction of modified doc
        self.summary    = summary     # human-readable summary of what changed
        self.changes    = changes     # list of individual change descriptions
        self.success    = success
        self.error      = error

    def to_dict(self):
        return {
            "success":  self.success,
            "summary":  self.summary,
            "changes":  self.changes,
            "error":    self.error,
        }


# ─── Intent Detection ─────────────────────────────────────────
# Heuristic keywords that strongly signal a user wants to edit
_EDIT_SIGNALS = [
    # delete / remove
    "remove", "delete", "drop", "erase", "strip", "exclude", "clear",
    # change / update
    "replace", "rename", "change", "update", "modify", "edit", "correct",
    "fix", "set", "overwrite", "rewrite",
    # add / insert
    "add", "insert", "append", "include",
    # structural
    "sort", "reorder", "filter", "hide", "move",
]

def is_edit_intent(question: str) -> bool:
    """
    Return True if the question looks like a document-edit instruction
    rather than a read-only Q&A query.
    """
    q = question.lower()
    for sig in _EDIT_SIGNALS:
        # require the keyword to appear as a word (not inside another word)
        if re.search(r'\b' + sig + r'\b', q):
            return True
    return False


# ─── LLM Edit-Plan Parser ────────────────────────────────────
def _ask_llm_for_plan(instruction: str, preview: str, llm_fn) -> dict:
    """
    Ask the LLM to produce a JSON edit plan.  Returns a dict with keys
    that the format-specific editors can act on.

    The plan schema (flexible, editors use what they recognise):
    {
      "operation":  "delete_row" | "delete_column" | "replace_text" |
                    "rename_column" | "add_row" | "filter_rows" |
                    "delete_paragraph" | "line_replace" | "other",
      "target":     <string — what to find / match>,
      "value":      <string — replacement value or new content (optional)>,
      "column":     <string — column name for CSV/XLSX operations (optional)>,
      "condition":  <string — e.g. "equals", "contains", "starts_with">,
      "explanation": <human-readable summary of the change>
    }
    """
    prompt = (
        "You are a document-editing assistant. The user wants to modify a document.\n"
        "Based on the instruction and the document preview below, produce a JSON edit plan.\n\n"
        "Instruction: " + instruction + "\n\n"
        "Document preview (first 2000 chars):\n" + preview[:2000] + "\n\n"
        "Respond with ONLY a valid JSON object (no markdown fences) matching this schema:\n"
        "{\n"
        '  "operation": "<one of: delete_row, delete_column, replace_text, rename_column, '
        'add_row, filter_rows, delete_paragraph, line_replace, other>",\n'
        '  "target": "<what to find or match>",\n'
        '  "value": "<replacement or new content, empty string if not applicable>",\n'
        '  "column": "<column name for tabular ops, empty string if not applicable>",\n'
        '  "condition": "<equals|contains|starts_with|regex>",\n'
        '  "explanation": "<one sentence summary of the change>"\n'
        "}"
    )
    raw = llm_fn(prompt)
    # Strip markdown code fences if the model wraps the JSON anyway
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON object from the response
        m = re.search(r'\{[\s\S]+\}', raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {
            "operation": "other",
            "target": "",
            "value": "",
            "column": "",
            "condition": "contains",
            "explanation": "Could not parse edit plan; applying best-effort edit.",
        }


# ─── Main Entry Point ─────────────────────────────────────────
def apply_edit(filename: str, file_bytes: bytes, instruction: str, llm_fn) -> EditResult:
    """
    Apply an AI-guided edit to a document.

    Args:
        filename:    Original filename (used to determine file type).
        file_bytes:  Raw bytes of the current document.
        instruction: Natural-language edit instruction from the user.
        llm_fn:      Callable(prompt: str) -> str   — calls the LLM and returns text.

    Returns:
        EditResult
    """
    ext = os.path.splitext(filename)[1].lower()

    # Get a plain-text preview for the LLM plan
    preview = _bytes_to_text_preview(ext, file_bytes)

    # Ask LLM for structured plan
    try:
        plan = _ask_llm_for_plan(instruction, preview, llm_fn)
    except Exception as e:
        return EditResult(
            new_bytes=file_bytes, new_text=preview,
            summary="Failed to generate edit plan.", changes=[],
            success=False, error=str(e),
        )

    # Dispatch to format-specific editor
    try:
        if ext in (".csv",):
            return _edit_csv(file_bytes, plan, filename)
        elif ext in (".xlsx", ".xls"):
            return _edit_xlsx(file_bytes, plan, filename)
        elif ext == ".docx":
            return _edit_docx(file_bytes, plan, filename)
        elif ext in (".txt", ".md", ".markdown", ".rst"):
            return _edit_txt(file_bytes, plan, filename)
        else:
            return EditResult(
                new_bytes=file_bytes, new_text=preview,
                summary=f"File type '{ext}' is not supported for editing.",
                changes=[], success=False,
                error=f"Unsupported edit format: {ext}",
            )
    except Exception as e:
        return EditResult(
            new_bytes=file_bytes, new_text=preview,
            summary="Edit failed with an unexpected error.", changes=[],
            success=False, error=str(e),
        )


# ─── Format Editors ───────────────────────────────────────────

def _edit_csv(file_bytes: bytes, plan: dict, filename: str) -> EditResult:
    """Edit a CSV file using pandas."""
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas is required for CSV editing. Run: pip install pandas")

    df = pd.read_csv(io.BytesIO(file_bytes))
    original_shape = df.shape
    changes = []
    operation = plan.get("operation", "other")
    target    = plan.get("target", "")
    value     = plan.get("value", "")
    column    = plan.get("column", "")
    condition = plan.get("condition", "contains")

    if operation == "delete_row":
        # Delete rows where column contains / equals target
        col = _resolve_column(df, column, target)
        before = len(df)
        df = _filter_out_rows(df, col, target, condition)
        removed = before - len(df)
        changes.append(f"Removed {removed} row(s) where '{col}' {condition} '{target}'.")

    elif operation == "delete_column":
        col = _resolve_column(df, target or column, "")
        if col and col in df.columns:
            df = df.drop(columns=[col])
            changes.append(f"Deleted column '{col}'.")

    elif operation == "rename_column":
        col = _resolve_column(df, target or column, "")
        if col and col in df.columns and value:
            df = df.rename(columns={col: value})
            changes.append(f"Renamed column '{col}' → '{value}'.")

    elif operation == "replace_text":
        col = _resolve_column(df, column, target)
        if col and col in df.columns:
            old_vals = df[col].astype(str)
            df[col] = old_vals.str.replace(target, value, regex=False)
            changed = (old_vals != df[col].astype(str)).sum()
            changes.append(f"Replaced '{target}' with '{value}' in column '{col}' ({changed} cell(s) updated).")
        else:
            # Apply across all string columns
            for c in df.select_dtypes(include="object").columns:
                df[c] = df[c].astype(str).str.replace(target, value, regex=False)
            changes.append(f"Replaced '{target}' with '{value}' across all text columns.")

    elif operation == "filter_rows":
        col = _resolve_column(df, column, target)
        before = len(df)
        df = _keep_rows(df, col, target, condition)
        changes.append(f"Kept {len(df)} of {before} rows where '{col}' {condition} '{target}'.")

    elif operation == "add_row":
        # value is expected to be a comma-separated list matching column order
        vals = [v.strip() for v in value.split(",")]
        if len(vals) == len(df.columns):
            new_row = pd.DataFrame([dict(zip(df.columns, vals))])
            df = pd.concat([df, new_row], ignore_index=True)
            changes.append(f"Added new row: {value}.")
        else:
            changes.append("Could not add row — value count doesn't match column count.")

    else:
        # Fallback: try a global text replace
        for c in df.select_dtypes(include="object").columns:
            df[c] = df[c].astype(str).str.replace(target, value, regex=False)
        changes.append(f"Applied best-effort text replace: '{target}' → '{value}'.")

    # Serialise back to CSV
    out = io.StringIO()
    df.to_csv(out, index=False)
    new_bytes = out.getvalue().encode("utf-8")
    new_text  = out.getvalue()

    summary = plan.get("explanation") or "; ".join(changes) or "Document edited."
    return EditResult(new_bytes=new_bytes, new_text=new_text, summary=summary, changes=changes)


def _edit_xlsx(file_bytes: bytes, plan: dict, filename: str) -> EditResult:
    """Edit an Excel file using pandas + openpyxl."""
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas is required for XLSX editing. Run: pip install pandas openpyxl")

    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
    # Re-use CSV logic (same DataFrame operations)
    # Trick: write df to a temp CSV string, parse as CSV, delegate, then re-export as XLSX
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    csv_result = _edit_csv(csv_bytes, plan, filename.replace(".xlsx", ".csv").replace(".xls", ".csv"))

    # Re-read the edited CSV back into pandas and write as XLSX
    edited_df = pd.read_csv(io.StringIO(csv_result.new_text))
    out = io.BytesIO()
    edited_df.to_excel(out, index=False, engine="openpyxl")
    new_bytes = out.getvalue()

    return EditResult(
        new_bytes=new_bytes,
        new_text=csv_result.new_text,
        summary=csv_result.summary,
        changes=csv_result.changes,
        success=csv_result.success,
        error=csv_result.error,
    )


def _edit_docx(file_bytes: bytes, plan: dict, filename: str) -> EditResult:
    """Edit a DOCX file using python-docx."""
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("python-docx is required for DOCX editing. Run: pip install python-docx")

    doc = Document(io.BytesIO(file_bytes))
    operation = plan.get("operation", "other")
    target    = plan.get("target", "")
    value     = plan.get("value", "")
    changes   = []

    if operation in ("delete_paragraph", "delete_row"):
        # Remove paragraphs whose text contains 'target'
        removed = 0
        for para in doc.paragraphs:
            if target.lower() in para.text.lower():
                # Clear the paragraph's runs
                for run in para.runs:
                    run.text = ""
                removed += 1
        changes.append(f"Deleted {removed} paragraph(s) containing '{target}'.")

    elif operation in ("replace_text", "other"):
        # Find-replace across all paragraphs and table cells
        replaced = 0
        for para in doc.paragraphs:
            if target in para.text:
                for run in para.runs:
                    if target in run.text:
                        run.text = run.text.replace(target, value)
                        replaced += 1
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            if target in run.text:
                                run.text = run.text.replace(target, value)
                                replaced += 1
        changes.append(f"Replaced '{target}' with '{value}' ({replaced} run(s) updated).")

    else:
        # Generic replace
        for para in doc.paragraphs:
            for run in para.runs:
                if target in run.text:
                    run.text = run.text.replace(target, value)
        changes.append(f"Applied text replacement: '{target}' → '{value}'.")

    out = io.BytesIO()
    doc.save(out)
    new_bytes = out.getvalue()
    new_text  = "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    summary = plan.get("explanation") or "; ".join(changes) or "Document edited."
    return EditResult(new_bytes=new_bytes, new_text=new_text, summary=summary, changes=changes)


def _edit_txt(file_bytes: bytes, plan: dict, filename: str) -> EditResult:
    """Edit a plain-text / Markdown / RST file with string operations."""
    text      = file_bytes.decode("utf-8", errors="replace")
    operation = plan.get("operation", "other")
    target    = plan.get("target", "")
    value     = plan.get("value", "")
    changes   = []

    if operation in ("delete_row", "delete_paragraph", "line_replace") and not value:
        # Remove lines containing the target
        lines_before = text.splitlines()
        lines_after  = [l for l in lines_before if target.lower() not in l.lower()]
        removed = len(lines_before) - len(lines_after)
        text = "\n".join(lines_after)
        changes.append(f"Removed {removed} line(s) containing '{target}'.")

    elif operation == "line_replace":
        # Replace occurrences on lines that contain target
        lines = text.splitlines()
        new_lines = []
        replaced = 0
        for line in lines:
            if target.lower() in line.lower():
                new_lines.append(line.replace(target, value))
                replaced += 1
            else:
                new_lines.append(line)
        text = "\n".join(new_lines)
        changes.append(f"Replaced '{target}' with '{value}' on {replaced} line(s).")

    else:
        # Generic find-replace
        count = text.count(target)
        text = text.replace(target, value)
        changes.append(f"Replaced all {count} occurrence(s) of '{target}' with '{value}'.")

    new_bytes = text.encode("utf-8")
    summary = plan.get("explanation") or "; ".join(changes) or "Document edited."
    return EditResult(new_bytes=new_bytes, new_text=text, summary=summary, changes=changes)


# ─── Helpers ──────────────────────────────────────────────────

def _bytes_to_text_preview(ext: str, file_bytes: bytes) -> str:
    """Return a plain-text preview of the document for the LLM."""
    try:
        if ext in (".csv", ".txt", ".md", ".markdown", ".rst"):
            return file_bytes.decode("utf-8", errors="replace")[:3000]
        elif ext in (".xlsx", ".xls"):
            import pandas as pd
            df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
            return df.to_csv(index=False)[:3000]
        elif ext == ".docx":
            from docx import Document
            doc = Document(io.BytesIO(file_bytes))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:3000]
    except Exception:
        pass
    return ""


def _resolve_column(df, column_hint: str, value_hint: str) -> str:
    """
    Find the best matching column name from the DataFrame.
    Falls back to the first column that contains the value hint (if any).
    """
    if column_hint and column_hint in df.columns:
        return column_hint
    # Case-insensitive partial match on column name
    if column_hint:
        for col in df.columns:
            if column_hint.lower() in col.lower():
                return col
    # If no column hint, find column that contains the value_hint
    if value_hint:
        for col in df.columns:
            if df[col].astype(str).str.contains(value_hint, case=False, na=False).any():
                return col
    return df.columns[0] if len(df.columns) > 0 else ""


def _filter_out_rows(df, col: str, target: str, condition: str):
    """Remove rows matching the condition (inverse filter)."""
    if not col or col not in df.columns:
        return df
    mask = _build_mask(df, col, target, condition)
    return df[~mask].reset_index(drop=True)


def _keep_rows(df, col: str, target: str, condition: str):
    """Keep only rows matching the condition."""
    if not col or col not in df.columns:
        return df
    mask = _build_mask(df, col, target, condition)
    return df[mask].reset_index(drop=True)


def _build_mask(df, col: str, target: str, condition: str):
    """Build a boolean mask for row matching."""
    s = df[col].astype(str)
    cond = condition.lower() if condition else "contains"
    if cond == "equals":
        return s.str.lower() == target.lower()
    elif cond == "starts_with":
        return s.str.lower().str.startswith(target.lower())
    elif cond == "regex":
        return s.str.contains(target, regex=True, na=False)
    else:  # contains (default)
        return s.str.lower().str.contains(target.lower(), na=False)
