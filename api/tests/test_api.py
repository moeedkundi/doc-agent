"""API integration tests. The headline one walks the full flow:
upload PO -> run -> upload invoice -> run -> review -> resume -> mismatch -> ask."""

import asyncio
import io
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest
from reportlab.pdfgen.canvas import Canvas

from docagent_gateway import LedgerEntry
from docagent_gateway.testing import NOW, response

from .conftest import SAMPLES, Stack, classify, truth, wrap

Build = Callable[..., Any]


async def wait_for(
    client: httpx.AsyncClient, run_id: str, statuses: set[str], limit_s: float = 60
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + limit_s
    while True:
        run = (await client.get(f"/api/runs/{run_id}")).json()
        if run["status"] in statuses:
            return run
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"run stuck in {run['status']}: {run}")
        await asyncio.sleep(0.1)


def parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, Any] = {}
        for line in block.split("\n"):
            if line.startswith(":") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key] = value[1:] if value.startswith(" ") else value
        if "event" in fields or "data" in fields:
            fields["data"] = json.loads(fields["data"]) if fields.get("data") else None
            events.append(fields)
    return events


async def upload(client: httpx.AsyncClient, data: bytes, name: str) -> httpx.Response:
    return await client.post("/api/documents", files={"file": (name, data, "application/pdf")})


# -- the full flow ------------------------------------------------------------------------------


async def test_full_flow_upload_run_review_resume_mismatch_ask(stack_factory: Build) -> None:
    s: Stack = await stack_factory()
    c = s.client

    # The model's responses, in call order. The invoice's total is read with low confidence,
    # so validation sends it to review (after one strong-tier re-read that agrees).
    po, inv = truth("po_003"), truth("inv_003")
    s.extraction_model.scripts["gemini/flash-model"] += [
        classify("purchase_order"),
        response(json.dumps(wrap(po)), 1946, 900),
        classify("invoice"),
        response(json.dumps(wrap(inv, low=frozenset({"total"}))), 1875, 800),
    ]
    s.extraction_model.scripts["gemini/strong-model"] += [
        response(
            json.dumps(
                {
                    "fields": [
                        {
                            "path": "total",
                            "value": inv["total"],
                            "confidence": 0.5,
                            "source_page": 1,
                        }
                    ]
                }
            )
        )
    ]

    # 1. Upload the PO (session cookie is created on this first request) and run it.
    r = await upload(c, (SAMPLES / "po_003.pdf").read_bytes(), "po_003.pdf")
    assert r.status_code == 201, r.text
    assert "docagent_session" in c.cookies
    po_doc = r.json()["document_id"]
    run = (await c.post("/api/runs", json={"document_id": po_doc})).json()
    po_run = await wait_for(c, run["run_id"], {"completed", "failed"})
    assert po_run["status"] == "completed", po_run
    assert po_run["result"]["record"]["po_number"] == "PO-85044"

    # SSE replays the whole run for a late client and ends with the result.
    events = parse_sse((await c.get(run["events"])).text)
    kinds = [e["event"] for e in events]
    assert kinds[-1] == "end" and events[-1]["data"]["status"] == "completed"
    node_ends = [e["data"]["node"] for e in events if e["event"] == "node_end"]
    assert node_ends == ["classify", "extract", "validate", "save", "summarize"]
    seqs = [int(e["id"]) for e in events if "id" in e]
    assert seqs == list(range(1, len(seqs) + 1))

    # Reconnect partway: Last-Event-ID resumes after that seq.
    again = parse_sse((await c.get(run["events"], headers={"Last-Event-ID": "5"})).text)
    assert [int(e["id"]) for e in again if "id" in e] == list(range(6, len(seqs) + 1))

    # 2. Upload the invoice and run it: it pauses for review.
    r = await upload(c, (SAMPLES / "inv_003.pdf").read_bytes(), "inv_003.pdf")
    inv_run_id = (await c.post("/api/runs", json={"document_id": r.json()["document_id"]})).json()[
        "run_id"
    ]
    paused = await wait_for(c, inv_run_id, {"awaiting_review", "failed", "completed"})
    assert paused["status"] == "awaiting_review", paused
    review = paused["review"]
    assert [i["rule"] for i in review["issues"]] == ["low_confidence"]
    assert review["issues"][0]["field_path"] == "total"
    assert paused["escalated"] is False  # the run row is final-only; escalation shows in result

    # 3. Approve -> resumes -> saves -> PO match finds the mismatch.
    r = await c.post(f"/api/runs/{inv_run_id}/resume", json={"action": "approve"})
    assert r.status_code == 202, r.text
    again_resume = await c.post(f"/api/runs/{inv_run_id}/resume", json={"action": "approve"})
    assert again_resume.status_code == 409  # not resumable twice
    done = await wait_for(c, inv_run_id, {"completed", "failed"})
    assert done["status"] == "completed", done
    match = done["result"]["match"]
    assert match["status"] == "mismatch"
    assert sorted(i["rule"] for i in match["issues"]) == [
        "total_exceeds_remaining",
        "unit_price_out_of_tolerance",
    ]
    assert done["result"]["escalated"] is True and done["result"]["reviewed"] is True
    inv_events = parse_sse((await c.get(f"/api/runs/{inv_run_id}/events")).text)
    assert "review_required" not in [e["event"] for e in inv_events]  # finished: not paused now
    assert [e["data"]["node"] for e in inv_events if e["event"] == "interrupt"] == ["human_review"]

    # 4. Ask: real SQL through sqlstore's gate + RLS; the model is scripted.
    sql = (
        "SELECT i.invoice_number, m.status FROM invoices i JOIN invoice_po_matches m "
        "ON m.invoice_id = i.id WHERE m.status IN ('mismatch', 'po_not_found')"
    )
    s.agent_model.scripts["gemini/flash-model"] += [
        response(json.dumps({"action": "query", "reason": "list", "sql": sql}), 900, 80),
        response(json.dumps({"answer": "BLS-2026-2422 doesn't match PO-85044."}), 600, 30),
    ]
    r = await c.post("/api/ask", json={"question": "Which invoices don't match their PO?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ask_events = parse_sse(r.text)
    end = ask_events[-1]
    assert end["event"] == "end" and end["data"]["status"] == "completed"
    result = end["data"]["result"]
    assert result["rows"] == [["BLS-2026-2422", "mismatch"]]
    assert result["sql"].endswith("LIMIT 201")
    assert result["answer"] == "BLS-2026-2422 doesn't match PO-85044."


# -- sessions -----------------------------------------------------------------------------------


async def test_cookie_is_httponly_lax_and_sessions_are_isolated(stack_factory: Build) -> None:
    s: Stack = await stack_factory()
    r = await s.client.post("/api/session")
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    sid = r.json()["session_id"]
    assert (await s.client.post("/api/session")).json()["session_id"] == sid  # cookie reused
    doc = (await s.client.post("/api/samples/po_001/document")).json()["document_id"]

    other = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=s.app), base_url="http://testserver"
    )
    async with other:
        r = await other.post("/api/runs", json={"document_id": doc})
        assert r.status_code == 404  # another session's document is invisible
        assert (await other.post("/api/session")).json()["session_id"] != sid


# -- uploads ------------------------------------------------------------------------------------


def _pdf(pages: int) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf)
    for i in range(pages):
        c.drawString(72, 720, f"page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.parametrize(
    ("data", "name", "status", "code"),
    [
        (
            b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200,
            "invoice.pdf",
            415,
            "unsupported_file_type",
        ),
        (b"%PDF-1.7 not really a pdf", "broken.pdf", 422, "unreadable_pdf"),
        (_pdf(6), "long.pdf", 422, "too_many_pages"),
        (b"%PDF-" + b"0" * (5 * 1024 * 1024), "huge.pdf", 413, "file_too_large"),
        (b"", "empty.pdf", 400, "empty_file"),
    ],
)
async def test_upload_rejections(
    stack_factory: Build, data: bytes, name: str, status: int, code: str
) -> None:
    s: Stack = await stack_factory()
    r = await upload(s.client, data, name)
    assert (r.status_code, r.json()["error"]) == (status, code)
    assert r.json()["message"]


async def test_png_and_five_page_pdf_are_accepted(stack_factory: Build) -> None:
    s: Stack = await stack_factory()
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    png = await s.client.post(
        "/api/documents", files={"file": ("scan.jpg", buf.getvalue(), "image/jpeg")}
    )
    assert png.status_code == 201 and png.json()["type"] == "image/png"  # by content, not name
    ok = await upload(s.client, _pdf(5), "five.pdf")
    assert ok.status_code == 201 and ok.json()["pages"] == 5


# -- rate limits and budget ------------------------------------------------------------------------


async def test_rate_limit_returns_429_with_retry_after(stack_factory: Build) -> None:
    s: Stack = await stack_factory(run_limit="2/10minutes")
    bogus = {"document_id": "00000000-0000-0000-0000-00000000abcd"}
    codes = [(await s.client.post("/api/runs", json=bogus)).status_code for _ in range(3)]
    assert codes == [404, 404, 429]
    r = await s.client.post("/api/runs", json=bogus)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert r.json()["error"] == "rate_limited" and "Try again in" in r.json()["message"]


async def test_budget_exhausted_returns_402_with_replay_hint(stack_factory: Build) -> None:
    s: Stack = await stack_factory()
    assert s.services.gateway is not None
    ledger: Any = s.services.gateway._ledger  # pyright: ignore[reportPrivateUsage]
    ledger.record(LedgerEntry(ts=NOW, model="m", tokens_in=0, tokens_out=0, cost_usd=Decimal("5")))
    doc = (await s.client.post("/api/samples/po_001/document")).json()["document_id"]
    r = await s.client.post("/api/runs", json={"document_id": doc})
    body = r.json()
    assert r.status_code == 402 and body["error"] == "budget_exceeded"
    assert body["replay_available"] is True and "po_001" in body["replay_samples"]
    assert (await s.client.post("/api/ask", json={"question": "anything"})).status_code == 402
    # ...while recorded runs stay available
    r = await s.client.post("/api/runs", json={"document_id": doc, "replay": True})
    assert r.status_code == 409 and r.json()["error"] == "no_recording"  # none in this test


async def test_budget_hit_mid_run_marks_the_run(stack_factory: Build) -> None:
    s: Stack = await stack_factory()
    # The API's pre-check passes, but the extraction server's gateway hits the cap.
    s.extraction_ledger.record(
        LedgerEntry(ts=NOW, model="m", tokens_in=0, tokens_out=0, cost_usd=Decimal("5"))
    )
    doc = (await s.client.post("/api/samples/po_001/document")).json()["document_id"]
    run_id = (await s.client.post("/api/runs", json={"document_id": doc})).json()["run_id"]
    run = await wait_for(s.client, run_id, {"completed", "failed"})
    assert run["status"] == "failed"
    assert run["error"]["code"] == "budget_exceeded" and run["error"]["replay_available"]
    events = parse_sse((await s.client.get(f"/api/runs/{run_id}/events")).text)
    assert [e["data"]["node"] for e in events if e["event"] == "error"] == ["classify"]
    assert events[-1]["event"] == "end" and events[-1]["data"]["error_code"] == "budget_exceeded"
    assert s.extraction_model.calls == []  # refused before any request left the process


# -- replay -------------------------------------------------------------------------------------

FIXTURE = {
    "sample_id": "po_001",
    "events": [
        {"offset_ms": 0, "node": "classify", "kind": "node_start"},
        {
            "offset_ms": 900,
            "node": "classify",
            "kind": "tool_end",
            "tool": "classify_document",
            "model": "gemini-3.5-flash-lite",
            "tokens_in": 1500,
            "tokens_out": 50,
            "latency_ms": 900,
            "cost_usd": "0.000575",
        },
        {
            "offset_ms": 950,
            "node": "classify",
            "kind": "node_end",
            "latency_ms": 950,
            "cost_usd": "0.000575",
        },
    ],
    "result": {
        "doc_type": "purchase_order",
        "summary": "recorded",
        "cost_usd": "0.000575",
        "escalated": False,
    },
}


async def test_replay_only_serves_recordings_without_llm(stack_factory: Build) -> None:
    s: Stack = await stack_factory(replay_only=True, fixtures={"po_001": FIXTURE})
    assert s.services.runs.runtime is None and s.services.gateway is None
    samples = (await s.client.get("/api/samples")).json()
    assert {x["id"]: x["replay_available"] for x in samples}["po_001"] is True
    doc = (await s.client.post("/api/samples/po_001/document")).json()["document_id"]
    r = await s.client.post("/api/runs", json={"document_id": doc})
    assert r.status_code == 202 and r.json()["replay"] is True
    run = await wait_for(s.client, r.json()["run_id"], {"completed", "failed"})
    assert run["replay"] and run["result"]["summary"] == "recorded"
    events = parse_sse((await s.client.get(r.json()["events"])).text)
    assert [e["event"] for e in events if "id" in e] == ["node_start", "tool_end", "node_end"]
    # Live work is refused with a code the UI understands.
    assert (await s.client.post("/api/ask", json={"question": "x"})).json()[
        "error"
    ] == "replay_only"
    assert (await upload(s.client, _pdf(1), "x.pdf")).status_code == 503
    doc2 = (await s.client.post("/api/samples/inv_001/document")).json()["document_id"]
    assert (await s.client.post("/api/runs", json={"document_id": doc2})).status_code == 503


async def test_health(stack_factory: Build) -> None:
    s: Stack = await stack_factory()
    r = await s.client.get("/api/health")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok" and body["db"] == "ok"
    assert set(body["mcp"]) == {"extraction", "validation", "sqlstore", "reference"}
    assert body["budget"]["daily_cap_usd"] == "1.00"
    assert "set-cookie" not in r.headers  # health doesn't create sessions


async def test_unknown_run_is_404(stack_factory: Build) -> None:
    s: Stack = await stack_factory()
    r = await s.client.get(f"/api/runs/{UUID(int=7)}")
    assert (r.status_code, r.json()["error"]) == (404, "run_not_found")
