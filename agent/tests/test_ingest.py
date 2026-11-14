"""The ingest graph end to end with a fake tool layer.

Validation and reference tools run the real server logic in-process; extraction returns
ground truth (optionally altered); sqlstore is an in-memory fake. Checkpoints use
LangGraph's in-memory saver, so interrupt/resume is exercised for real.
"""

import copy
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from docagent_agent.ingest import build_ingest_graph
from docagent_agent.tools import NODE_TOOLS, TOOL_SERVER, NodeTools, ToolCallError, ToolNotAllowed
from docagent_agent.trace import MemoryTraceSink, Tracer
from docagent_mcp_reference import data as reference
from docagent_mcp_validation import matching, rules
from docagent_mcp_validation.settings import load_settings
from docagent_schemas import (
    GroundTruth,
    Invoice,
    InvoiceRecord,
    MatchTolerances,
    PurchaseOrder,
    PurchaseOrderRecord,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "evals" / "dataset" / "samples"
SETTINGS = load_settings(ROOT / "config")
USAGE = {"tier": "default", "model": "flash", "calls": 1, "tokens_in": 1000,
         "tokens_out": 500, "cost_usd": "0.002625", "latency_ms": 1200}  # fmt: skip


def truth(doc_id: str) -> GroundTruth:
    return GroundTruth.model_validate_json((SAMPLES / f"{doc_id}.json").read_text())


def wrap(value: Any) -> Any:
    """Plain record JSON -> extraction JSON (every leaf a Field)."""
    if isinstance(value, dict):
        return {k: wrap(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [wrap(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value):
        value = float(value)  # decimals arrive as strings in record JSON
    return {"value": value, "confidence": 0.98, "source_page": 1, "evidence": None}


@dataclass
class Doc:
    doc_type: str
    record: dict[str, Any]
    """Plain record the 'model' reads off the page."""
    strong_reads: dict[str, Any] | None = None
    """What the strong tier reads for re-extracted paths (default: the same values)."""
    strong_fails: bool = False


@dataclass
class FakeServices:
    docs: dict[str, Doc]
    saved: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=lambda: {})
    matches: list[dict[str, Any]] = field(default_factory=lambda: [])
    calls: list[str] = field(default_factory=lambda: [])

    async def call(self, tool: str, args: Mapping[str, Any]) -> Any:
        self.calls.append(tool)
        handler: Callable[[Mapping[str, Any]], Any] = getattr(self, f"_{tool}")
        return handler(args)

    # extraction
    def _classify_document(self, a: Mapping[str, Any]) -> Any:
        d = self.docs[a["document_id"]]
        return {"doc_type": d.doc_type, "confidence": 0.95, "reason": "test", "usage": USAGE}

    def _extract_fields(self, a: Mapping[str, Any]) -> Any:
        d = self.docs[a["document_id"]]
        return {"doc_type": d.doc_type, "record": wrap(d.record), "pages": [1], "usage": USAGE}

    def _reextract_fields(self, a: Mapping[str, Any]) -> Any:
        d = self.docs[a["document_id"]]
        if d.strong_fails:
            raise ToolCallError("reextract_fields", "provider_unavailable", "quota")
        from docagent_agent.ingest import get_path

        source = d.strong_reads or d.record
        fields = {p: wrap(get_path(source, p)) for p in a["field_paths"]}
        return {"doc_type": d.doc_type, "fields": fields, "usage": {**USAGE, "tier": "strong"}}

    # validation (real logic)
    def _validate_record(self, a: Mapping[str, Any]) -> Any:
        doc_type = a["doc_type"]
        rec = a["record"]
        model: Any
        try:
            model = (Invoice if doc_type == "invoice" else PurchaseOrder).model_validate(rec)
        except Exception:
            model = (InvoiceRecord if doc_type == "invoice" else PurchaseOrderRecord
                     ).model_validate(rec)  # fmt: skip
        return rules.validate(doc_type, model, SETTINGS).model_dump(mode="json")

    def _match_invoice_to_po(self, a: Mapping[str, Any]) -> Any:
        return matching.match_invoice_to_po(
            InvoiceRecord.model_validate(a["invoice"]),
            PurchaseOrderRecord.model_validate(a["purchase_order"]),
            [InvoiceRecord.model_validate(p) for p in a["prior_invoices"]],
            MatchTolerances(),
        ).model_dump(mode="json")

    # reference (real data)
    def _lookup_vendor(self, a: Mapping[str, Any]) -> Any:
        return reference.lookup_vendor(a.get("name"), a.get("tax_id")).model_dump(mode="json")

    # sqlstore (in memory)
    def _save_record(self, a: Mapping[str, Any]) -> Any:
        rid = str(uuid4())
        self.saved[rid] = (a["doc_type"], copy.deepcopy(dict(a["record"])))
        return {"record_id": rid}

    def _get_purchase_order(self, a: Mapping[str, Any]) -> Any:
        pos = [(i, r) for i, (t, r) in self.saved.items()
               if t == "purchase_order" and r["po_number"] == a["po_number"]]  # fmt: skip
        if not pos:
            return {"found": False, "po_id": None, "purchase_order": None, "prior_invoices": [],
                    "duplicate_invoice_ids": []}  # fmt: skip
        prior: list[dict[str, Any]] = []
        dups: list[str] = []
        for i, (t, r) in self.saved.items():
            if (
                t != "invoice"
                or r.get("po_number") != a["po_number"]
                or i == a["exclude_invoice_id"]
            ):
                continue
            if r["invoice_number"] == a.get("exclude_invoice_number"):
                dups.append(i)
            else:
                prior.append({"invoice_id": i, "record": r})
        po_id, po = pos[-1]
        return {"found": True, "po_id": po_id, "purchase_order": po, "prior_invoices": prior,
                "duplicate_invoice_ids": dups}  # fmt: skip

    def _save_match_result(self, a: Mapping[str, Any]) -> Any:
        self.matches.append(dict(a))
        return {"match_id": str(uuid4())}


@dataclass
class Rig:
    services: FakeServices
    sink: MemoryTraceSink
    graph: Any
    session: str = field(default_factory=lambda: str(uuid4()))

    async def run(
        self, document_id: str, resume: Any = None, run_id: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        run_id = run_id or str(uuid4())
        config = {"configurable": {"thread_id": run_id}}
        if resume is None:
            inp: Any = {"run_id": run_id, "session_id": self.session,
                        "document_id": document_id, "cost_usd": "0"}  # fmt: skip
        else:
            inp = Command(resume=resume)
        await self.graph.ainvoke(inp, config)
        return run_id, dict((await self.graph.aget_state(config)).values)

    async def pending(self, run_id: str) -> dict[str, Any] | None:
        snap = await self.graph.aget_state({"configurable": {"thread_id": run_id}})
        return dict(snap.interrupts[0].value) if snap.interrupts else None


def make_rig(**docs: Doc) -> Rig:
    services = FakeServices(docs=dict(docs))
    sink = MemoryTraceSink()
    tracer = Tracer(sink, services)
    return Rig(services, sink, build_ingest_graph(tracer, InMemorySaver()))


def record(doc_id: str) -> dict[str, Any]:
    return truth(doc_id).record.model_dump(mode="json")


def broken_total(doc_id: str) -> dict[str, Any]:
    r = record(doc_id)
    r["total"] = str(Decimal(r["total"]) - Decimal("100.00"))
    return r


# -- the four scenarios -------------------------------------------------------------------------


async def test_po_is_saved_without_review() -> None:
    rig = make_rig(po=Doc("purchase_order", record("po_003")))
    run_id, state = await rig.run("po")
    assert await rig.pending(run_id) is None
    assert state["doc_type"] == "purchase_order" and "record_id" in state
    assert not state.get("reviewed") and not state.get("escalated")
    assert "match" not in state
    assert state["cost_usd"] == "0.005250"  # classify + extract
    assert rig.sink.runs[UUID(run_id)]["status"] == "completed"


async def test_invoice_after_its_po_is_a_two_issue_mismatch() -> None:
    rig = make_rig(po=Doc("purchase_order", record("po_003")),
                   inv=Doc("invoice", record("inv_003")))  # fmt: skip
    await rig.run("po")
    _, state = await rig.run("inv")
    assert state["match"]["status"] == "mismatch"
    assert sorted(i["rule"] for i in state["match"]["issues"]) == [
        "total_exceeds_remaining", "unit_price_out_of_tolerance"]  # fmt: skip
    assert rig.services.matches[-1]["status"] == "mismatch"
    assert "PO PO-85044: mismatch" in state["summary"]


async def test_broken_total_escalates_then_pauses_and_resumes_after_edit() -> None:
    rig = make_rig(po=Doc("purchase_order", record("po_003")),
                   bad=Doc("invoice", broken_total("inv_003")))  # fmt: skip
    await rig.run("po")
    run_id, state = await rig.run("bad")
    payload = await rig.pending(run_id)
    assert payload is not None
    assert state["escalated"] and state["attempts"] == 1 and state["stalled"]
    assert [i["rule"] for i in payload["issues"]] == ["subtotal_plus_tax_equals_total"]
    assert any("same values" in n for n in payload["notes"])
    assert rig.sink.runs[UUID(run_id)]["status"] == "awaiting_review"
    assert "save_record" not in rig.services.calls[-3:]  # nothing saved while paused

    fixed = dict(payload["record"], total=record("inv_003")["total"])
    _, state = await rig.run("bad", resume={"action": "edit", "record": fixed}, run_id=run_id)
    assert state["reviewed"] and state["review_edits"] == [
        {"path": "total", "old": payload["record"]["total"], "new": fixed["total"]}
    ]
    assert rig.sink.reviews[-1].action == "edit"
    assert state["final_issues"] == []
    assert state["match"]["status"] == "mismatch"  # the price drift is still there
    assert rig.sink.runs[UUID(run_id)]["status"] == "completed"
    assert rig.sink.runs[UUID(run_id)]["escalated"] is True


async def test_rescanned_copy_of_same_invoice_gets_the_same_result() -> None:
    rig = make_rig(po=Doc("purchase_order", record("po_003")),
                   inv=Doc("invoice", record("inv_003")),
                   scan=Doc("invoice", record("inv_003")))  # fmt: skip
    await rig.run("po")
    _, first = await rig.run("inv")
    _, again = await rig.run("scan")
    assert again["match"]["status"] == first["match"]["status"] == "mismatch"
    assert again["match"]["issues"] == first["match"]["issues"]
    assert len(again["match"]["duplicates"]) == 1
    assert "re-submission" in again["summary"]


# -- other paths ---------------------------------------------------------------------------


async def test_strong_tier_fixes_a_misread_without_review() -> None:
    misread = broken_total("inv_003")
    rig = make_rig(po=Doc("purchase_order", record("po_003")),
                   inv=Doc("invoice", misread, strong_reads=record("inv_003")))  # fmt: skip
    await rig.run("po")
    run_id, state = await rig.run("inv")
    assert await rig.pending(run_id) is None
    assert state["escalated"] and not state.get("reviewed")
    assert any("corrected total" in n for n in state["notes"])
    saved_total = next(r for t, r in rig.services.saved.values() if t == "invoice")["total"]
    assert saved_total == record("inv_003")["total"]


async def test_failed_escalation_goes_to_review_with_a_note() -> None:
    rig = make_rig(bad=Doc("invoice", broken_total("inv_003"), strong_fails=True))
    run_id, _ = await rig.run("bad")
    payload = await rig.pending(run_id)
    assert payload is not None
    assert any("provider_unavailable" in n for n in payload["notes"])
    _, state = await rig.run("bad", resume={"action": "approve"}, run_id=run_id)
    assert state["match"]["status"] == "po_not_found"  # its PO was never ingested
    assert rig.sink.reviews[-1].action == "approve"


async def test_unknown_documents_are_rejected() -> None:
    rig = make_rig(junk=Doc("unknown", {}))
    run_id, state = await rig.run("junk")
    assert state["summary"].startswith("Rejected")
    assert rig.sink.runs[UUID(run_id)]["status"] == "rejected"
    assert rig.services.calls == ["classify_document"]


# -- tool isolation and tracing --------------------------------------------------------------------


SQLSTORE_TOOLS = {t for t, s in TOOL_SERVER.items() if s == "sqlstore"}


def test_classify_and_extract_cannot_reach_sqlstore() -> None:
    assert NODE_TOOLS["classify"] == {"classify_document"}
    assert NODE_TOOLS["extract"] == {"extract_fields"}
    for node in ("classify", "extract", "reextract", "validate"):
        assert not NODE_TOOLS[node] & SQLSTORE_TOOLS, node


@pytest.mark.parametrize("tool", sorted(SQLSTORE_TOOLS))
async def test_node_tools_refuse_anything_off_the_allowlist(tool: str) -> None:
    services = FakeServices(docs={})
    for node in ("classify", "extract"):
        with pytest.raises(ToolNotAllowed):
            await NodeTools(node, services).call(tool, sql="DELETE FROM invoices")
    assert services.calls == []  # refused before reaching any server


async def test_every_node_traces_start_and_end_and_only_uses_its_tools() -> None:
    rig = make_rig(po=Doc("purchase_order", record("po_003")),
                   inv=Doc("invoice", record("inv_003")))  # fmt: skip
    await rig.run("po")
    run_id, _ = await rig.run("inv")
    events = [e for e in rig.sink.events if e.run_id == UUID(run_id)]
    nodes = [e.node for e in events if e.kind == "node_start"]
    assert nodes == ["classify", "extract", "validate", "save", "po_match", "summarize"]
    assert [e.node for e in events if e.kind == "node_end"] == nodes
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    for e in events:
        if e.kind == "tool_end":
            assert e.tool in NODE_TOOLS[e.node], (e.node, e.tool)
    extract_end = next(e for e in events if e.kind == "node_end" and e.node == "extract")
    assert (extract_end.tool, extract_end.model, extract_end.tokens_in, extract_end.cost_usd) == (
        "extract_fields",
        "flash",
        1000,
        Decimal("0.002625"),
    )
    assert extract_end.latency_ms is not None
    assert json.dumps(extract_end.sse())  # SSE shape is JSON-serialisable


async def test_interrupt_is_traced_and_resume_continues_the_same_run() -> None:
    rig = make_rig(bad=Doc("invoice", broken_total("inv_003")))
    run_id, _ = await rig.run("bad")
    kinds = [(e.node, e.kind) for e in rig.sink.events if e.run_id == UUID(run_id)]
    assert ("human_review", "interrupt") in kinds
    assert ("reextract", "node_end") in kinds
    await rig.run("bad", resume={"action": "approve"}, run_id=run_id)
    after = [(e.node, e.kind) for e in rig.sink.events if e.run_id == UUID(run_id)]
    assert after[-1] == ("summarize", "node_end")
    assert ("human_review", "node_end") in after
