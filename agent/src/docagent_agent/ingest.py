"""The ingest graph (SPEC §7.1).

    START -> classify --unknown--> reject_unknown -> END
               |
               v
            extract (default tier) -> validate --fixable errors, tries left--> reextract
                                        ^         (strong tier, failing paths only) |
                                        +-------------------------------------------+
                                        |--needs review--> human_review (interrupt)
                                        |                        |
                                        v                        v
                                      save <-------------- validate_final
                                        |--invoice with PO number--> po_match --+
                                        v                                       |
                                    summarize <---------------------------------+ -> END

Nodes call MCP tools only through their `NodeContext` (allowlist in tools.NODE_TOOLS) and
every node emits start/end trace events. State is JSON-only so the Postgres checkpointer
can persist it across the human-review pause (thread_id = run_id).
"""

# State keys are filled in node order (classify -> extract -> validate -> ...), and routing
# only reaches a node once the keys it reads exist, so "possibly missing key" is a false
# positive here. langgraph ships partial type information.
# pyright: reportTypedDictNotRequiredAccess=false, reportMissingTypeStubs=false

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict, cast
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict

from docagent_agent.tools import ToolCallError
from docagent_agent.trace import NodeContext, ReviewAction, Tracer
from docagent_schemas import Invoice, InvoiceRecord, PurchaseOrder, PurchaseOrderRecord

Json = dict[str, Any]


class IngestState(TypedDict, total=False):
    run_id: str
    session_id: str
    document_id: str
    doc_type: str
    classification: Json
    extracted: Json
    """Extraction output: every leaf a Field {value, confidence, source_page, evidence}."""
    record: Json
    """Plain record (what gets saved); after review, the human's version."""
    issues: list[Json]
    final_issues: list[Json]
    attempts: int
    escalated: bool
    stalled: bool
    notes: list[str]
    reviewed: bool
    review_edits: list[Json]
    record_id: str
    match: Json
    cost_usd: str
    summary: str


class ReviewDecision(BaseModel):
    """What a reviewer sends back to resume the graph."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "edit"]
    record: Json | None = None
    """Full edited plain record (required for `edit`)."""


@dataclass(frozen=True)
class IngestSettings:
    max_reextract_attempts: int = 2


_RECORDS: dict[str, tuple[type[Invoice] | type[PurchaseOrder],
                          type[InvoiceRecord] | type[PurchaseOrderRecord]]] = {
    "invoice": (Invoice, InvoiceRecord),
    "purchase_order": (PurchaseOrder, PurchaseOrderRecord),
}  # fmt: skip
_SEGMENT = re.compile(r"([a-z_]+)(?:\[(\d+)\])?")


# -- helpers ----------------------------------------------------------------------------


def _ids(state: IngestState) -> tuple[UUID, UUID]:
    return UUID(state["run_id"]), UUID(state["session_id"])


def _add_cost(state: IngestState, ctx: NodeContext) -> str:
    return str(Decimal(state.get("cost_usd", "0")) + ctx.cost)


def _plain(doc_type: str, extracted: Json) -> Json:
    model = _RECORDS[doc_type][0].model_validate(extracted)
    return model.to_record().model_dump(mode="json")


def get_path(data: Json, path: str) -> Any:
    cur: Any = data
    for seg in path.split("."):
        m = _SEGMENT.fullmatch(seg)
        if m is None:
            raise KeyError(path)
        cur = cur[m.group(1)]
        if m.group(2) is not None:
            cur = cur[int(m.group(2))]
    return cur


def set_path(data: Json, path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = data
    for seg in parts[:-1]:
        m = _SEGMENT.fullmatch(seg)
        if m is None:
            raise KeyError(path)
        cur = cur[m.group(1)]
        if m.group(2) is not None:
            cur = cur[int(m.group(2))]
    m = _SEGMENT.fullmatch(parts[-1])
    if m is None:
        raise KeyError(path)
    if m.group(2) is None:
        cur[m.group(1)] = value
    else:
        cur[m.group(1)][int(m.group(2))] = value


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in cast(Json, data).items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
        return out
    if isinstance(data, list):
        out = {}
        for i, v in enumerate(cast(list[Any], data)):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: data}


def reextract_paths(issues: list[Json]) -> list[str]:
    """Field paths worth re-reading: fields that broke a rule or were read with low
    confidence. Arithmetic failures widen to every operand, since any of them may be misread.
    """
    paths: list[str] = []
    for issue in issues:
        path = str(issue.get("field_path") or "")
        if not path or not (issue["severity"] == "error" or issue["rule"] == "low_confidence"):
            continue
        if issue["rule"] in ("subtotal_plus_tax_equals_total", "lines_sum_to_subtotal"):
            paths += ["subtotal", "tax_amount", "total"]
        elif issue["rule"] == "line_amount" and path.endswith(".line_total"):
            base = path.rsplit(".", 1)[0]
            paths += [f"{base}.quantity", f"{base}.unit_price", path]
        elif issue["rule"] in ("required_field", "line_items_present") and path == "line_items":
            continue  # a whole list can't be re-read field-by-field
        else:
            paths.append(path)
    return list(dict.fromkeys(paths))


def result_of(state: Mapping[str, Any]) -> Json:
    """What the API reports for a finished ingest run."""
    keys = ("doc_type", "record", "issues", "final_issues", "match", "summary", "notes",
            "review_edits", "record_id", "cost_usd", "escalated", "reviewed")  # fmt: skip
    return {k: state[k] for k in keys if k in state}


def blocking(issues: list[Json]) -> list[Json]:
    return [i for i in issues if i["severity"] in ("error", "review")]


# -- graph --------------------------------------------------------------------------------


def build_ingest_graph(
    tracer: Tracer,
    checkpointer: BaseCheckpointSaver[Any] | None,
    settings: IngestSettings | None = None,
) -> CompiledStateGraph[IngestState, None, IngestState, IngestState]:
    cfg = settings or IngestSettings()

    async def classify(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        await tracer.sink.set_run(run_id, sid, status="running")
        async with tracer.node("classify", run_id, sid) as ctx:
            out = await ctx.call(
                "classify_document",
                document_id=state["document_id"],
                session_id=state["session_id"],
                run_id=state["run_id"],
            )
        return {"doc_type": out["doc_type"], "classification": out,
                "cost_usd": _add_cost(state, ctx), "attempts": 0, "escalated": False,
                "notes": []}  # fmt: skip

    async def reject_unknown(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        async with tracer.node("reject_unknown", run_id, sid):
            reason = state["classification"].get("reason", "")
            summary = f"Rejected: not an invoice or purchase order ({reason})"
            await tracer.sink.set_run(run_id, sid, status="rejected",
                                      total_cost_usd=Decimal(state.get("cost_usd", "0")),
                                      result_json=result_of({**state, "summary": summary}),
                                      finished_at=datetime.now(UTC))  # fmt: skip
        return {"summary": summary}

    async def extract(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        async with tracer.node("extract", run_id, sid) as ctx:
            out = await ctx.call(
                "extract_fields",
                document_id=state["document_id"],
                doc_type=state["doc_type"],
                model_tier="default",
                session_id=state["session_id"],
                run_id=state["run_id"],
            )
        return {"extracted": out["record"], "cost_usd": _add_cost(state, ctx)}  # fmt: skip

    async def validate(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        doc_type, extracted = state["doc_type"], state["extracted"]
        async with tracer.node("validate", run_id, sid) as ctx:
            result = await ctx.call("validate_record", doc_type=doc_type, record=extracted)
            party = "seller" if doc_type == "invoice" else "vendor"
            name = extracted[party]["name"]["value"]
            tax_id = extracted[party]["tax_id"]["value"]
            issues: list[Json] = list(result["issues"])
            if name or tax_id:
                vendor = await ctx.call("lookup_vendor", name=name, tax_id=tax_id)
                if not vendor["found"]:
                    issues.append({"field_path": f"{party}.name", "severity": "review",
                                   "rule": "vendor_unknown",
                                   "message": f"{name!r} is not in the vendor master"})  # fmt: skip
                elif vendor["tax_id_matches"] is False:
                    issues.append({"field_path": f"{party}.tax_id", "severity": "review",
                                   "rule": "vendor_tax_id_mismatch",
                                   "message": f"tax ID {tax_id!r} differs from the vendor "
                                   f"master ({vendor['vendor']['tax_id']})"})  # fmt: skip
        return {"issues": issues, "record": _plain(doc_type, extracted),
                "cost_usd": _add_cost(state, ctx)}  # fmt: skip

    def after_classify(state: IngestState) -> str:
        return "reject_unknown" if state["doc_type"] == "unknown" else "extract"

    def after_validate(state: IngestState) -> str:
        issues = blocking(state["issues"])
        if not issues:
            return "save"
        can_retry = state.get("attempts", 0) < cfg.max_reextract_attempts
        if reextract_paths(issues) and can_retry and not state.get("stalled"):
            return "reextract"
        return "human_review"

    async def reextract(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        paths = reextract_paths(blocking(state["issues"]))
        extracted = dict(state["extracted"])
        notes = list(state.get("notes", []))
        stalled = False
        async with tracer.node("reextract", run_id, sid) as ctx:
            try:
                out = await ctx.call("reextract_fields", document_id=state["document_id"],
                                     doc_type=state["doc_type"], field_paths=paths,
                                     model_tier="strong", session_id=state["session_id"],
                                     run_id=state["run_id"])  # fmt: skip
            except ToolCallError as exc:
                notes.append(f"Escalation to the strong tier failed ({exc.code}); "
                             "sent to human review.")  # fmt: skip
                stalled = True
            else:
                changed: list[str] = []
                for path, field in out["fields"].items():
                    if field["value"] is None and field["confidence"] == 0:
                        continue  # strong tier couldn't read it; keep the first reading
                    if get_path(extracted, path)["value"] != field["value"]:
                        changed.append(path)
                    set_path(extracted, path, field)
                if not changed:
                    stalled = True
                    notes.append("Strong tier re-read " + ", ".join(paths) +
                                 " and got the same values: the document itself is "
                                 "inconsistent.")  # fmt: skip
                else:
                    notes.append("Strong tier corrected " + ", ".join(changed) + ".")
        return {"extracted": extracted, "attempts": state.get("attempts", 0) + 1,
                "escalated": True, "stalled": stalled, "notes": notes,
                "cost_usd": _add_cost(state, ctx)}  # fmt: skip

    async def human_review(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        async with tracer.node("human_review", run_id, sid):
            # LangGraph re-runs this node from the top on resume, and interrupt() then returns
            # the decision. Mark the run paused only when it really pauses, so a resume never
            # flips the status back to awaiting_review (which would accept a second resume).
            try:
                decision = interrupt(
                    {
                        "run_id": state["run_id"],
                        "doc_type": state["doc_type"],
                        "record": state["record"],
                        "issues": blocking(state["issues"]),
                        "notes": state.get("notes", []),
                    },
                    response_schema=ReviewDecision,
                )
            except GraphInterrupt:
                await tracer.sink.set_run(run_id, sid, status="awaiting_review")
                raise
            await tracer.sink.set_run(run_id, sid, status="running")
            record = state["record"]
            actions: list[ReviewAction] = []
            if decision.action == "edit" and decision.record is not None:
                new = _RECORDS[state["doc_type"]][1].model_validate(decision.record)
                record = new.model_dump(mode="json")
                before, after = flatten(state["record"]), flatten(record)
                actions = [ReviewAction(p, before.get(p), after.get(p), "edit")
                           for p in sorted(set(before) | set(after))
                           if before.get(p) != after.get(p)]  # fmt: skip
            if not actions:
                actions = [ReviewAction("", None, None, "approve")]
            await tracer.sink.review(run_id, sid, actions)
        edits = [{"path": a.field_path, "old": a.old_value, "new": a.new_value}
                 for a in actions if a.action == "edit"]  # fmt: skip
        return {"record": record, "reviewed": True, "review_edits": edits}

    async def validate_final(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        async with tracer.node("validate_final", run_id, sid) as ctx:
            result = await ctx.call("validate_record", doc_type=state["doc_type"],
                                    record=state["record"])  # fmt: skip
        # The reviewer has the last word: remaining issues are reported, not blocking.
        return {"final_issues": list(result["issues"])}

    async def save(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        async with tracer.node("save", run_id, sid) as ctx:
            out = await ctx.call("save_record", session_id=state["session_id"],
                                 document_id=state["document_id"], doc_type=state["doc_type"],
                                 record=state["record"])  # fmt: skip
        return {"record_id": str(out["record_id"])}

    def after_save(state: IngestState) -> str:
        if state["doc_type"] == "invoice" and state["record"].get("po_number"):
            return "po_match"
        return "summarize"

    async def po_match(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        record = state["record"]
        async with tracer.node("po_match", run_id, sid) as ctx:
            lookup = await ctx.call("get_purchase_order", session_id=state["session_id"],
                                    po_number=record["po_number"],
                                    exclude_invoice_id=state["record_id"],
                                    exclude_invoice_number=record["invoice_number"])  # fmt: skip
            if not lookup["found"]:
                missing = f"no purchase order {record['po_number']} on file"
                issues = [{"rule": "po_not_found", "invoice_line": None, "po_line": None,
                           "message": missing}]  # fmt: skip
                match: Json = {"status": "po_not_found", "issues": issues, "line_matches": []}
                po_id = None
            else:
                match = await ctx.call(
                    "match_invoice_to_po", invoice=record, purchase_order=lookup["purchase_order"],
                    prior_invoices=[p["record"] for p in lookup["prior_invoices"]])  # fmt: skip
                po_id = lookup["po_id"]
            await ctx.call("save_match_result", session_id=state["session_id"],
                           invoice_id=state["record_id"], po_id=po_id, status=match["status"],
                           issues=match["issues"])  # fmt: skip
        match = {**match, "po_number": record["po_number"],
                 "duplicates": lookup.get("duplicate_invoice_ids", [])}  # fmt: skip
        return {"match": match}

    async def summarize(state: IngestState) -> IngestState:
        run_id, sid = _ids(state)
        async with tracer.node("summarize", run_id, sid):
            r = state["record"]
            number = r.get("invoice_number") or r.get("po_number")
            label = "Invoice" if state["doc_type"] == "invoice" else "Purchase order"
            lines = [f"{label} {number} saved: {r.get('currency')} {r.get('total')}."]
            if state.get("reviewed"):
                edits = state.get("review_edits", [])
                lines.append(f"Human review: {len(edits)} field(s) edited." if edits
                             else "Human review: approved as extracted.")  # fmt: skip
            lines += state.get("notes", [])
            match = state.get("match")
            if match:
                rules = ", ".join(i["rule"] for i in match["issues"]) or "no issues"
                lines.append(f"PO {match['po_number']}: {match['status']} ({rules}).")
                if match.get("duplicates"):
                    times = len(match["duplicates"])
                    lines.append(f"Note: this invoice number was already saved {times} "
                                 "time(s) in this session (re-submission); earlier copies "
                                 "were not counted twice.")  # fmt: skip
            total = Decimal(state.get("cost_usd", "0"))
            lines.append(f"Cost ${total} · escalated: {'yes' if state.get('escalated') else 'no'}.")
            summary = " ".join(lines)
            result = result_of({**state, "summary": summary, "cost_usd": str(total)})
            # Result and final status in one update: a reader never sees "completed" without it.
            await tracer.sink.set_run(run_id, sid, status="completed", total_cost_usd=total,
                                      escalated=bool(state.get("escalated")),
                                      result_json=result,
                                      finished_at=datetime.now(UTC))  # fmt: skip
        return {"summary": summary}

    g = StateGraph(IngestState)
    for name, fn in (
        ("classify", classify),
        ("reject_unknown", reject_unknown),
        ("extract", extract),
        ("validate", validate),
        ("reextract", reextract),
        ("human_review", human_review),
        ("validate_final", validate_final),
        ("save", save),
        ("po_match", po_match),
        ("summarize", summarize),
    ):
        g.add_node(name, fn)  # pyright: ignore[reportUnknownMemberType]
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", after_classify, ["reject_unknown", "extract"])
    g.add_edge("reject_unknown", END)
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", after_validate, ["save", "reextract", "human_review"])
    g.add_edge("reextract", "validate")
    g.add_edge("human_review", "validate_final")
    g.add_edge("validate_final", "save")
    g.add_conditional_edges("save", after_save, ["po_match", "summarize"])
    g.add_edge("po_match", "summarize")
    g.add_edge("summarize", END)
    return g.compile(checkpointer=checkpointer)  # pyright: ignore[reportUnknownMemberType]
