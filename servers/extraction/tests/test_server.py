"""Extraction tools over MCP (in-memory client) with the real gateway and a fake provider."""

import json
import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import Client

from docagent_gateway import LedgerEntry, MemoryLedger
from docagent_gateway.testing import NOW, Harness, make_config, response
from docagent_mcp_extraction.server import ExtractionSettings, create_server
from docagent_mcp_extraction.store import DocumentStore
from docagent_schemas import GroundTruth, Invoice

DATASET = Path(__file__).resolve().parents[3] / "evals" / "dataset"


def wrap(record: Any, confidence: float = 0.97) -> Any:
    """Ground-truth record JSON -> extraction-shaped JSON (every leaf a Field)."""
    if isinstance(record, dict):
        return {k: wrap(v, confidence) for k, v in record.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(record, list):
        return [wrap(v, confidence) for v in record]  # pyright: ignore[reportUnknownVariableType]
    value = (
        float(record) if isinstance(record, str) and record.replace(".", "").isdigit() else record
    )
    return {"value": value, "confidence": confidence, "source_page": 1, "evidence": None}


@pytest.fixture
def docs(tmp_path: Path) -> Callable[[str], UUID]:
    def add(name: str) -> UUID:
        doc_id = uuid4()
        shutil.copy(DATASET / "samples" / f"{name}.pdf", tmp_path / f"{doc_id}.pdf")
        return doc_id

    return add


@pytest.fixture
def truth() -> GroundTruth:
    return GroundTruth.model_validate_json((DATASET / "samples" / "inv_003.json").read_text())


def _server(h: Harness, tmp_path: Path, **kw: Any) -> Any:
    return create_server(h.gateway, DocumentStore(tmp_path), ExtractionSettings(**kw))


def _err_code(result: Any) -> str:
    assert result.is_error
    return json.loads(result.content[0].text.split(": ", 1)[1])["code"]


async def test_tools_listed(harness: Callable[..., Harness], tmp_path: Path) -> None:
    async with Client(_server(harness(), tmp_path)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"classify_document", "extract_fields", "reextract_fields"}
    assert tools["extract_fields"].input_schema["properties"]["document_id"]["format"] == "uuid"


async def test_extract_fields_through_gateway(
    harness: Callable[..., Harness],
    tmp_path: Path,
    docs: Callable[[str], UUID],
    truth: GroundTruth,
) -> None:
    payload = json.dumps(wrap(truth.record.model_dump(mode="json")))
    h = harness(flash_model=[response(payload, 1875, 1052)])
    doc_id, sid = docs("inv_003"), uuid4()
    async with Client(_server(h, tmp_path)) as client:
        res = await client.call_tool(
            "extract_fields",
            {"document_id": str(doc_id), "doc_type": "invoice", "session_id": str(sid)},
        )
    assert not res.is_error, res.content
    out = res.structured_content
    assert out["pages"] == [1]
    assert out["usage"] == {
        "tier": "default", "model": "flash-model", "calls": 1, "tokens_in": 1875,
        "tokens_out": 1052, "cost_usd": "0.005351", "latency_ms": out["usage"]["latency_ms"],
    }  # fmt: skip
    assert Invoice.model_validate(out["record"]).to_record() == truth.record
    sent = h.provider.calls[0]["messages"]
    images = [p for p in sent[1]["content"] if p["type"] == "image_url"]
    assert len(images) == 1 and images[0]["image_url"]["url"].startswith("data:image/png")
    assert "<untrusted_document_text>" in sent[1]["content"][0]["text"]
    [entry] = h.ledger.entries
    assert entry.session_id == sid and entry.cost_usd == Decimal("0.005351")


async def test_classify(
    harness: Callable[..., Harness], tmp_path: Path, docs: Callable[[str], UUID]
) -> None:
    answer = {"doc_type": "purchase_order", "confidence": 0.93, "reason": "Says PURCHASE ORDER"}
    h = harness(flash_model=[response(json.dumps(answer), 900, 60)])
    async with Client(_server(h, tmp_path)) as client:
        res = await client.call_tool("classify_document", {"document_id": str(docs("po_001"))})
    out = res.structured_content
    assert (out["doc_type"], out["confidence"]) == ("purchase_order", 0.93)
    assert out["usage"]["tokens_in"] == 900


async def test_reextract_only_requested_paths_and_coerces_types(
    harness: Callable[..., Harness], tmp_path: Path, docs: Callable[[str], UUID]
) -> None:
    answer = {"fields": [
        {"path": "line_items[1].unit_price", "value": "98.08", "confidence": 0.9,
         "source_page": 1, "evidence": "98.08"},
        {"path": "due_date", "value": "13/09/2026", "confidence": 0.8},
    ]}  # fmt: skip
    h = harness(strong_model=[response(json.dumps(answer))])
    async with Client(_server(h, tmp_path)) as client:
        res = await client.call_tool("reextract_fields", {
            "document_id": str(docs("inv_003")), "doc_type": "invoice",
            "field_paths": ["line_items[1].unit_price", "due_date", "seller.tax_id"],
        })  # fmt: skip
    out = res.structured_content
    assert h.provider.calls[0]["model"] == "gemini/strong-model"  # strong tier by default
    prompt = h.provider.calls[0]["messages"][1]["content"][0]["text"]
    assert "- line_items[1].unit_price (plain number)" in prompt
    assert "- due_date (date as YYYY-MM-DD)" in prompt
    fields = out["fields"]
    assert set(fields) == {"line_items[1].unit_price", "due_date", "seller.tax_id"}
    assert fields["line_items[1].unit_price"]["value"] == 98.08
    assert fields["due_date"] == {  # wrong date format -> null, confidence 0 -> review
        "value": None, "confidence": 0.0, "source_page": None,
        "evidence": "unreadable value '13/09/2026'",
    }  # fmt: skip
    assert fields["seller.tax_id"]["value"] is None and fields["seller.tax_id"]["confidence"] == 0


@pytest.mark.parametrize(
    "path", ["seller", "line_items.unit_price", "line_items[0]", "nope", "total[0]"]
)
async def test_reextract_rejects_bad_paths(
    path: str, harness: Callable[..., Harness], tmp_path: Path, docs: Callable[[str], UUID]
) -> None:
    async with Client(_server(harness(), tmp_path)) as client:
        res = await client.call_tool("reextract_fields", {
            "document_id": str(docs("inv_003")), "doc_type": "invoice", "field_paths": [path],
        })  # fmt: skip
    assert _err_code(res) == "unknown_field_path"


async def test_structured_errors(
    harness: Callable[..., Harness], tmp_path: Path, docs: Callable[[str], UUID]
) -> None:
    ledger = MemoryLedger()
    sid = uuid4()
    ledger.record(
        LedgerEntry(
            ts=NOW, model="m", tokens_in=0, tokens_out=0, cost_usd=Decimal("0.10"), session_id=sid
        )
    )
    h = harness(make_config(), ledger)
    doc = docs("inv_003")
    async with Client(_server(h, tmp_path, max_pages=5)) as client:
        missing = await client.call_tool(
            "extract_fields", {"document_id": str(uuid4()), "doc_type": "invoice"}
        )
        pages = await client.call_tool(
            "extract_fields", {"document_id": str(doc), "doc_type": "invoice", "pages": [2]}
        )
        budget = await client.call_tool(
            "extract_fields",
            {"document_id": str(doc), "doc_type": "invoice", "session_id": str(sid)},
        )
        bad_id = await client.call_tool(
            "extract_fields", {"document_id": "../../etc/passwd", "doc_type": "invoice"}
        )
    assert _err_code(missing) == "document_not_found"
    assert _err_code(pages) == "invalid_pages"
    assert _err_code(budget) == "budget_exceeded"
    assert bad_id.is_error  # not a UUID: rejected by the input schema
    assert h.provider.calls == []


async def test_page_limit(
    harness: Callable[..., Harness], tmp_path: Path, docs: Callable[[str], UUID]
) -> None:
    long_doc = uuid4()
    shutil.copy(DATASET / "eval" / "po_005.pdf", tmp_path / f"{long_doc}.pdf")  # 2 pages
    async with Client(_server(harness(), tmp_path, max_pages=1)) as client:
        res = await client.call_tool(
            "extract_fields", {"document_id": str(long_doc), "doc_type": "purchase_order"}
        )
    assert _err_code(res) == "too_many_pages"


async def test_source_pages_outside_the_document_are_dropped(
    harness: Callable[..., Harness],
    tmp_path: Path,
    docs: Callable[[str], UUID],
    truth: GroundTruth,
) -> None:
    payload = wrap(truth.record.model_dump(mode="json"))
    payload["invoice_number"]["source_page"] = 7  # a one-page document
    payload["total"]["source_page"] = 10**300  # seen in a real run; breaks checkpointing
    h = harness(
        flash_model=[
            response(json.dumps(payload)),
            response(json.dumps({**payload, "total": {**payload["total"], "source_page": 1}})),
        ]
    )
    async with Client(_server(h, tmp_path)) as client:
        res = await client.call_tool(
            "extract_fields", {"document_id": str(docs("inv_003")), "doc_type": "invoice"}
        )
    record = res.structured_content["record"]
    assert record["invoice_number"]["source_page"] is None
    assert record["total"]["source_page"] == 1  # out-of-bound value forced a repair call
    assert len(h.provider.calls) == 2
