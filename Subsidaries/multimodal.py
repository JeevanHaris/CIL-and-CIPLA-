"""
ARIA v3.0 — Multimodal Input Tools
────────────────────────────────────
Handles non-text inputs the agent may receive:
  - Scanned PDF  → OCR via pytesseract (page by page)
  - Image file   → OCR or vision analysis (llava via Gateway)
  - Engineering drawing / photograph → vision model

Install prerequisites:
  pip install pytesseract Pillow pypdf
  # Windows: also install Tesseract binary from
  # https://github.com/UB-Mannheim/tesseract/wiki
  # and add to PATH, or set TESSERACT_CMD below.

If pytesseract / Tesseract is not installed, OCR functions
gracefully return an error string rather than crashing.
"""

import os
import io
import re


# ─── Tesseract path (override if not in PATH) ──────────────────
# Example: r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "")


# ─── OCR Result ────────────────────────────────────────────────
class OCRResult:
    def __init__(self, text="", page_texts=None, error=None, pages=0):
        self.text       = text          # full concatenated text
        self.page_texts = page_texts or []  # per-page text list
        self.error      = error
        self.pages      = pages

    @property
    def success(self):
        return self.error is None and bool(self.text.strip())

    def to_dict(self):
        return {
            "success":    self.success,
            "pages":      self.pages,
            "char_count": len(self.text),
            "preview":    self.text[:300],
            "error":      self.error,
        }

    def __str__(self):
        if self.success:
            return f"OCR OK ({self.pages} pages, {len(self.text)} chars)"
        return f"OCR Error: {self.error}"


# ─── Scanned PDF Detection ─────────────────────────────────────
def is_scanned_pdf(extracted_text: str, page_count: int = 1) -> bool:
    """
    Heuristic: a PDF is considered scanned/image-based if the native
    text extraction yields very few characters per page.

    Threshold: < 50 meaningful chars per page → likely scanned.
    """
    if not extracted_text:
        return True
    clean = re.sub(r"\s+", "", extracted_text)
    chars_per_page = len(clean) / max(page_count, 1)
    return chars_per_page < 50


# ─── OCR a full PDF ────────────────────────────────────────────
def ocr_pdf(file_bytes: bytes, max_pages: int = 30) -> OCRResult:
    """
    Convert each page of a scanned PDF to an image and run Tesseract OCR.

    Args:
        file_bytes: Raw PDF bytes.
        max_pages:  Safety cap (default 30 pages).

    Returns:
        OCRResult with full text and per-page text list.
    """
    # Try to import dependencies
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        return OCRResult(error=(
            "pdf2image not installed. Run: pip install pdf2image\n"
            "Also install poppler: https://github.com/oschwartz10612/poppler-windows/releases"
        ))

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return OCRResult(error=(
            "pytesseract / Pillow not installed. Run: pip install pytesseract Pillow"
        ))

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    try:
        # Convert PDF pages to PIL Images
        images = convert_from_bytes(file_bytes, dpi=200, first_page=1,
                                    last_page=max_pages)
    except Exception as e:
        return OCRResult(error=f"PDF→image conversion failed: {e}")

    page_texts = []
    for i, img in enumerate(images):
        try:
            page_text = pytesseract.image_to_string(img, lang="eng")
            page_texts.append(page_text.strip())
        except Exception as e:
            page_texts.append(f"[OCR error on page {i + 1}: {e}]")

    full_text = "\n\n".join(page_texts)
    return OCRResult(
        text=full_text,
        page_texts=page_texts,
        pages=len(images),
    )


# ─── OCR a single image ────────────────────────────────────────
def ocr_image(file_bytes: bytes) -> OCRResult:
    """
    Run Tesseract OCR on a single image (PNG, JPG, BMP, TIFF).

    Returns:
        OCRResult with extracted text.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return OCRResult(error=(
            "pytesseract / Pillow not installed. Run: pip install pytesseract Pillow"
        ))

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    try:
        img = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(img, lang="eng")
        return OCRResult(text=text.strip(), page_texts=[text.strip()], pages=1)
    except Exception as e:
        return OCRResult(error=f"Image OCR failed: {e}")


# ─── Smart PDF Reader ──────────────────────────────────────────
def smart_read_pdf(file_bytes: bytes) -> OCRResult:
    """
    Intelligently read a PDF:
      1. Try native text extraction (pypdf).
      2. If scanned (< 50 chars/page), fall back to OCR.

    Returns:
        OCRResult with text, page_texts, and detection info.
    """
    native_text = ""
    page_count  = 1

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        page_count = len(reader.pages)
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        native_text = "\n\n".join(pages)
    except ImportError:
        pass
    except Exception:
        pass

    if is_scanned_pdf(native_text, page_count):
        print("[Multimodal] Scanned PDF detected → running OCR")
        return ocr_pdf(file_bytes)

    # Native text is sufficient
    pages = [p.strip() for p in native_text.split("\n\n") if p.strip()]
    return OCRResult(
        text=native_text.strip(),
        page_texts=pages,
        pages=page_count,
    )


# ─── Vision Analysis via LLaVA ────────────────────────────────
def analyze_image_with_vision(image_bytes: bytes, gateway,
                               prompt: str = None) -> str:
    """
    Send an image to the vision model (LLaVA) via the ModelGateway.

    Args:
        image_bytes: Raw image bytes (PNG/JPG).
        gateway:     The running ModelGateway instance.
        prompt:      Custom analysis prompt. Defaults to general description.

    Returns:
        The vision model's text response.
    """
    import base64

    if prompt is None:
        prompt = (
            "Describe this image in detail. List all visible objects, text, "
            "labels, measurements, defects, or notable features. "
            "Be specific and structured."
        )

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    try:
        response = gateway.call(
            model_id="llava",
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64],
                }
            ],
        )
        return response.content
    except Exception as e:
        return f"[Vision analysis failed: {e}]"


# ─── MIME → modality classifier ───────────────────────────────
def classify_file_modality(filename: str, file_bytes: bytes = None) -> str:
    """
    Determine what kind of processing a file needs.

    Returns one of:
        "text"    → plain text / markdown / code
        "pdf"     → PDF (may need OCR if scanned)
        "image"   → image (needs vision or OCR)
        "office"  → DOCX / XLSX / PPTX (structured extraction)
        "unknown" → unsupported
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md", ".csv", ".rst", ".json", ".yaml", ".yml"):
        return "text"
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".webp"):
        return "image"
    if ext in (".docx", ".xlsx", ".xls", ".pptx", ".ppt"):
        return "office"
    return "unknown"
