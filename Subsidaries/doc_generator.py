"""
ARIA v3.0 — Document Generator
────────────────────────────────
Generate real DOCX, PPTX, and XLSX files from structured JSON data.
The LLM produces JSON; this module renders the actual binary files.

DOCX: python-docx   → pip install python-docx
PPTX: python-pptx   → pip install python-pptx
XLSX: openpyxl      → pip install openpyxl

PPTX uses a neutral professional template (light background, dark text).
DOCX uses a clean corporate style (Calibri, standard margins).
XLSX uses header formatting with auto-width columns.

JSON schemas are documented per function.
"""

import io


# ══════════════════════════════════════════════════════════════
# DOCX Generator
# ══════════════════════════════════════════════════════════════

def generate_docx(data: dict) -> bytes:
    """
    Generate a professional DOCX document from structured data.

    Schema:
    {
      "title": "Approval Note",
      "subject": "Inspection of Pump P-101",       # optional
      "author":  "ARIA",                             # optional
      "date":    "2026-09-04",                       # optional
      "sections": [
        {
          "heading": "Executive Summary",
          "body":    "The pump inspection revealed...",
          "table":   {                                # optional
            "headers": ["Parameter", "Value", "Limit", "Status"],
            "rows":    [["Vibration", "6.2 mm/s", "4.5 mm/s", "EXCEED"]]
          },
          "bullets": ["Finding 1", "Finding 2"]     # optional
        }
      ],
      "footer": "Confidential — ARIA Generated"     # optional
    }

    Returns: raw bytes of the .docx file.
    """
    try:
        from docx import Document
        from docx.shared import Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError:
        raise RuntimeError(
            "python-docx not installed. Run: pip install python-docx"
        )

    doc = Document()

    # ── Page margins (2 cm all sides) ─────────────────────────
    for section in doc.sections:
        section.top_margin    = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)

    # ── Document title ────────────────────────────────────────
    title = data.get("title", "Document")
    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in h.runs:
        run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)

    # ── Metadata block (subject / author / date) ──────────────
    meta_lines = []
    if data.get("subject"):
        meta_lines.append(f"Subject: {data['subject']}")
    if data.get("author"):
        meta_lines.append(f"Prepared by: {data['author']}")
    if data.get("date"):
        meta_lines.append(f"Date: {data['date']}")

    if meta_lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for line in meta_lines:
            run = p.add_run(line + "\n")
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

        doc.add_paragraph()   # spacer

    # ── Sections ─────────────────────────────────────────────
    for section_data in data.get("sections", []):
        heading = section_data.get("heading", "")
        body    = section_data.get("body", "")
        table   = section_data.get("table")
        bullets = section_data.get("bullets", [])

        if heading:
            doc.add_heading(heading, level=1)

        if body:
            p = doc.add_paragraph(body)
            p.style.font.size = Pt(11)

        if bullets:
            for bullet in bullets:
                doc.add_paragraph(bullet, style="List Bullet")

        if table:
            headers = table.get("headers", [])
            rows    = table.get("rows", [])
            if headers:
                t = doc.add_table(rows=1, cols=len(headers))
                t.style = "Table Grid"
                hdr_cells = t.rows[0].cells
                for i, h_text in enumerate(headers):
                    hdr_cells[i].text = h_text
                    # Bold header
                    for para in hdr_cells[i].paragraphs:
                        for run in para.runs:
                            run.bold = True
                            run.font.size = Pt(10)
                for row_data in rows:
                    row_cells = t.add_row().cells
                    for i, cell_text in enumerate(row_data[:len(headers)]):
                        row_cells[i].text = str(cell_text)
                        for para in row_cells[i].paragraphs:
                            for run in para.runs:
                                run.font.size = Pt(10)
                doc.add_paragraph()  # spacer after table

    # ── Footer text ───────────────────────────────────────────
    footer_text = data.get("footer", "")
    if footer_text:
        footer = doc.sections[0].footer
        fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        fp.text = footer_text
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in fp.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════
# PPTX Generator — Neutral Professional Template
# ══════════════════════════════════════════════════════════════

# Colour palette (neutral, corporate)
_PPTX_TITLE_BG   = "1F3864"   # Deep navy
_PPTX_TITLE_FG   = "FFFFFF"   # White
_PPTX_ACCENT     = "2E75B6"   # Professional blue
_PPTX_SLIDE_BG   = "FFFFFF"   # White slide background
_PPTX_BODY_FG    = "222222"   # Near-black body text
_PPTX_SUBHEADING = "2E75B6"   # Blue subheadings


def generate_pptx(data: dict) -> bytes:
    """
    Generate a professional PPTX presentation from structured data.

    Schema:
    {
      "title":    "Inspection Report Summary",
      "subtitle": "Pump P-101 — September 2026",   # optional
      "author":   "ARIA",                             # optional
      "slides": [
        {
          "title":   "Executive Summary",
          "bullets": ["Finding 1", "Finding 2", "Finding 3"],
          "notes":   "Speaker notes for this slide."  # optional
        },
        {
          "title":   "Measurements Table",
          "table":   {
            "headers": ["Parameter", "Measured", "Limit"],
            "rows":    [["Vibration", "6.2 mm/s", "4.5 mm/s"]]
          }
        }
      ]
    }

    Returns: raw bytes of the .pptx file.
    """
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt, Emu
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
    except ImportError:
        raise RuntimeError(
            "python-pptx not installed. Run: pip install python-pptx"
        )

    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)

    # ── Title slide ───────────────────────────────────────────
    blank_layout = prs.slide_layouts[6]   # completely blank

    def _hex(h: str) -> RGBColor:
        h = h.lstrip("#")
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _add_textbox(slide, left, top, width, height,
                     text, font_size, bold=False,
                     color="222222", align=PP_ALIGN.LEFT):
        from pptx.util import Inches, Pt
        txBox = slide.shapes.add_textbox(
            Inches(left), Inches(top), Inches(width), Inches(height)
        )
        tf = txBox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(font_size)
        run.font.bold = bold
        run.font.color.rgb = _hex(color)
        return txBox

    def _add_filled_rect(slide, left, top, width, height, fill_hex):
        from pptx.util import Inches
        from pptx.util import Emu
        shape = slide.shapes.add_shape(
            1,   # MSO_SHAPE_TYPE.RECTANGLE
            Inches(left), Inches(top), Inches(width), Inches(height)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = _hex(fill_hex)
        shape.line.fill.background()
        return shape

    # Title slide
    ts = prs.slides.add_slide(blank_layout)
    # Navy background bar (top 40%)
    _add_filled_rect(ts, 0, 0, 13.33, 3.5, _PPTX_TITLE_BG)
    _add_textbox(ts, 0.5, 0.8, 12.3, 1.5,
                 data.get("title", "Presentation"),
                 font_size=36, bold=True, color=_PPTX_TITLE_FG,
                 align=PP_ALIGN.CENTER)
    subtitle = data.get("subtitle", "")
    if subtitle:
        _add_textbox(ts, 0.5, 2.4, 12.3, 0.7,
                     subtitle, font_size=18, color=_PPTX_TITLE_FG,
                     align=PP_ALIGN.CENTER)
    author = data.get("author", "")
    if author:
        _add_textbox(ts, 0.5, 4.5, 12.3, 0.5,
                     f"Prepared by: {author}",
                     font_size=12, color="666666", align=PP_ALIGN.CENTER)

    # ── Content slides ────────────────────────────────────────
    for slide_data in data.get("slides", []):
        slide = prs.slides.add_slide(blank_layout)

        # Slide header bar
        _add_filled_rect(slide, 0, 0, 13.33, 1.0, _PPTX_ACCENT)
        slide_title = slide_data.get("title", "")
        _add_textbox(slide, 0.2, 0.1, 12.9, 0.8,
                     slide_title, font_size=22, bold=True,
                     color=_PPTX_TITLE_FG, align=PP_ALIGN.LEFT)

        bullets = slide_data.get("bullets", [])
        table   = slide_data.get("table")

        # Bullets
        if bullets:
            from pptx.util import Inches, Pt
            txBox = slide.shapes.add_textbox(
                Inches(0.4), Inches(1.2), Inches(12.5), Inches(5.8)
            )
            tf = txBox.text_frame
            tf.word_wrap = True
            for j, bullet in enumerate(bullets):
                p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
                p.level = 0
                run = p.add_run()
                run.text = f"  •  {bullet}"
                run.font.size = Pt(18)
                run.font.color.rgb = _hex(_PPTX_BODY_FG)

        # Table
        if table:
            from pptx.util import Inches, Pt
            headers = table.get("headers", [])
            rows    = table.get("rows", [])
            if headers:
                cols = len(headers)
                tbl = slide.shapes.add_table(
                    len(rows) + 1, cols,
                    Inches(0.4), Inches(1.3),
                    Inches(12.5), Inches(0.5 * (len(rows) + 1))
                ).table

                # Header row
                for ci, h_text in enumerate(headers):
                    cell = tbl.cell(0, ci)
                    cell.text = h_text
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = _hex(_PPTX_ACCENT)
                    for para in cell.text_frame.paragraphs:
                        for run in para.runs:
                            run.font.bold  = True
                            run.font.color.rgb = _hex(_PPTX_TITLE_FG)
                            run.font.size  = Pt(12)

                # Data rows
                for ri, row_data in enumerate(rows):
                    for ci, cell_text in enumerate(row_data[:cols]):
                        cell = tbl.cell(ri + 1, ci)
                        cell.text = str(cell_text)
                        if ri % 2 == 0:
                            cell.fill.solid()
                            cell.fill.fore_color.rgb = _hex("F2F2F2")
                        for para in cell.text_frame.paragraphs:
                            for run in para.runs:
                                run.font.size = Pt(11)

        # Speaker notes
        notes = slide_data.get("notes", "")
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════
# XLSX Generator
# ══════════════════════════════════════════════════════════════

def generate_xlsx(data: dict) -> bytes:
    """
    Generate an XLSX workbook from structured data.

    Schema:
    {
      "title":   "Analysis Report",              # optional — goes in sheet tab
      "sheets": [
        {
          "name":    "Inspection Data",
          "headers": ["Parameter", "Value", "Limit", "Status"],
          "rows":    [
            ["Vibration", 6.2, 4.5, "EXCEED"],
            ["Pressure",  2.1, 3.0, "OK"]
          ],
          "summary": "Vibration exceeds limit. Action required."  # optional
        }
      ]
    }

    Returns: raw bytes of the .xlsx file.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError(
            "openpyxl not installed. Run: pip install openpyxl"
        )

    # ── Colour constants ──────────────────────────────────────
    HEADER_FILL  = PatternFill("solid", fgColor="1F3864")
    ALT_FILL     = PatternFill("solid", fgColor="EBF3FB")
    WARN_FILL    = PatternFill("solid", fgColor="FFC7CE")
    OK_FILL      = PatternFill("solid", fgColor="C6EFCE")
    HEADER_FONT  = Font(bold=True, color="FFFFFF", size=11)
    BODY_FONT    = Font(size=11)
    TITLE_FONT   = Font(bold=True, size=14, color="1F3864")
    THIN_BORDER  = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )
    CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
    LEFT   = Alignment(horizontal="left",   vertical="center", wrap_text=True)

    def _status_fill(value):
        """Colour-code cells based on keywords."""
        s = str(value).upper()
        if any(k in s for k in ("EXCEED", "FAIL", "ERROR", "HIGH", "BAD")):
            return WARN_FILL
        if any(k in s for k in ("OK", "PASS", "GOOD", "NORMAL")):
            return OK_FILL
        return None

    wb = openpyxl.Workbook()
    wb.remove(wb.active)   # remove default empty sheet

    for sheet_data in data.get("sheets", []):
        ws = wb.create_sheet(title=sheet_data.get("name", "Sheet")[:31])

        headers = sheet_data.get("headers", [])
        rows    = sheet_data.get("rows", [])
        summary = sheet_data.get("summary", "")

        row_offset = 1

        # Optional title row
        doc_title = data.get("title") or sheet_data.get("name", "")
        if doc_title:
            ws.cell(row=1, column=1, value=doc_title).font = TITLE_FONT
            ws.merge_cells(start_row=1, start_column=1,
                           end_row=1, end_column=max(len(headers), 1))
            row_offset = 2
            ws.row_dimensions[1].height = 22

        # Header row
        if headers:
            for ci, h in enumerate(headers, start=1):
                cell = ws.cell(row=row_offset, column=ci, value=h)
                cell.font       = HEADER_FONT
                cell.fill       = HEADER_FILL
                cell.alignment  = CENTER
                cell.border     = THIN_BORDER
            ws.row_dimensions[row_offset].height = 20
            row_offset += 1

        # Data rows
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row[:len(headers)], start=1):
                cell = ws.cell(row=row_offset + ri, column=ci, value=val)
                cell.font      = BODY_FONT
                cell.alignment = CENTER if ci > 1 else LEFT
                cell.border    = THIN_BORDER
                # Alternating row shading
                sf = _status_fill(val)
                if sf:
                    cell.fill = sf
                elif ri % 2 == 1:
                    cell.fill = ALT_FILL

        # Auto-width columns
        for ci, h in enumerate(headers, start=1):
            col_letter = get_column_letter(ci)
            max_len = len(str(h))
            for row in rows:
                if ci - 1 < len(row):
                    max_len = max(max_len, len(str(row[ci - 1])))
            ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

        # Summary cell (below data)
        if summary:
            summary_row = row_offset + len(rows) + 1
            cell = ws.cell(row=summary_row, column=1, value=summary)
            cell.font = Font(italic=True, size=10, color="555555")
            ws.merge_cells(
                start_row=summary_row, start_column=1,
                end_row=summary_row, end_column=max(len(headers), 1)
            )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─── CLI test ──────────────────────────────────────────────────
if __name__ == "__main__":
    import pathlib, datetime

    # Test DOCX
    docx_data = {
        "title":   "Approval Note",
        "subject": "Inspection of Pump P-101",
        "author":  "ARIA v3.0",
        "date":    datetime.date.today().isoformat(),
        "sections": [
            {
                "heading": "Executive Summary",
                "body":    "This approval note summarises the inspection findings for Pump P-101.",
            },
            {
                "heading": "Findings",
                "table": {
                    "headers": ["Parameter", "Measured", "Limit", "Status"],
                    "rows": [
                        ["Vibration (RMS)", "6.2 mm/s", "4.5 mm/s", "EXCEED"],
                        ["Bearing clearance", "0.28 mm", "0.20 mm", "EXCEED"],
                        ["Pressure drop", "4.3 bar", "5.0 bar", "OK"],
                    ]
                }
            },
            {
                "heading": "Recommendation",
                "body":    "Replace bearings immediately. Schedule maintenance shutdown.",
                "bullets": ["Action 1: Procure replacement bearings",
                            "Action 2: Shut down pump within 72 hours",
                            "Action 3: Perform post-repair vibration test"],
            },
        ],
        "footer": "ARIA v3.0 — Confidential",
    }
    docx_bytes = generate_docx(docx_data)
    pathlib.Path("test_approval_note.docx").write_bytes(docx_bytes)
    print(f"DOCX generated: {len(docx_bytes):,} bytes")

    # Test XLSX
    xlsx_data = {
        "title": "Inspection Analysis",
        "sheets": [{
            "name": "Findings",
            "headers": ["Parameter", "Measured", "Limit", "Status"],
            "rows": [
                ["Vibration", 6.2, 4.5, "EXCEED"],
                ["Bearing clearance", 0.28, 0.20, "EXCEED"],
                ["Pressure drop", 4.3, 5.0, "OK"],
            ],
            "summary": "2 parameters exceed limits. Immediate action required.",
        }]
    }
    xlsx_bytes = generate_xlsx(xlsx_data)
    pathlib.Path("test_analysis.xlsx").write_bytes(xlsx_bytes)
    print(f"XLSX generated: {len(xlsx_bytes):,} bytes")

    # Test PPTX
    pptx_data = {
        "title": "Pump P-101 Inspection Report",
        "subtitle": "September 2026",
        "author": "ARIA v3.0",
        "slides": [
            {
                "title": "Executive Summary",
                "bullets": [
                    "Inspection conducted on 2026-09-04",
                    "2 of 3 parameters exceed limits",
                    "Immediate bearing replacement recommended",
                ],
            },
            {
                "title": "Measurement Data",
                "table": {
                    "headers": ["Parameter", "Measured", "Limit", "Status"],
                    "rows": [
                        ["Vibration",        "6.2 mm/s", "4.5 mm/s", "EXCEED"],
                        ["Bearing clearance","0.28 mm",  "0.20 mm",  "EXCEED"],
                        ["Pressure drop",    "4.3 bar",  "5.0 bar",  "OK"],
                    ]
                }
            },
        ]
    }
    pptx_bytes = generate_pptx(pptx_data)
    pathlib.Path("test_report.pptx").write_bytes(pptx_bytes)
    print(f"PPTX generated: {len(pptx_bytes):,} bytes")
