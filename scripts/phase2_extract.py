"""Phase 2 'Done when' demo: extract sample documents end-to-end through the gateway.

For each PDF: render pages + text layer, call the default tier with the extraction schema,
compare the result with the ground truth, and record cost in the Postgres ledger. Then show
the ledger rows and prove the session budget blocks further calls.

    uv run --env-file .env python scripts/phase2_extract.py [--report out.md] PDF...

Needs DATABASE_URL (owner, to create a session row), DATABASE_APP_URL (ledger writes),
GEMINI_API_KEY and the budget variables. Real API calls: costs a few cents at most.
"""

import argparse
import asyncio
import io
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg

from docagent_gateway import (
    BudgetExceeded,
    Gateway,
    ImageInput,
    PostgresLedger,
    load_config,
)
from docagent_mcp_extraction.pages import render_pages
from docagent_mcp_extraction.prompts import extraction_messages
from docagent_schemas import GroundTruth, Invoice, PurchaseOrder

DEFAULT_DOCS = ["evals/dataset/samples/po_003.pdf", "evals/dataset/samples/inv_003.pdf"]


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in cast(dict[str, Any], value).items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(cast(list[Any], value)):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: value}


def _norm(v: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v).lower())


def compare(got: Any, want: Any) -> str:
    """'=' exact, '~' equal after normalisation (case/punctuation/number format), 'x' differs."""
    if got == want:
        return "="
    try:
        if got is not None and want is not None and Decimal(str(got)) == Decimal(str(want)):
            return "="
    except InvalidOperation:
        pass
    if got is not None and want is not None and _norm(got) == _norm(want):
        return "~"
    return "x"


async def extract_one(gw: Gateway, pdf: Path, session_id: Any) -> tuple[str, list[str]]:
    truth_json, pdf_bytes = await asyncio.to_thread(
        lambda: (pdf.with_suffix(".json").read_text(encoding="utf-8"), pdf.read_bytes())
    )
    truth = GroundTruth.model_validate_json(truth_json)
    pages = render_pages(pdf_bytes)
    schema = Invoice if truth.doc_type == "invoice" else PurchaseOrder
    out = await gw.complete(
        extraction_messages(truth.doc_type, pages),
        tier="default",
        response_schema=schema,
        images=[ImageInput(p.png) for p in pages],
        session_id=session_id,
    )
    if out.parsed is None:
        raise RuntimeError("gateway returned no parsed object")
    extracted = flatten(out.parsed.to_record().model_dump(mode="json"))
    expected = flatten(truth.record.model_dump(mode="json"))
    confidences = {
        k.removesuffix(".confidence"): v
        for k, v in flatten(out.parsed.model_dump(mode="json")).items()
        if k.endswith(".confidence")
    }

    rows: list[str] = []
    counts = {"=": 0, "~": 0, "x": 0}
    for key in sorted(set(extracted) | set(expected), key=_sort_key):
        mark = compare(extracted.get(key), expected.get(key))
        counts[mark] += 1
        flag = {"=": "", "~": "≈ format only", "x": "**✗ DIFFERS**"}[mark]
        conf = confidences.get(key)
        rows.append(
            f"| `{key}` | {_cell(extracted.get(key), mark == 'x')} | "
            f"{_cell(expected.get(key), mark == 'x')} | "
            f"{'' if conf is None else f'{conf:.2f}'} | {flag} |"
        )
    header = (
        f"### {pdf.name} ({truth.doc_type}, template `{truth.template}`, "
        f"{'scanned' if truth.scanned else 'clean'})\n\n"
        f"model `{out.model}` · calls {out.calls} · tokens in/out {out.tokens_in}/"
        f"{out.tokens_out} · cost ${out.cost_usd} · {out.latency_ms} ms · "
        f"fields: {counts['=']} exact, {counts['~']} format-only, {counts['x']} differ\n\n"
        "| field | extracted | ground truth | conf | |\n|---|---|---|---|---|"
    )
    return header, rows


def _sort_key(key: str) -> tuple[int, str]:
    m = re.search(r"\[(\d+)\]", key)
    return (int(m.group(1)) + 1 if m else 0, key)


def _cell(v: Any, bold: bool) -> str:
    s = "∅" if v is None else str(v).replace("|", "\\|")
    return f"**{s}**" if bold else s


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="*", default=DEFAULT_DOCS)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    owner_dsn = os.environ["DATABASE_URL"]
    ledger = PostgresLedger(os.environ.get("DATABASE_APP_URL", owner_dsn))
    config = load_config()
    gw = Gateway(config, ledger)

    session_id = uuid4()
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO sessions (id) VALUES (%s)", (session_id,))

    sections: list[str] = [f"Session `{session_id}`\n"]
    for pdf in map(Path, args.pdfs):
        header, rows = await extract_one(gw, pdf, session_id)
        sections.append(header + "\n" + "\n".join(rows) + "\n")

    with psycopg.connect(owner_dsn) as conn:
        ledger_rows = conn.execute(
            "SELECT id, ts, model, tokens_in, tokens_out, cost_usd FROM llm_ledger "
            "WHERE session_id = %s ORDER BY id",
            (session_id,),
        ).fetchall()
    table = [
        "### llm_ledger rows for this session\n",
        "| id | ts (UTC) | model | tokens_in | tokens_out | cost_usd |",
        "|---|---|---|---|---|---|",
    ]
    total = Decimal(0)
    for rid, ts, model, tin, tout, cost in ledger_rows:
        table.append(f"| {rid} | {ts:%Y-%m-%d %H:%M:%S} | {model} | {tin} | {tout} | {cost} |")
        total += cost
    table.append(f"| | | **total** | | | **{total}** |")
    sections.append("\n".join(table) + "\n")

    # Live budget check: cap this session at what it has already spent -> next call refused
    # before any request leaves the process.
    capped = Gateway(
        config.model_copy(
            update={"budgets": config.budgets.model_copy(update={"session_usd": total})}
        ),
        ledger,
    )
    try:
        await capped.complete(
            [{"role": "user", "content": "hi"}], tier="default", session_id=session_id
        )
        sections.append("### Budget check\n\nUNEXPECTED: call was allowed\n")
    except BudgetExceeded as exc:
        sections.append(f"### Budget check\n\n`BudgetExceeded({exc.scope!r})`: {exc}\n")

    report = "\n".join(sections)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    stdout: Any = sys.stdout
    if isinstance(stdout, io.TextIOWrapper):
        stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
