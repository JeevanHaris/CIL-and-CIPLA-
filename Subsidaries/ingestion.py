"""
CMPDI/CIL — Document Ingestion Pipeline
────────────────────────────────────────
Accepts uploaded files, classifies them, routes to the appropriate
parser, and returns a NormalizedDocument preserving page-level
structure (headings, paragraphs, tables, figures).

Supported formats:
  PDF  (native text + scanned/OCR fallback)
  DOCX
  XLSX / XLS / CSV
  PNG / JPG / JPEG / TIFF / BMP (image → OCR)

Dependencies:
  pip install pypdf python-docx openpyxl pandas pdfplumber
  (OCR requires: pip install pytesseract Pillow pdf2image)
"""

import os
import io
import uuid
import time
import json
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


# ─── Data Structures ──────────────────────────────────────────────

@dataclass
class CellData:
    """A single cell in a table."""
    row: int
    col: int
    value: str
    is_header: bool = False


@dataclass
class TableData:
    """Structured table extracted from a document page."""
    table_id: str
    page_num: int
    headers: List[str]
    rows: List[List[str]]
    caption: str = ""
    source_region: str = ""      # "left", "right", "full-width"

    def to_text(self) -> str:
        """Render table as plain text for LLM context."""
        lines = []
        if self.caption:
            lines.append(f"Table: {self.caption}")
        if self.headers:
            lines.append(" | ".join(self.headers))
            lines.append("-" * (len(" | ".join(self.headers))))
        for row in self.rows:
            lines.append(" | ".join(str(c) for c in row))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "table_id": self.table_id,
            "page_num": self.page_num,
            "headers": self.headers,
            "rows": self.rows,
            "caption": self.caption,
        }


@dataclass
class PageContent:
    """Content of a single document page, with structure preserved."""
    page_num: int
    headings: List[str] = field(default_factory=list)
    paragraphs: List[str] = field(default_factory=list)
    tables: List[TableData] = field(default_factory=list)
    raw_text: str = ""
    char_count: int = 0

    def full_text(self) -> str:
        """Concatenate all text elements for search indexing."""
        parts = self.headings + self.paragraphs
        for t in self.tables:
            parts.append(t.to_text())
        return "\n\n".join(parts) or self.raw_text

    def to_dict(self) -> dict:
        return {
            "page_num": self.page_num,
            "headings": self.headings,
            "paragraphs": self.paragraphs,
            "tables": [t.to_dict() for t in self.tables],
            "char_count": self.char_count,
        }


@dataclass
class NormalizedDocument:
    """
    Unified document representation after ingestion.
    Preserves page-level structure for evidence tracing.
    """
    doc_id: str
    filename: str
    doc_type: str           # "pdf", "scanned_pdf", "docx", "xlsx", "image", "text"
    upload_time: str
    pages: List[PageContent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""      # full concatenated text (for quick search)
    page_count: int = 0
    char_count: int = 0
    ocr_applied: bool = False
    ingestion_time_s: float = 0.0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.raw_text.strip())

    def get_page(self, page_num: int) -> Optional[PageContent]:
        for p in self.pages:
            if p.page_num == page_num:
                return p
        return None

    def all_tables(self) -> List[TableData]:
        tables = []
        for p in self.pages:
            tables.extend(p.tables)
        return tables

    def to_summary_dict(self) -> dict:
        return {
            "doc_id":       self.doc_id,
            "filename":     self.filename,
            "doc_type":     self.doc_type,
            "upload_time":  self.upload_time,
            "page_count":   self.page_count,
            "char_count":   self.char_count,
            "table_count":  len(self.all_tables()),
            "ocr_applied":  self.ocr_applied,
            "success":      self.success,
            "error":        self.error,
            "metadata":     self.metadata,
            "ingestion_time_s": round(self.ingestion_time_s, 2),
        }


# ─── File Type Classifier ─────────────────────────────────────────

def classify_file(filename: str, file_bytes: bytes) -> str:
    """
    Determine the processing modality for a file.

    Returns one of:
        "pdf"    → digital PDF (native text extraction)
        "docx"   → Word document
        "xlsx"   → Excel spreadsheet
        "csv"    → CSV data file
        "image"  → image file requiring OCR
        "text"   → plain text / markdown
        "unknown"→ unsupported
    """
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    ext_map = {
        "pdf":  "pdf",
        "docx": "docx", "doc": "docx",
        "xlsx": "xlsx", "xls": "xlsx",
        "csv":  "csv",
        "txt":  "text", "md": "text",
        "png":  "image", "jpg": "image", "jpeg": "image",
        "tiff": "image", "tif": "image", "bmp": "image",
        "gif":  "image", "webp": "image",
        "pptx": "pptx", "ppt": "pptx",
    }
    return ext_map.get(ext, "unknown")


# ─── PDF Parser ───────────────────────────────────────────────────

def _parse_pdf(file_bytes: bytes, doc: NormalizedDocument) -> NormalizedDocument:
    """Parse a PDF, extracting text + tables per page. Falls back to OCR if scanned."""
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        total_pages = len(reader.pages)
    except Exception as e:
        doc.error = f"PDF read error: {e}"
        return doc

    # Quick scanned detection: check char density on first 3 pages
    sample_text = ""
    try:
        for i, pg in enumerate(reader.pages[:3]):
            sample_text += (pg.extract_text() or "")
    except Exception:
        pass

    chars_per_page = len(sample_text.strip()) / max(min(total_pages, 3), 1)
    is_scanned = chars_per_page < 50

    if is_scanned:
        doc.ocr_applied = True
        doc.doc_type = "scanned_pdf"
        return _parse_scanned_pdf(file_bytes, doc)

    # Digital PDF — extract text + tables with pdfplumber if available
    pages_out = []
    all_text_parts = []

    if pdfplumber:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for i, plumber_page in enumerate(pdf.pages):
                    page_num = i + 1
                    raw = plumber_page.extract_text() or ""
                    tables_raw = plumber_page.extract_tables() or []

                    tables = []
                    for t_idx, table in enumerate(tables_raw):
                        if not table:
                            continue
                        headers = [str(c or "") for c in (table[0] if table else [])]
                        rows = [[str(c or "") for c in row] for row in table[1:]]
                        td = TableData(
                            table_id=f"{doc.doc_id}_p{page_num}_t{t_idx}",
                            page_num=page_num,
                            headers=headers,
                            rows=rows,
                        )
                        tables.append(td)

                    headings, paragraphs = _split_headings_paragraphs(raw)
                    pc = PageContent(
                        page_num=page_num,
                        headings=headings,
                        paragraphs=paragraphs,
                        tables=tables,
                        raw_text=raw,
                        char_count=len(raw),
                    )
                    pages_out.append(pc)
                    all_text_parts.append(raw)
        except Exception as e:
            print(f"[Ingestion] pdfplumber error: {e}, falling back to pypdf")
            pages_out = []
            all_text_parts = []

    if not pages_out:
        # pypdf fallback
        for i, pg in enumerate(reader.pages):
            page_num = i + 1
            raw = pg.extract_text() or ""
            headings, paragraphs = _split_headings_paragraphs(raw)
            pc = PageContent(
                page_num=page_num,
                headings=headings,
                paragraphs=paragraphs,
                tables=[],
                raw_text=raw,
                char_count=len(raw),
            )
            pages_out.append(pc)
            all_text_parts.append(raw)

    doc.pages = pages_out
    doc.raw_text = "\n\n".join(all_text_parts)
    doc.page_count = len(pages_out)
    doc.char_count = len(doc.raw_text)
    return doc


def _parse_scanned_pdf(file_bytes: bytes, doc: NormalizedDocument) -> NormalizedDocument:
    """OCR a scanned PDF page by page."""
    from multimodal import ocr_pdf
    result = ocr_pdf(file_bytes)
    if not result.success:
        doc.error = result.error
        return doc

    pages_out = []
    for i, page_text in enumerate(result.page_texts):
        page_num = i + 1
        headings, paragraphs = _split_headings_paragraphs(page_text)
        pc = PageContent(
            page_num=page_num,
            headings=headings,
            paragraphs=paragraphs,
            tables=[],
            raw_text=page_text,
            char_count=len(page_text),
        )
        pages_out.append(pc)

    doc.pages = pages_out
    doc.raw_text = result.text
    doc.page_count = len(pages_out)
    doc.char_count = len(doc.raw_text)
    return doc


# ─── DOCX Parser ──────────────────────────────────────────────────

def _parse_docx(file_bytes: bytes, doc: NormalizedDocument) -> NormalizedDocument:
    """Parse a Word document, preserving heading/paragraph structure."""
    try:
        from docx import Document
        from docx.oxml.ns import qn
    except ImportError:
        doc.error = "python-docx not installed. Run: pip install python-docx"
        return doc

    try:
        word_doc = Document(io.BytesIO(file_bytes))
    except Exception as e:
        doc.error = f"DOCX read error: {e}"
        return doc

    headings = []
    paragraphs = []
    all_text_parts = []

    for para in word_doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        all_text_parts.append(text)
        if para.style.name.startswith("Heading"):
            headings.append(text)
        else:
            paragraphs.append(text)

    # Tables
    tables = []
    for t_idx, table in enumerate(word_doc.tables):
        rows_data = []
        for row in table.rows:
            rows_data.append([cell.text.strip() for cell in row.cells])
        if rows_data:
            headers = rows_data[0]
            rows = rows_data[1:]
            td = TableData(
                table_id=f"{doc.doc_id}_t{t_idx}",
                page_num=1,
                headers=headers,
                rows=rows,
            )
            tables.append(td)
            all_text_parts.append(td.to_text())

    pc = PageContent(
        page_num=1,
        headings=headings,
        paragraphs=paragraphs,
        tables=tables,
        raw_text="\n\n".join(all_text_parts),
        char_count=len("\n\n".join(all_text_parts)),
    )
    doc.pages = [pc]
    doc.raw_text = "\n\n".join(all_text_parts)
    doc.page_count = 1
    doc.char_count = len(doc.raw_text)
    return doc


# ─── Excel / CSV Parser ───────────────────────────────────────────

def _parse_xlsx(file_bytes: bytes, filename: str, doc: NormalizedDocument) -> NormalizedDocument:
    """Parse an Excel file, converting each sheet into a TableData."""
    try:
        import pandas as pd
    except ImportError:
        doc.error = "pandas not installed. Run: pip install pandas openpyxl"
        return doc

    tables = []
    all_text_parts = []

    try:
        if filename.lower().endswith(".csv"):
            df_map = {"Sheet1": pd.read_csv(io.BytesIO(file_bytes))}
        else:
            df_map = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, dtype=str)

        for sheet_name, df in df_map.items():
            df = df.fillna("")
            headers = list(df.columns)
            rows = df.values.tolist()
            td = TableData(
                table_id=f"{doc.doc_id}_{sheet_name}",
                page_num=1,
                headers=headers,
                rows=rows,
                caption=sheet_name,
            )
            tables.append(td)
            all_text_parts.append(td.to_text())
    except Exception as e:
        doc.error = f"Excel/CSV parse error: {e}"
        return doc

    pc = PageContent(
        page_num=1,
        headings=[],
        paragraphs=[],
        tables=tables,
        raw_text="\n\n".join(all_text_parts),
        char_count=len("\n\n".join(all_text_parts)),
    )
    doc.pages = [pc]
    doc.raw_text = "\n\n".join(all_text_parts)
    doc.page_count = 1
    doc.char_count = len(doc.raw_text)
    return doc


# ─── Image Parser ─────────────────────────────────────────────────

def _parse_image(file_bytes: bytes, doc: NormalizedDocument) -> NormalizedDocument:
    """OCR an image file."""
    from multimodal import ocr_image
    result = ocr_image(file_bytes)
    if not result.success:
        doc.error = result.error
        return doc

    headings, paragraphs = _split_headings_paragraphs(result.text)
    pc = PageContent(
        page_num=1,
        headings=headings,
        paragraphs=paragraphs,
        tables=[],
        raw_text=result.text,
        char_count=len(result.text),
    )
    doc.pages = [pc]
    doc.raw_text = result.text
    doc.page_count = 1
    doc.char_count = len(doc.raw_text)
    doc.ocr_applied = True
    return doc


# ─── Text Parser ──────────────────────────────────────────────────

def _parse_text(file_bytes: bytes, doc: NormalizedDocument) -> NormalizedDocument:
    """Parse plain text / markdown."""
    try:
        text = file_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        doc.error = f"Text decode error: {e}"
        return doc

    headings, paragraphs = _split_headings_paragraphs(text)
    pc = PageContent(
        page_num=1,
        headings=headings,
        paragraphs=paragraphs,
        tables=[],
        raw_text=text,
        char_count=len(text),
    )
    doc.pages = [pc]
    doc.raw_text = text
    doc.page_count = 1
    doc.char_count = len(doc.raw_text)
    return doc


# ─── Heading / Paragraph Splitter ────────────────────────────────

def _split_headings_paragraphs(text: str):
    """
    Heuristic splitter: short ALL-CAPS lines or lines ending with
    a number pattern are likely headings. Returns (headings, paragraphs).
    """
    import re
    lines = text.split("\n")
    headings = []
    paragraphs = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        words = stripped.split()
        is_short = len(words) <= 10
        is_upper = stripped.isupper() and len(stripped) > 3
        is_numbered = bool(re.match(r"^\d+(\.\d+)*[\s\.]", stripped))
        if is_short and (is_upper or is_numbered):
            headings.append(stripped)
        else:
            paragraphs.append(stripped)

    return headings, paragraphs


# ─── Metadata Extractor ───────────────────────────────────────────

def _infer_metadata(doc: NormalizedDocument) -> dict:
    """
    Infer document metadata from filename and content heuristics.
    Returns dict with: title, organization, year, doc_type_hint.
    """
    import re
    meta = {}
    name = os.path.splitext(doc.filename)[0]

    # Try to extract year
    year_match = re.search(r"(20\d{2})", name)
    if year_match:
        meta["year"] = year_match.group(1)

    # Try to identify subsidiary
    from ontology import SUBSIDIARIES
    name_upper = name.upper()
    for code in SUBSIDIARIES:
        if code in name_upper:
            meta["organization"] = code
            break
    else:
        meta["organization"] = "CIL"

    # Guess document type
    name_lower = name.lower()
    if "annual" in name_lower and "report" in name_lower:
        meta["doc_type_hint"] = "annual_report"
    elif "geological" in name_lower or "geo" in name_lower:
        meta["doc_type_hint"] = "geological_report"
    elif "exploration" in name_lower:
        meta["doc_type_hint"] = "exploration_report"
    elif "parliamentary" in name_lower or "parliament" in name_lower:
        meta["doc_type_hint"] = "parliamentary_question"
    else:
        meta["doc_type_hint"] = "other"

    meta["title"] = name.replace("_", " ").replace("-", " ").title()
    return meta


# ─── Main Ingestion Pipeline ─────────────────────────────────────

class IngestionPipeline:
    """
    Orchestrates file → NormalizedDocument conversion.

    Usage:
        pipeline = IngestionPipeline()
        doc = pipeline.ingest(filename="annual_report_2024.pdf", file_bytes=b"...")
        print(doc.page_count, doc.raw_text[:200])
    """

    def ingest(self, filename: str, file_bytes: bytes,
               doc_id: str = None) -> NormalizedDocument:
        """
        Ingest a file and return a NormalizedDocument.

        Args:
            filename:   Original filename (used for classification + metadata).
            file_bytes: Raw file content as bytes.
            doc_id:     Optional pre-assigned ID. Auto-generated if not provided.

        Returns:
            NormalizedDocument (check .success and .error).
        """
        t0 = time.time()
        doc_id = doc_id or str(uuid.uuid4())
        modality = classify_file(filename, file_bytes)

        doc = NormalizedDocument(
            doc_id=doc_id,
            filename=filename,
            doc_type=modality,
            upload_time=datetime.utcnow().isoformat() + "Z",
        )

        print(f"[Ingestion] {filename} → modality={modality}, size={len(file_bytes)} bytes")

        if modality == "pdf":
            doc = _parse_pdf(file_bytes, doc)
        elif modality == "docx":
            doc = _parse_docx(file_bytes, doc)
        elif modality in ("xlsx", "csv"):
            doc = _parse_xlsx(file_bytes, filename, doc)
        elif modality == "image":
            doc = _parse_image(file_bytes, doc)
        elif modality == "text":
            doc = _parse_text(file_bytes, doc)
        else:
            doc.error = f"Unsupported file type: {modality}"

        # Enrich metadata
        if doc.success:
            doc.metadata = _infer_metadata(doc)

        doc.ingestion_time_s = time.time() - t0
        print(f"[Ingestion] Done: {doc.page_count}p, "
              f"{doc.char_count} chars, OCR={doc.ocr_applied}, "
              f"{doc.ingestion_time_s:.1f}s")
        return doc

    def ingest_text(self, filename: str, text_content: str,
                    doc_id: str = None) -> NormalizedDocument:
        """Convenience: ingest a raw text string directly."""
        return self.ingest(filename, text_content.encode("utf-8"), doc_id)
