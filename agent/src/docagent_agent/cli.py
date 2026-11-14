"""Command-line driver for the ingest graph.

    uv run python -m agent.cli session new
    uv run python -m agent.cli ingest <file> [--session <uuid>]
    uv run python -m agent.cli resume <run_id> [--approve | --set PATH=VALUE ...]
    uv run python -m agent.cli ask "<question>" --session <uuid> [--trace]

`ingest` prints every trace event as it is written. When the graph pauses for human review
it shows the flagged fields and asks you to approve or edit them, then resumes. If there is
no interactive terminal (or you choose "later"), the run stays paused in the Postgres
checkpointer and `resume` picks it up, even from another process.

Needs the four MCP servers running and the variables from .env
(run with `uv run --env-file .env ...`).
"""

import argparse
import io
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from langgraph.types import Command
from pydantic import ValidationError

from docagent_agent.ingest import ReviewDecision, flatten, get_path, set_path
from docagent_agent.runtime import (
    AgentConfig,
    Runtime,
    create_ask_run,
    create_run,
    create_session,
    open_runtime,
    register_document,
    run_async,
    session_exists,
)
from docagent_agent.trace import TraceEvent

Json = dict[str, Any]


# -- live trace -------------------------------------------------------------------------------


def _fmt_event(e: TraceEvent) -> str | None:
    ts = e.ts.astimezone().strftime("%H:%M:%S")
    secs = f"{e.latency_ms / 1000:.1f}s" if e.latency_ms is not None else ""
    tokens = f"{e.tokens_in}->{e.tokens_out} tok" if e.tokens_in else ""
    cost = f"${e.cost_usd}" if e.cost_usd else ""
    if e.kind == "node_start":
        return f"{ts}  > {e.node}"
    if e.kind == "tool_end":
        detail = "  ".join(x for x in (e.model or "", tokens, secs, cost) if x)
        return f"{ts}      . {e.tool}  {detail}".rstrip()
    if e.kind == "node_end":
        detail = "  ".join(x for x in (secs, cost) if x)
        return f"{ts}  ok {e.node}  {detail}".rstrip()
    if e.kind == "interrupt":
        return f"{ts}  || {e.node}: paused for human review"
    return f"{ts}  !! {e.node}: {e.error}"


def _print_event(e: TraceEvent) -> None:
    line = _fmt_event(e)
    if line:
        print(line, flush=True)


# -- review ------------------------------------------------------------------------------------


def _show_review(payload: Json) -> None:
    print("\n" + "=" * 72)
    print(f"Review needed for {payload['doc_type']} (run {payload['run_id']})")
    for issue in payload["issues"]:
        print(f"  ! {issue['field_path'] or '(record)'}: {issue['message']}  [{issue['rule']}]")
    for note in payload.get("notes", []):
        print(f"  - {note}")
    record = payload["record"]
    flagged = {i["field_path"] for i in payload["issues"] if i["field_path"]}
    shown = sorted(flagged | {"subtotal", "tax_amount", "total", "currency"})
    print("\n  Current values:")
    for path in shown:
        try:
            print(f"    {path:<28} {get_path(record, path)}")
        except (KeyError, IndexError, TypeError):
            continue
    print("=" * 72)


def _edit_record(doc_type: str, record: Json, edits: list[tuple[str, str]]) -> Json:
    from docagent_schemas import InvoiceRecord, PurchaseOrderRecord

    updated = json.loads(json.dumps(record))
    known = flatten(record)
    for path, value in edits:
        if path not in known:
            raise KeyError(f"unknown field path {path!r}")
        set_path(updated, path, None if value.strip().lower() in ("", "null") else value)
    model = InvoiceRecord if doc_type == "invoice" else PurchaseOrderRecord
    return model.model_validate(updated).model_dump(mode="json")


def _ask(payload: Json) -> ReviewDecision | None:
    """Interactive review. None means: leave the run paused."""
    _show_review(payload)
    while True:
        choice = input("[a]pprove as is, [e]dit fields, or [l]ater (stay paused)? ").strip().lower()
        if choice in ("a", "approve"):
            return ReviewDecision(action="approve")
        if choice in ("l", "later", "q"):
            return None
        if choice in ("e", "edit"):
            edits: list[tuple[str, str]] = []
            while True:
                path = input("  field path to change (blank when done): ").strip()
                if not path:
                    break
                try:
                    current = get_path(payload["record"], path)
                except (KeyError, IndexError, TypeError):
                    print(f"  no field {path!r}")
                    continue
                value = input(f"  new value for {path} [{current}]: ")
                edits.append((path, value))
            try:
                record = _edit_record(payload["doc_type"], payload["record"], edits)
            except (KeyError, ValidationError) as exc:
                print(f"  could not apply edits: {exc}")
                continue
            return ReviewDecision(action="edit", record=record)


def _decision_from_flags(payload: Json, approve: bool, sets: list[str]) -> ReviewDecision | None:
    if approve:
        return ReviewDecision(action="approve")
    if sets:
        pairs = [tuple(s.split("=", 1)) for s in sets]
        if any(len(p) != 2 for p in pairs):
            raise SystemExit("--set expects PATH=VALUE")
        edits = [(p[0].strip(), p[1]) for p in pairs]
        return ReviewDecision(
            action="edit", record=_edit_record(payload["doc_type"], payload["record"], edits)
        )
    return None


# -- driving the graph ---------------------------------------------------------------------------


async def _pending(rt: Runtime, config: Json) -> Json | None:
    snapshot = await rt.graph.aget_state(config)  # pyright: ignore[reportArgumentType]
    for interrupt in snapshot.interrupts:
        return dict(interrupt.value)
    return None


async def _drive(
    rt: Runtime,
    run_id: UUID,
    first_input: Any,
    *,
    interactive: bool,
    approve: bool = False,
    sets: list[str] | None = None,
) -> int:
    config: Json = {"configurable": {"thread_id": str(run_id)}}
    rt.tracer.listen(_print_event)
    next_input = first_input
    while True:
        try:
            # durability="sync": each step's checkpoint is stored before the next step runs,
            # so a checkpoint failure stops the run before any later side effects.
            await rt.graph.ainvoke(next_input, config, durability="sync")  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
        except Exception as exc:
            state = (await rt.graph.aget_state(config)).values  # pyright: ignore[reportArgumentType]
            await rt.tracer.sink.set_run(run_id, UUID(state["session_id"]), status="failed")
            print(f"\nRun failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        payload = await _pending(rt, config)
        if payload is None:
            break
        decision = _decision_from_flags(payload, approve, sets or [])
        approve, sets = False, []  # flags apply to the first pause only
        if decision is None and interactive and sys.stdin.isatty():
            decision = _ask(payload)
        elif decision is None:
            _show_review(payload)
        if decision is None:
            print(f"\nPaused for review. Resume with:\n"
                  f"  uv run --env-file .env python -m agent.cli resume {run_id}")  # fmt: skip
            return 0
        print(f"\nResuming with: {decision.action}")
        next_input = Command(resume=decision.model_dump())

    state: Json = dict((await rt.graph.aget_state(config)).values)  # pyright: ignore[reportArgumentType]
    print("\n" + state.get("summary", "(no summary)"))
    cost = Decimal(state.get("cost_usd", "0"))
    print(f"run {run_id} · total cost ${cost} · escalated: "
          f"{'yes' if state.get('escalated') else 'no'}")  # fmt: skip
    return 0


async def _ingest(args: argparse.Namespace) -> int:
    cfg = AgentConfig.from_env()
    if args.session:
        session_id = UUID(args.session)
        if not session_exists(cfg, session_id):
            raise SystemExit(f"session {session_id} does not exist")
    else:
        session_id = create_session(cfg)
        print(f"new session {session_id}")
    doc_id = register_document(cfg, session_id, Path(args.file))
    run_id = create_run(cfg, session_id, doc_id)
    print(f"ingest {args.file} · document {doc_id} · run {run_id}\n")
    async with open_runtime(cfg) as rt:
        first = {"run_id": str(run_id), "session_id": str(session_id),
                 "document_id": str(doc_id), "cost_usd": "0"}  # fmt: skip
        return await _drive(rt, run_id, first, interactive=not args.no_input)


async def _resume(args: argparse.Namespace) -> int:
    cfg = AgentConfig.from_env()
    run_id = UUID(args.run_id)
    async with open_runtime(cfg) as rt:
        config: Json = {"configurable": {"thread_id": str(run_id)}}
        payload = await _pending(rt, config)
        if payload is None:
            raise SystemExit(f"run {run_id} is not waiting for review")
        decision = _decision_from_flags(payload, args.approve, args.set or [])
        if decision is None:
            if not sys.stdin.isatty():
                _show_review(payload)
                raise SystemExit("no terminal: pass --approve or --set PATH=VALUE")
            decision = _ask(payload)
            if decision is None:
                print("Still paused.")
                return 0
        print(f"Resuming run {run_id} with: {decision.action}\n")
        return await _drive(rt, run_id, Command(resume=decision.model_dump()), interactive=True)


def _table(columns: list[str], rows: list[list[Any]], limit: int = 20) -> str:
    cells = [[("" if v is None else str(v)) for v in row] for row in rows[:limit]]
    widths = [min(48, max([len(c), *(len(r[i]) for r in cells)])) for i, c in enumerate(columns)]

    def fmt(vals: list[str]) -> str:
        return " | ".join(v[:48].ljust(w) for v, w in zip(vals, widths, strict=True))

    lines = [fmt(columns), "-+-".join("-" * w for w in widths), *(fmt(r) for r in cells)]
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows")
    return "\n".join("    " + line for line in lines)


async def _ask_command(args: argparse.Namespace) -> int:
    cfg = AgentConfig.from_env()
    session_id = UUID(args.session)
    if not session_exists(cfg, session_id):
        raise SystemExit(f"session {session_id} does not exist")
    run_id = create_ask_run(cfg, session_id, args.question)
    print(f"Q: {args.question}")
    print(f"   (session {session_id}, run {run_id})")
    async with open_runtime(cfg) as rt:
        if args.trace:
            rt.tracer.listen(_print_event)
        config: Json = {"configurable": {"thread_id": f"ask:{session_id}"}}
        try:
            await rt.ask.ainvoke(  # pyright: ignore[reportUnknownMemberType]
                {"run_id": str(run_id), "session_id": str(session_id), "question": args.question},
                config,  # pyright: ignore[reportArgumentType]
                durability="sync",
            )
        except Exception as exc:
            await rt.tracer.sink.set_run(run_id, session_id, status="failed")
            print(f"Ask failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        snapshot = await rt.ask.aget_state(config)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
        state: Json = dict(snapshot.values)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    plan = state["plan"]
    print()
    print(f"Plan: {plan['action']} - {plan['reason']}")
    result = state.get("result")
    print()
    if result:
        print("SQL (as executed):")
        print(f"    {result['sql']}")
        print()
        print(f"Rows ({result['row_count']}{', truncated' if result['truncated'] else ''}):")
        print(_table(result["columns"], result["rows"]))
    elif state.get("sql"):
        err: Json = state.get("sql_error") or {}
        print("SQL (rejected by the safety pipeline):")
        print(f"    {state['sql']}")
        print(f"    -> {err.get('code')}: {err.get('message')}")
    else:
        print("SQL: none")
    conv = state.get("conversion")
    if conv and "error" not in conv:
        print()
        print(
            f"Conversion: {conv['from_currency']} {conv['amount_in']} -> "
            f"{conv['to_currency']} {conv['amount']} (rate {conv['rate']}, {conv['rate_date']})"
        )
    print()
    print(f"Answer: {state['answer']}")
    print(f"(cost ${Decimal(state.get('cost_usd', '0'))})")
    return 0


def main(argv: list[str] | None = None) -> int:
    stdout: Any = sys.stdout
    if isinstance(stdout, io.TextIOWrapper):
        stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    parser = argparse.ArgumentParser(prog="agent.cli", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sess = sub.add_parser("session", help="manage sessions")
    sess.add_argument("action", choices=["new"])
    ing = sub.add_parser("ingest", help="run the ingest graph on a file")
    ing.add_argument("file")
    ing.add_argument("--session", help="existing session id (default: create one)")
    ing.add_argument("--no-input", action="store_true",
                     help="never prompt; leave the run paused if review is needed")  # fmt: skip
    res = sub.add_parser("resume", help="resume a run paused for review")
    res.add_argument("run_id")
    res.add_argument("--approve", action="store_true", help="approve the record as extracted")
    res.add_argument("--set", action="append", metavar="PATH=VALUE",
                     help="edit a field, e.g. --set total=1234.50 (repeatable)")  # fmt: skip
    ask = sub.add_parser("ask", help="ask a question about the session's saved documents")
    ask.add_argument("question")
    ask.add_argument("--session", required=True, help="session id")
    ask.add_argument("--trace", action="store_true", help="print trace events live")
    args = parser.parse_args(argv)
    try:
        if args.command == "session":
            print(create_session(AgentConfig.from_env()))
            return 0
        if args.command == "ingest":
            return run_async(_ingest(args))
        if args.command == "ask":
            return run_async(_ask_command(args))
        return run_async(_resume(args))
    except LookupError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
