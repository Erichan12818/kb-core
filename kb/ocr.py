#!/usr/bin/env python3
"""Optional OCR for scanned PDFs that have no text layer.

Off by default. A scanned page — a filed HKID card, a passport application —
currently contributes nothing to the index: PyPDFLoader finds no text layer
and returns empty, so kb/ingest.py:load_text() records the file with zero
chunks and moves on. Turning this on changes that: whatever is drawn on the
page gets read and embedded, verbatim. That is not a decision to make for the
user, so it stays opt-in (Settings → Search and capture → OCR scanned PDFs)
and defaults to off.

Runs through an ONNX model bundled inside the rapidocr-onnxruntime package
itself (~13MB, no separate download) rather than shelling out to a system
`tesseract` binary — pytesseract would need Tesseract installed separately by
every user, which the rest of this project deliberately avoids requiring.
"""
from .config import cfg

_engine = None


def enabled():
    return bool(cfg("ocr.enabled", False))


def max_pages():
    try:
        return max(1, int(cfg("ocr.max_pages", 20)))
    except (TypeError, ValueError):
        return 20


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def ocr_pdf(path):
    """Extracted text for a scanned PDF, page by page, up to max_pages().

    Only called when the ordinary text-layer extraction already came back
    empty — this is the fallback for a PDF that is actually just page images,
    not a way to re-OCR PDFs that already have real text.
    """
    import pymupdf

    engine = _get_engine()
    parts = []
    doc = pymupdf.open(str(path))
    try:
        cap = max_pages()
        pages = min(doc.page_count, cap)
        for i in range(pages):
            pix = doc[i].get_pixmap(dpi=200)
            result, _ = engine(pix.tobytes("png"))
            if result:
                parts.append(" ".join(r[1] for r in result))
        if doc.page_count > pages:
            parts.append(f"…（OCR 只處理咗頭 {pages} 頁，共 {doc.page_count} 頁）")
    finally:
        doc.close()
    return "\n".join(parts)
