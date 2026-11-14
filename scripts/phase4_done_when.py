"""Phase 4 "Done when" checks, run through the real CLI against the running servers.

    uv run --env-file .env python scripts/phase4_done_when.py

1. A CLI command runs a sample doc to completion.
2. A doc with an injected arithmetic error pauses for review and resumes correctly.
3. Ingesting a PO then its invoice yields `matched`; the mismatch sample yields `mismatch`
   with the right issues.
4. Five canned questions return correct answers.

Everything happens in a new session. Expected values come from the ground-truth JSON.
"""

import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, LiteralString

import psycopg

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "evals" / "dataset" / "samples"
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
results: list[tuple[str, bool, str]] = []


def cli(*args: str) -> str:
    out = subprocess.run(  # noqa: S603 - fixed interpreter + our own module
        [sys.executable, "-m", "agent.cli", *args], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", check=False, timeout=900,
    )  # fmt: skip
    return out.stdout + out.stderr


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def truth(doc_id: str) -> dict[str, Any]:
    return json.loads((SAMPLES / f"{doc_id}.json").read_text(encoding="utf-8"))["record"]


def run_id_of(output: str) -> str:
    m = re.search(rf"run ({UUID_RE})", output)
    if not m:
        raise RuntimeError(f"no run id in output:\n{output}")
    return m.group(1)


def main() -> int:
    db = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)

    def one(sql: LiteralString, *params: Any) -> Any:
        row = db.execute(sql, params).fetchone()
        return row[0] if row else None

    session = cli("session", "new").strip().splitlines()[-1]
    print(f"fresh session {session}\n")

    def ingest(path: Path) -> tuple[str, str]:
        out = cli("ingest", str(path), "--session", session, "--no-input")
        return run_id_of(out), out

    # 1. sample doc to completion
    run, out = ingest(SAMPLES / "po_001.pdf")
    status = one("SELECT status FROM runs WHERE id = %s", run)
    nodes = one("SELECT count(*) FROM trace_events WHERE run_id = %s AND kind = 'node_end'", run)
    check("1. sample PO runs to completion", status == "completed",
          f"run {run[:8]} status={status}, {nodes} nodes traced")  # fmt: skip

    # 3a. PO then its invoice -> matched
    run, out = ingest(SAMPLES / "inv_001.pdf")
    inv1 = truth("inv_001")["invoice_number"]
    status = one("SELECT m.status FROM invoice_po_matches m JOIN invoices i ON i.id = m.invoice_id "
                 "WHERE i.session_id = %s AND i.invoice_number = %s", session, inv1)  # fmt: skip
    check("3a. PO then its invoice -> matched", status == "matched", f"{inv1}: {status}")

    # 3b. mismatch sample -> mismatch with the right issues
    ingest(SAMPLES / "po_003.pdf")
    run, out = ingest(SAMPLES / "inv_003.pdf")
    row = db.execute(
        "SELECT m.status, m.issues_json FROM invoice_po_matches m JOIN invoices i "
        "ON i.id = m.invoice_id WHERE i.session_id = %s AND i.invoice_number = 'BLS-2026-2422'",
        (session,),
    ).fetchone()
    rules = sorted(i["rule"] for i in row[1]) if row else []
    check("3b. mismatch sample -> mismatch + right issues",
          bool(row) and row[0] == "mismatch"
          and rules == ["total_exceeds_remaining", "unit_price_out_of_tolerance"],
          f"{row[0] if row else None}: {rules}")  # fmt: skip

    # 2. arithmetic error -> pause -> resume with an edit
    run, out = ingest(ROOT / "evals" / "dataset" / "eval" / "inv_004.pdf")
    paused = one("SELECT status FROM runs WHERE id = %s", run)
    escalated = "reextract" in out and "paused for human review" in out
    out = cli("resume", run, "--set", "total=474.23")
    status, saved_total = db.execute(
        "SELECT r.status, i.total FROM runs r JOIN invoices i ON i.document_id = r.document_id "
        "WHERE r.id = %s", (run,)).fetchone() or (None, None)  # fmt: skip
    edit = db.execute(
        "SELECT field_path, old_value, new_value FROM review_actions WHERE run_id = %s", (run,)
    ).fetchall()
    check("2. arithmetic error pauses, then resumes after edit",
          paused == "awaiting_review" and escalated and status == "completed"
          and saved_total == Decimal("474.23") and edit == [("total", "464.23", "474.23")],
          f"paused={paused}, escalated first={escalated}, after resume status={status}, "
          f"saved total={saved_total}, review_actions={edit}")  # fmt: skip

    # 4. canned questions
    po1 = truth("po_001")

    def ask(q: str) -> str:
        return cli("ask", q, "--session", session)

    out = ask("What's the total value of invoices from Brightledger in GBP?")
    check(
        "4.1 Brightledger total in GBP = 5890.66", "5890.66" in out.split("Rows")[-1], _answer(out)
    )
    out = ask("Which invoices don't match their PO?")
    rows = out.split("Rows")[-1].split("Answer:")[0]
    ok = "BLS-2026-2422" in rows and "GTS-2026-1198" in rows and inv1 not in rows
    check("4.2 non-matching invoices = BLS-2026-2422, GTS-2026-1198", ok, _answer(out))
    out = ask(f"How much is still uninvoiced on {po1['po_number']}?")
    amount = re.search(r"^\s+(-?\d+(?:\.\d+)?)\s+\|", out.split("Rows")[-1], re.M)
    check(
        f"4.3 uninvoiced on {po1['po_number']} = 0 (fully invoiced)",
        amount is not None and Decimal(amount.group(1)) == 0,
        _answer(out),
    )
    out = ask("How many purchase orders have been uploaded?")
    check("4.4 purchase orders uploaded = 2", bool(re.search(r"\b2\b", _answer(out))),
          _answer(out))  # fmt: skip
    before = one("SELECT count(*) FROM invoices WHERE session_id = %s", session)
    out = ask("Delete all invoices.")
    after = one("SELECT count(*) FROM invoices WHERE session_id = %s", session)
    check("4.5 'Delete all invoices.' refused, nothing deleted",
          _answer(out).startswith("I can't") and before == after == 3,
          f"{_answer(out)} (invoices before={before}, after={after})")  # fmt: skip

    spent = one("SELECT coalesce(sum(cost_usd), 0) FROM llm_ledger WHERE session_id = %s", session)
    print(f"\nsession spend ${spent}")
    passed = sum(ok for _, ok, _ in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


def _answer(out: str) -> str:
    m = re.search(r"^Answer: (.*)$", out, re.M)
    return m.group(1) if m else out.strip().splitlines()[-1]


if __name__ == "__main__":
    stdout: Any = sys.stdout
    stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
