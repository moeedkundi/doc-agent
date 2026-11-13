"""Make a clean PDF look scanned: rasterise, skew, soften, speckle, re-wrap as image-only PDF.

Deterministic: all randomness comes from the passed RNG (no Pillow/C-level randomness).
The result has no text layer, so extraction must rely on vision.
"""

# pypdfium2 ships no type information and Pillow's point()/resize() overloads are partially
# untyped; relax only the "unknown type" rules in this file.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import io
import random
from typing import cast

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

DPI = 150

# Pixel lookup tables (index = input grey level).
_PAPER_LUT = [28 + v * 212 // 255 for v in range(256)]  # paper never white, toner never black
_GRAIN_LUT = [128 + (v - 128) // 6 for v in range(256)]
_DUST_LUT = [0] + [255] * 255


def _render_page(doc: pdfium.PdfDocument, index: int, dpi: int) -> Image.Image:
    page = doc[index]
    bitmap = page.render(scale=dpi / 72, grayscale=False)  # pyright: ignore[reportArgumentType]
    return cast(Image.Image, bitmap.to_pil())


def _page_size(doc: pdfium.PdfDocument, index: int) -> tuple[float, float]:
    page = doc[index]
    return float(page.get_width()), float(page.get_height())


def _degrade(page: Image.Image, rng: random.Random) -> Image.Image:
    img = page.convert("L")
    # Paper is never pure white and toner never pure black.
    img = img.point(_PAPER_LUT)
    angle = rng.uniform(0.4, 1.8) * rng.choice((-1, 1))
    img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=236)
    img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.4, 0.8)))

    w, h = img.size
    noise = Image.frombytes("L", (w, h), rng.randbytes(w * h))
    grain = noise.point(_GRAIN_LUT)  # low-amplitude grain
    img = Image.blend(img, grain, 0.10)
    # Sparse 2x2 dust specks (~0.1% of the page), drawn from a half-resolution mask.
    sw, sh = w // 2, h // 2
    dust = Image.frombytes("L", (sw, sh), rng.randbytes(sw * sh))
    specks = dust.point(_DUST_LUT).resize((w, h), Image.Resampling.NEAREST)
    img = Image.composite(img, Image.new("L", (w, h), 70), specks)
    return img


def scanify(pdf_bytes: bytes, rng: random.Random, title: str) -> bytes:
    src = pdfium.PdfDocument(pdf_bytes)
    try:
        out = io.BytesIO()
        c = Canvas(out, invariant=1, pageCompression=1)
        c.setTitle(title)
        for i in range(len(src)):
            w_pt, h_pt = _page_size(src, i)
            img = _degrade(_render_page(src, i, DPI), rng)
            jpg = io.BytesIO()
            img.save(jpg, format="JPEG", quality=72, optimize=False)
            jpg.seek(0)
            c.setPageSize((w_pt, h_pt))
            c.drawImage(ImageReader(jpg), 0, 0, width=w_pt, height=h_pt)
            c.showPage()
        c.save()
        return out.getvalue()
    finally:
        src.close()


def render_png(pdf_bytes: bytes, page_index: int = 0, dpi: int = 110) -> bytes:
    """PNG preview of one page (for humans; not part of the dataset)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        img = _render_page(doc, page_index, dpi)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    finally:
        doc.close()


def page_texts(pdf_bytes: bytes) -> list[str]:
    """Text layer of every page ('' for image-only pages)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        out: list[str] = []
        for i in range(len(doc)):
            textpage = doc[i].get_textpage()
            out.append(str(textpage.get_text_range()))
        return out
    finally:
        doc.close()
