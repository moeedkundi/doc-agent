"""Turn a PDF into what the model sees: page images (PNG) plus the text layer, if any."""

# pypdfium2 ships no type information; keep its untyped surface inside this module.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import io
from dataclasses import dataclass
from typing import cast

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

DEFAULT_DPI = 150
"""Must match extraction.render_dpi in config/limits.yaml."""


@dataclass(frozen=True)
class PageContent:
    number: int
    """1-based page number."""
    png: bytes
    text: str
    """Text layer; empty for scanned/image-only pages."""


def render_pages(pdf_bytes: bytes, dpi: int = DEFAULT_DPI) -> list[PageContent]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    texts = [(page.extract_text() or "").strip() for page in reader.pages]
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        pages: list[PageContent] = []
        for i in range(len(doc)):
            bitmap = doc[i].render(scale=dpi / 72)  # pyright: ignore[reportArgumentType]
            img = cast(Image.Image, bitmap.to_pil())
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            pages.append(PageContent(number=i + 1, png=buf.getvalue(), text=texts[i]))
        return pages
    finally:
        doc.close()


def page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def render_image(data: bytes) -> list[PageContent]:
    """A PNG/JPEG upload is a one-page document with no text layer (re-encoded as PNG)."""
    with Image.open(io.BytesIO(data)) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
    return [PageContent(number=1, png=buf.getvalue(), text="")]
