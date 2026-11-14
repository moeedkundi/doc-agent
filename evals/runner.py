"""Run the ingest graph over the synthetic dataset and score it against ground truth.

    uv run --env-file .env python -m evals.runner --suite smoke     # the 6 public samples
    uv run --env-file .env python -m evals.runner --suite full      # the eval set (65 docs)
    uv run --env-file .env python -m evals.runner --suite smoke \\
        --baseline evals/reports/smoke_baseline.json --max-drop 5    # CI gate

Each run uses a fresh session marked `purpose='eval'`, so its spend goes to the eval budget
(EVAL_BUDGET_USD), never the public demo's daily budget. Documents are processed in manifest
order (every PO before its invoices). Reviews are approved automatically and counted.
Needs the four MCP servers running (see their READMEs) and the database migrated.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from langgraph.types import Command
from psycopg.rows import dict_row

from docagent_agent.runtime import (
    AgentConfig,
    Runtime,
    create_run,
    open_runtime,
    register_document,
    run_async,
)
from docagent_schemas import GroundTruth
from evals.scoring import DocOutcome, report

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "dataset"
REPORTS = ROOT / "evals" / "reports"
Suite = Literal["smoke", "full"]
GATED = ("field_accuracy",)  # match status over 3 smoke invoices is too coarse to gate on


def load_suite(suite: Suite, only: list[str] | None = None) -> list[GroundTruth]:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    split = "sample" if suite == "smoke" else "eval"
    truths = [
        GroundTruth.model_validate_json((DATASET / d["ground_truth"]).read_text("utf-8"))
        for d in manifest["documents"]
        if d["split"] == split and (not only or d["doc_id"] in only)
    ]
    # Manifest order already puts POs first; keep it explicit.
    return sorted(truths, key=lambda t: t.doc_type != "purchase_order")


def create_eval_session(cfg: AgentConfig) -> UUID:
    sid = uuid4()
    with psycopg.connect(cfg.app_dsn, autocommit=True) as conn, conn.transaction():
        conn.execute("SELECT set_config('app.session_id', %s, true)", (str(sid),))
        conn.execute("INSERT INTO sessions (id, purpose) VALUES (%s, 'eval')", (sid,))
    return sid


async def run_one(
    rt: Runtime, cfg: AgentConfig, sid: UUID, gt: GroundTruth, db: psycopg.Cursor[dict[str, Any]]
) -> DocOutcome:
    doc = register_document(cfg, sid, DATASET / gt.file)
    run = create_run(cfg, sid, doc)
    config: dict[str, Any] = {"configurable": {"thread_id": str(run)}}
    inp: Any = {
        "run_id": str(run),
        "session_id": str(sid),
        "document_id": str(doc),
        "cost_usd": "0",
    }
    reviewed = False
    error: str | None = None
    started = time.perf_counter()
    try:
        while True:
            await rt.graph.ainvoke(inp, config, durability="sync")  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
            snap = await rt.graph.aget_state(config)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
            if not snap.interrupts:
                break
            reviewed = True  # counted, then approved as extracted
            inp = Command(resume={"action": "approve"})
    except Exception as exc:  # a failed run scores as all-wrong; keep going
        error = f"{type(exc).__name__}: {exc}"[:300]
    latency = time.perf_counter() - started
    row = (
        db.execute(
            "SELECT status, result_json, total_cost_usd, escalated, error_code "
            "FROM runs WHERE id = %s",
            (run,),
        ).fetchone()
        or {}
    )
    result: dict[str, Any] = row.get("result_json") or {}
    match: dict[str, Any] = result.get("match") or {}
    expected = gt.expected_match
    return DocOutcome(
        doc_id=gt.doc_id,
        doc_type=gt.doc_type,
        scanned=gt.scanned,
        variant_of=gt.variant_of,
        status=str(row.get("status") or "failed"),
        predicted_doc_type=result.get("doc_type"),
        expected_record=gt.record.model_dump(mode="json"),
        predicted_record=result.get("record"),
        expected_match=expected.status if expected else None,
        expected_rules=list(expected.rules) if expected else [],
        predicted_match=match.get("status"),
        predicted_rules=[i["rule"] for i in match.get("issues", [])],
        injected=[i.kind for i in gt.injected],
        escalated=bool(row.get("escalated")),
        reviewed=reviewed,
        cost_usd=Decimal(str(row.get("total_cost_usd") or 0)),
        latency_s=round(latency, 3),
        error_code=row.get("error_code") or (error and "exception"),
    )


def model_usage(db: psycopg.Cursor[dict[str, Any]], sid: UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT model, count(*) AS calls, sum(tokens_in) AS tokens_in, "
        "sum(tokens_out) AS tokens_out, sum(cost_usd) AS cost_usd FROM llm_ledger "
        "WHERE session_id = %s GROUP BY model ORDER BY model",
        (sid,),
    ).fetchall()
    return [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def to_markdown(rep: dict[str, Any]) -> str:
    m = rep["metrics"]
    cs = m["clean_vs_scanned"]
    lines = [
        f"# DocAgent eval report: {rep['suite']} ({m['documents']} documents)",
        "",
        f"Run {rep['finished_at']} · models: default `{rep['models']['default']}`, strong "
        f"`{rep['models']['strong']}` · session `{rep['session_id']}`",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Field accuracy | {_pct(m['field_accuracy'])} |",
        f"| Classification accuracy | {_pct(m['classification_accuracy'])} |",
        *(
            f"| F1, {t} | {v['f1']:.3f} (P {v['precision']:.3f}, R {v['recall']:.3f}) |"
            for t, v in m["by_doc_type"].items()
        ),
        f"| Accuracy, clean documents | {_pct(cs['clean']['field_accuracy'])} "
        f"({cs['clean']['documents']}) |",
        f"| Accuracy, scanned documents | {_pct(cs['scanned']['field_accuracy'])} "
        f"({cs['scanned']['documents']}) |",
        f"| Same content, clean vs scanned ({cs['paired']['pairs']} pairs) | "
        f"{_pct(cs['paired']['clean_twins'])} vs {_pct(cs['paired']['scanned_variants'])} |",
        f"| Match-status accuracy | {_pct(m['match_status_accuracy'])} |",
        f"| Mismatch recall | {_pct(m['mismatch_recall'])} |",
        f"| Escalation rate | {_pct(m['escalation_rate'])} |",
        f"| Review rate | {_pct(m['review_rate'])} |",
        f"| Cost per document | avg ${m['cost_per_doc_usd']['avg'] or 0:.4f}, "
        f"max ${m['cost_per_doc_usd']['max'] or 0:.4f} "
        f"(total ${m['cost_per_doc_usd']['total']:.4f}) |",
        f"| Latency per document | p50 {m['latency_s']['p50'] or 0:.1f}s, "
        f"p95 {m['latency_s']['p95'] or 0:.1f}s |",
        f"| Failed runs | {m['failed_runs']} |",
        "",
        "## Mismatch recall by injected kind",
        "",
        "| Kind | Recall |",
        "|---|---|",
        *(f"| {k} | {_pct(v)} |" for k, v in m["mismatch_recall_by_kind"].items()),
        "",
        "## By model",
        "",
        "| Model | Calls | Tokens in/out | Cost |",
        "|---|---|---|---|",
        *(
            f"| {u['model']} | {u['calls']} | {u['tokens_in']}/{u['tokens_out']} | "
            f"${Decimal(u['cost_usd']):.4f} |"
            for u in m["by_model"]
        ),
        "",
        "## Per-field accuracy",
        "",
        "| Field | Accuracy |",
        "|---|---|",
        *(f"| `{k}` | {_pct(v)} |" for k, v in m["per_field_accuracy"].items()),
        "",
        "## Documents with errors",
        "",
    ]
    wrong = [
        d
        for d in rep["documents"]
        if d["wrong_fields"]
        or d["status"] != "completed"
        or d["predicted_match"] != d["expected_match"]
    ]
    if not wrong:
        lines.append("None.")
    for d in wrong:
        fields = "; ".join(
            f"`{w['path']}` {w['expected']!r} → {w['predicted']!r}" for w in d["wrong_fields"][:6]
        )
        match = (
            f" match {d['predicted_match']} (expected {d['expected_match']})"
            if d["predicted_match"] != d["expected_match"]
            else ""
        )
        lines.append(
            f"- **{d['doc_id']}**{' (scanned)' if d['scanned'] else ''}: "
            f"{fields or d['status']}{match}"
        )
    return "\n".join(lines) + "\n"


async def main(args: argparse.Namespace) -> int:
    cfg = AgentConfig.from_env()
    if not os.environ.get("EVAL_BUDGET_USD"):
        print("EVAL_BUDGET_USD is not set; evals use their own budget.", file=sys.stderr)
        return 2
    truths = load_suite(args.suite, args.docs)
    sid = create_eval_session(cfg)
    print(f"{args.suite} eval: {len(truths)} documents, session {sid}", flush=True)
    db = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True).cursor(row_factory=dict_row)
    outcomes: list[DocOutcome] = []
    async with open_runtime(cfg) as rt:
        tiers = rt.tracer.gateway.config.models.tiers if rt.tracer.gateway else {}
        for i, gt in enumerate(truths, 1):
            o = await run_one(rt, cfg, sid, gt, db)
            outcomes.append(o)
            print(
                f"[{i:>2}/{len(truths)}] {gt.doc_id:<12} {o.status:<9} "
                f"{o.latency_s:>5.1f}s ${o.cost_usd}  match={o.predicted_match}"
                f"{'  REVIEWED' if o.reviewed else ''}{'  ESCALATED' if o.escalated else ''}"
                f"{'  ' + str(o.error_code) if o.error_code else ''}",
                flush=True,
            )
    metrics = report(outcomes, model_usage(db, sid))
    rep: dict[str, Any] = {
        "suite": args.suite,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": str(sid),
        "models": {name: t.model for name, t in tiers.items()},
        "metrics": metrics,
        "documents": [
            {
                **{
                    k: v
                    for k, v in asdict(o).items()
                    if k not in ("score", "expected_record", "predicted_record")
                },
                "cost_usd": str(o.cost_usd),
                "field_accuracy": (o.score.correct / o.score.total)
                if o.score and o.score.total
                else None,
                "wrong_fields": [
                    {"path": f.path, "expected": f.expected, "predicted": f.predicted}
                    for f in (o.score.fields if o.score else [])
                    if not f.correct
                ],
            }
            for o in outcomes
        ],
    }
    out_dir = Path(args.out)
    stem = args.name or ("latest" if args.suite == "full" else "smoke")
    await asyncio.to_thread(write_reports, out_dir, stem, rep)
    print(
        f"\nfield accuracy {_pct(metrics['field_accuracy'])} · match status "
        f"{_pct(metrics['match_status_accuracy'])} · mismatch recall "
        f"{_pct(metrics['mismatch_recall'])} · wrote {out_dir / stem}.json/.md"
    )
    return gate(metrics, args.baseline, args.max_drop)


def write_reports(out_dir: Path, stem: str, rep: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(rep, indent=2, default=str)
    (out_dir / f"{stem}.json").write_text(text + "\n", encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(to_markdown(rep), encoding="utf-8")


def gate(metrics: dict[str, Any], baseline_path: str | None, max_drop: float) -> int:
    """Fail (exit 1) when a gated metric falls more than `max_drop` points below baseline."""
    if not baseline_path:
        return 0
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    failed = False
    for key in GATED:
        now, then = metrics.get(key), baseline.get(key)
        if now is None or then is None:
            continue
        drop = (then - now) * 100
        ok = drop <= max_drop
        failed |= not ok
        print(
            f"gate {key}: {now * 100:.1f}% vs baseline {then * 100:.1f}% "
            f"({'-' if drop > 0 else '+'}{abs(drop):.1f} pts) {'OK' if ok else 'FAIL'}"
        )
    return 1 if failed else 0


def cli() -> int:
    parser = argparse.ArgumentParser(description="DocAgent evals")
    parser.add_argument("--suite", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--docs", nargs="*", help="only these doc_ids")
    parser.add_argument("--out", default=str(REPORTS))
    parser.add_argument("--name", help="report file stem (default: latest for full, smoke)")
    parser.add_argument("--baseline", help="baseline JSON with field_accuracy etc. (0..1)")
    parser.add_argument("--max-drop", type=float, default=5.0, help="allowed drop in points")
    stdout: Any = sys.stdout
    stdout.reconfigure(encoding="utf-8")
    return run_async(main(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(cli())
