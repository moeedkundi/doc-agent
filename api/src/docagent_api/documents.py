"""Serving stored documents to the viewer."""

# pypdfium2 ships no type information; keep its untyped surface inside this module.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import io
from pathlib import Path
from typing import cast

import pypdfium2 as pdfium
from PIL import Image

_MIME_BY_EXT = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}
VIEW_DPI = 108  # sharp enough for the viewer, small enough to load fast


def mime_of(storage_path: str) -> str:
    return _MIME_BY_EXT.get(Path(storage_path).suffix.lower(), "application/octet-stream")


def render_page_png(path: Path, page: int) -> bytes:
    """Render 1-based `page` of a PDF to PNG bytes."""
    pdf = pdfium.PdfDocument(path)
    try:
        bitmap = pdf[page - 1].render(scale=VIEW_DPI / 72)  # pyright: ignore[reportArgumentType]
        image = cast(Image.Image, bitmap.to_pil())
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()
