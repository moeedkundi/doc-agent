from pathlib import Path

from docagent_mcp_extraction.pages import render_pages
from docagent_mcp_extraction.prompts import SYSTEM_PROMPT, extraction_messages

DATASET = Path(__file__).resolve().parents[3] / "evals" / "dataset"


def test_clean_pdf_has_png_pages_and_text_layer() -> None:
    pages = render_pages((DATASET / "samples" / "po_001.pdf").read_bytes())
    assert len(pages) == 1
    assert pages[0].png.startswith(b"\x89PNG")
    assert "PURCHASE ORDER" in pages[0].text.upper()


def test_scanned_pdf_has_no_text_layer() -> None:
    pages = render_pages((DATASET / "samples" / "inv_002.pdf").read_bytes())
    assert pages[0].text == ""


def test_document_text_is_fenced_as_untrusted() -> None:
    pages = render_pages((DATASET / "samples" / "po_001.pdf").read_bytes())
    system, user = extraction_messages("purchase_order", pages)
    assert system["content"] == SYSTEM_PROMPT and "UNTRUSTED" in SYSTEM_PROMPT
    body = user["content"]
    assert body.index("<untrusted_document_text>") < body.index(pages[0].text[:20])
    assert body.rstrip().endswith("</untrusted_document_text>")
