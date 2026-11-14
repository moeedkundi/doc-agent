"""Field-level scoring of extracted records against ground truth, and report metrics.

Match policy (also in evals/README.md):
- IDs (invoice_number, po_number, sku, tax_id), dates, currency: exact (surrounding
  whitespace ignored).
- Amounts and quantities (subtotal, tax_amount, total, unit_price, line_total, quantity):
  Decimal-equal ("12.50" == "12.5").
- Names, addresses, descriptions, payment terms: equal after normalising (case, punctuation,
  whitespace), or rapidfuzz ratio >= 90 on the normalised text.
- A null on both sides is correct. Line items are compared by position; missing lines make
  their fields wrong, extra lines count as false positives.
"""

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from rapidfuzz import fuzz

Policy = Literal["exact", "decimal", "text"]
FUZZY_MIN = 90.0

EXACT_FIELDS = frozenset(
    {
        "invoice_number",
        "po_number",
        "sku",
        "tax_id",
        "currency",
        "invoice_date",
        "due_date",
        "po_date",
        "requested_delivery_date",
    }
)
DECIMAL_FIELDS = frozenset(
    {"subtotal", "tax_amount", "total", "unit_price", "line_total", "quantity"}
)
TEXT_FIELDS = frozenset({"name", "address", "description", "ship_to", "payment_terms"})


def policy_for(path: str) -> Policy:
    leaf = path.rsplit(".", 1)[-1]
    if leaf in DECIMAL_FIELDS:
        return "decimal"
    if leaf in TEXT_FIELDS:
        return "text"
    if leaf in EXACT_FIELDS:
        return "exact"
    raise KeyError(f"no match policy for field {path!r}")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold()).split())


def values_match(path: str, expected: Any, predicted: Any) -> bool:
    if expected is None or predicted is None:
        return expected is None and predicted is None
    policy = policy_for(path)
    if policy == "decimal":
        try:
            return Decimal(str(expected)) == Decimal(str(predicted))
        except InvalidOperation:
            return False
    if policy == "exact":
        return str(expected).strip() == str(predicted).strip()
    a, b = _norm(str(expected)), _norm(str(predicted))
    return a == b or fuzz.ratio(a, b) >= FUZZY_MIN


def flatten(record: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(record, dict):
        out: dict[str, Any] = {}
        for k, v in cast(dict[str, Any], record).items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
        return out
    if isinstance(record, list):
        out = {}
        for i, v in enumerate(cast(list[Any], record)):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: record}


def template(path: str) -> str:
    """`line_items[3].unit_price` -> `line_items[].unit_price`."""
    return re.sub(r"\[\d+\]", "[]", path)


@dataclass
class FieldResult:
    path: str
    expected: Any
    predicted: Any
    correct: bool


@dataclass
class DocScore:
    fields: list[FieldResult]
    tp: int
    fp: int
    fn: int

    @property
    def correct(self) -> int:
        return sum(f.correct for f in self.fields)

    @property
    def total(self) -> int:
        return len(self.fields)


def score_record(expected: dict[str, Any], predicted: dict[str, Any] | None) -> DocScore:
    """Compare every ground-truth field; count TP/FP/FN over non-null values for F1."""
    exp = flatten(expected)
    pred = flatten(predicted) if predicted else {}
    results: list[FieldResult] = []
    tp = fp = fn = 0
    for path, want in exp.items():
        got = pred.get(path)
        ok = values_match(path, want, got)
        results.append(FieldResult(path, want, got, ok))
        if want is not None and ok:
            tp += 1
        else:
            if want is not None:
                fn += 1
            if got is not None:
                fp += 1
    for path, got in pred.items():  # fields the ground truth doesn't have (extra lines)
        if path not in exp and got is not None:
            fp += 1
    return DocScore(results, tp, fp, fn)


def f1(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


@dataclass
class DocOutcome:
    """Everything the runner learned about one document."""

    doc_id: str
    doc_type: str
    scanned: bool
    variant_of: str | None
    status: str
    predicted_doc_type: str | None
    expected_record: dict[str, Any]
    predicted_record: dict[str, Any] | None
    expected_match: str | None
    expected_rules: list[str]
    predicted_match: str | None
    predicted_rules: list[str]
    injected: list[str]
    escalated: bool
    reviewed: bool
    cost_usd: Decimal
    latency_s: float
    error_code: str | None = None
    score: DocScore | None = field(default=None, repr=False)


MISMATCH_KINDS = ("price_drift", "over_billed_qty", "wrong_vendor")


def report(outcomes: list[DocOutcome], usage: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-document outcomes into the report's metrics (rates as 0..1)."""
    for o in outcomes:
        o.score = score_record(o.expected_record, o.predicted_record)

    def accuracy(docs: list[DocOutcome]) -> float | None:
        total = sum(d.score.total for d in docs if d.score)
        return sum(d.score.correct for d in docs if d.score) / total if total else None

    per_field: dict[str, list[bool]] = defaultdict(list)
    for o in outcomes:
        for fr in o.score.fields if o.score else []:
            per_field[f"{o.doc_type}.{template(fr.path)}"].append(fr.correct)

    by_type: dict[str, dict[str, Any]] = {}
    for doc_type in sorted({o.doc_type for o in outcomes}):
        docs = [o for o in outcomes if o.doc_type == doc_type]
        tp = sum(d.score.tp for d in docs if d.score)
        fp = sum(d.score.fp for d in docs if d.score)
        fn = sum(d.score.fn for d in docs if d.score)
        by_type[doc_type] = {
            "documents": len(docs),
            "field_accuracy": accuracy(docs),
            **f1(tp, fp, fn),
        }

    variants = [o for o in outcomes if o.variant_of]
    twins = {o.doc_id: o for o in outcomes if o.doc_id in {v.variant_of for v in variants}}

    invoices = [o for o in outcomes if o.doc_type == "invoice"]
    match_ok = [o.predicted_match == o.expected_match for o in invoices]
    mismatches = [o for o in invoices if o.expected_match == "mismatch"]
    caught = [o for o in mismatches if o.predicted_match == "mismatch"]
    by_kind = {
        kind: _rate([o.predicted_match == "mismatch" for o in mismatches if kind in o.injected])
        for kind in MISMATCH_KINDS
    }
    rules_ok = [sorted(o.predicted_rules) == sorted(o.expected_rules) for o in caught]

    costs = [float(o.cost_usd) for o in outcomes]
    latencies = [o.latency_s for o in outcomes]
    return {
        "documents": len(outcomes),
        "failed_runs": sum(o.status != "completed" for o in outcomes),
        "field_accuracy": accuracy(outcomes),
        "classification_accuracy": _rate([o.predicted_doc_type == o.doc_type for o in outcomes]),
        "by_doc_type": by_type,
        "clean_vs_scanned": {
            "clean": {
                "documents": sum(not o.scanned for o in outcomes),
                "field_accuracy": accuracy([o for o in outcomes if not o.scanned]),
            },
            "scanned": {
                "documents": sum(o.scanned for o in outcomes),
                "field_accuracy": accuracy([o for o in outcomes if o.scanned]),
            },
            "paired": {
                "pairs": len(variants),
                "clean_twins": accuracy(list(twins.values())),
                "scanned_variants": accuracy(variants),
            },
        },
        "match_status_accuracy": _rate(match_ok),
        "mismatch_recall": _rate([True] * len(caught) + [False] * (len(mismatches) - len(caught))),
        "mismatch_recall_by_kind": by_kind,
        "mismatch_rules_exact": _rate(rules_ok),
        "escalation_rate": _rate([o.escalated for o in outcomes]),
        "review_rate": _rate([o.reviewed for o in outcomes]),
        "cost_per_doc_usd": {
            "avg": statistics.fmean(costs) if costs else None,
            "max": max(costs) if costs else None,
            "total": sum(costs),
        },
        "latency_s": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)},
        "by_model": usage,
        "per_field_accuracy": {k: sum(v) / len(v) for k, v in sorted(per_field.items())},
    }


def _rate(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None
