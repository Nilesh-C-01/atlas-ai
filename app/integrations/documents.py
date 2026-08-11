"""
integrations/documents.py

Extracts text from an uploaded PDF so it can be injected into the
conversation as context — same "raw text IS the grounding" approach as
Sheets, no separate parsing/embedding layer.
"""

from __future__ import annotations

import io

from pypdf import PdfReader

MAX_PDF_CHARS = 20_000  # keep the extracted text within a sane token budget


def extract_pdf_text(pdf_bytes: bytes) -> str | None:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return None

    text = "\n\n".join(p for p in pages_text if p.strip())
    if not text.strip():
        return None

    if len(text) > MAX_PDF_CHARS:
        text = text[:MAX_PDF_CHARS]

    return text
