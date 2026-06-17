"""PDF text extraction.

This module is issuer-agnostic. It only turns a PDF into plain text using,
in order of preference:

1. PyMuPDF (``fitz``) -- fast, good layout preservation.
2. pdfplumber -- fallback if PyMuPDF is unavailable or yields no text.
3. OCR (``pytesseract`` + ``pdf2image``) -- only attempted when steps 1 and 2
   produce no usable text, per the project requirement to avoid OCR unless
   text extraction fails. OCR is optional; if its dependencies are not
   installed we log a warning and give up rather than crashing.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Below this many non-whitespace characters we treat extraction as "failed"
# and fall through to the next strategy. A real statement has far more text;
# scanned/image-only PDFs typically yield near-zero characters here.
_MIN_USABLE_CHARS = 40


def _extract_pymupdf(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.debug("PyMuPDF not installed; skipping")
        return ""
    try:
        with fitz.open(path) as doc:
            return "\n".join(page.get_text("text") for page in doc)
    except Exception as exc:  # pragma: no cover - corrupt/locked PDFs
        logger.warning("PyMuPDF failed to read %s: %s", path, exc)
        return ""


def _extract_pdfplumber(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError:
        logger.debug("pdfplumber not installed; skipping")
        return ""
    try:
        with pdfplumber.open(path) as pdf:
            parts = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(parts)
    except Exception as exc:  # pragma: no cover - corrupt/locked PDFs
        logger.warning("pdfplumber failed to read %s: %s", path, exc)
        return ""


def _extract_ocr(path: Path) -> str:
    """Last-resort OCR. Returns "" if OCR dependencies are unavailable."""
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        logger.warning(
            "Text extraction failed for %s and OCR dependencies "
            "(pytesseract, pdf2image) are not installed; cannot recover text.",
            path,
        )
        return ""
    try:
        logger.warning("Falling back to OCR for %s (no embedded text found)", path)
        images = convert_from_path(str(path))
        return "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception as exc:  # pragma: no cover - OCR runtime issues
        logger.warning("OCR failed for %s: %s", path, exc)
        return ""


def _usable(text: str) -> bool:
    return len("".join(text.split())) >= _MIN_USABLE_CHARS


def extract_text(path: str | Path) -> str:
    """Extract plain text from ``path``, trying each strategy in turn.

    Returns an empty string if no strategy produces usable text.
    """
    path = Path(path)

    text = _extract_pymupdf(path)
    if _usable(text):
        return text

    fallback = _extract_pdfplumber(path)
    if _usable(fallback):
        logger.debug("Used pdfplumber fallback for %s", path)
        return fallback

    # Keep whatever embedded text we did manage to scrape, in case it is
    # short but real. Only OCR when we have essentially nothing.
    best = text or fallback
    if _usable(best):
        return best

    ocr_text = _extract_ocr(path)
    if ocr_text:
        return ocr_text

    logger.warning("No usable text could be extracted from %s", path)
    return best
