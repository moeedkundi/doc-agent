"""Build the full synthetic dataset: purchase orders, invoices against them, ground truth.

Composition (SPEC §10 Phase 1), 30 POs + 30 invoices:

    split    scenario          invoices  POs  expected match
    sample   full                  1      1   matched
    sample   partial (PO-1042)     1      1   partial
    sample   price_drift           1      1   mismatch
    eval     full                  8      8   matched
    eval     partial               3      3   partial
    eval     split (2 invoices)    6      3   partial, then matched
    eval     price_drift           2      2   mismatch
    eval     over_billed_qty       2      2   mismatch
    eval     wrong_vendor          2      2   mismatch
    eval     missing_po            2      -   po_not_found  (cites a PO that doesn't exist)
    eval     no_po                 2      -   none          (no PO number printed)
    eval     uninvoiced            -      7   -

Single-document arithmetic defects (for validate_record / review): one eval `full` invoice
with a wrong printed total, one eval `partial` invoice with a wrong line total, one
uninvoiced eval PO with a wrong printed total.

Mismatch invoices always target a PO no other invoice touches, so the expected outcome does
not depend on how earlier mismatching invoices are counted. Everything derives from `seed`.
"""

import hashlib
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Literal

from faker import Faker
from reportlab.lib.colors import HexColor

from docagent_schemas import (
    ExpectedMatch,
    GroundTruth,
    Injection,
    InvoiceRecord,
    MatchIssueRule,
    PurchaseOrderRecord,
)
from evals.generator.catalog import BUYERS, PAYMENT_TERMS, VENDORS, Company, Country
from evals.generator.money import DateStyle, MoneyStyle, q2
from evals.generator.records import InvoiceLinePlan, PoContext, make_invoice, make_po
from evals.generator.render import Style
from evals.generator.scan import scanify
from evals.generator.templates import classic, compact, modern
from evals.generator.templates.base import InvoiceDoc, PoDoc

DEFAULT_SEED = 20261114
GENERATOR_VERSION = 1
AMOUNT_TOLERANCE = Decimal("0.01")
"""Must match validation.amount_tolerance in config/limits.yaml."""
PRICE_TOLERANCE_PCT = Decimal("2.0")
"""Must match po_matching.unit_price_tolerance_pct in config/limits.yaml."""

TEMPLATES: tuple[ModuleType, ...] = (classic, modern, compact)
Split = Literal["sample", "eval"]
Kind = Literal[
    "full",
    "partial",
    "split",
    "price_drift",
    "over_billed_qty",
    "wrong_vendor",
    "missing_po",
    "no_po",
    "uninvoiced",
]

_ACCENTS = ("#2f5d50", "#7a3b2e", "#1f4e79", "#5b4a8b", "#8a5a00", "#3d6b2f", "#9c2f4f", "#264653")
_DATE_STYLES: dict[Country, tuple[DateStyle, ...]] = {
    "US": ("us_slash", "long", "iso"),
    "GB": ("dmy_slash", "d_mon_y", "iso"),
    "DE": ("eu_dot", "d_mon_y", "iso"),
    "NL": ("eu_dot", "dmy_slash", "iso"),
}
_MONEY_STYLES: dict[Country, tuple[MoneyStyle, ...]] = {
    "US": ("symbol", "code"),
    "GB": ("symbol", "code"),
    "DE": ("eu", "code", "symbol"),
    "NL": ("eu", "code"),
}
_FAKER_LOCALE: dict[Country, str] = {"US": "en_US", "GB": "en_GB", "DE": "de_DE", "NL": "nl_NL"}


def _stable_index(key: str, n: int) -> int:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") % n


def company_style(company: Company) -> Style:
    """Each company always prints with the same accent, date and money conventions."""
    return Style(
        date_style=_DATE_STYLES[company.country][
            _stable_index("date:" + company.code, len(_DATE_STYLES[company.country]))
        ],
        money_style=_MONEY_STYLES[company.country][
            _stable_index("money:" + company.code, len(_MONEY_STYLES[company.country]))
        ],
        accent=HexColor(_ACCENTS[_stable_index("accent:" + company.code, len(_ACCENTS))]),
    )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class PoJob:
    doc_id: str
    split: Split
    ctx: PoContext
    template: ModuleType
    scanned: bool
    contact: str
    injected: list[Injection] = field(default_factory=lambda: [])


@dataclass
class InvoiceJob:
    doc_id: str
    split: Split
    record: InvoiceRecord
    seller: Company
    buyer: Company
    tax_rate: Decimal
    payment_terms: str
    template: ModuleType
    scanned: bool
    references_po: str | None
    injected: list[Injection]
    expected: ExpectedMatch | None


@dataclass(frozen=True)
class Dataset:
    seed: int
    pos: list[PoJob]
    invoices: list[InvoiceJob]


@dataclass
class _Builder:
    rng: random.Random
    fakers: dict[Country, Faker]
    used_po_numbers: set[str] = field(default_factory=lambda: set())
    used_invoice_numbers: set[str] = field(default_factory=lambda: set())
    # Remaining state per emitted PO doc_id, in ingest order.
    remaining_qty: dict[str, list[Decimal]] = field(default_factory=lambda: {})
    remaining_value: dict[str, Decimal] = field(default_factory=lambda: {})

    # -- numbering --------------------------------------------------------------
    def po_number(self, fixed: str | None = None, lo: int = 1000, hi: int = 89999) -> str:
        if fixed is not None:
            self.used_po_numbers.add(fixed)
            return fixed
        while True:
            n = f"PO-{self.rng.randint(lo, hi)}"
            if n not in self.used_po_numbers:
                self.used_po_numbers.add(n)
                return n

    def invoice_number(self, vendor: Company, d: date) -> str:
        fmt = _stable_index("invfmt:" + vendor.code, 3)
        while True:
            n = self.rng.randint(100, 9999)
            number = (
                f"{vendor.code}-{d.year}-{n:04d}",
                f"INV{n:06d}",
                f"{d.year % 100:02d}{n:05d}",
            )[fmt]
            if number not in self.used_invoice_numbers:
                self.used_invoice_numbers.add(number)
                return number

    # -- parties ----------------------------------------------------------------
    def pair(self) -> tuple[Company, Company]:
        vendor = self.rng.choice(VENDORS)
        buyer = self.rng.choice([b for b in BUYERS if b.country == vendor.country])
        return buyer, vendor

    def other_vendor(self, vendor: Company) -> Company:
        return self.rng.choice(
            [v for v in VENDORS if v.country == vendor.country and v.code != vendor.code]
        )

    # -- POs --------------------------------------------------------------------
    def new_po(self, *, fixed_number: str | None = None, long_order: bool = False) -> PoContext:
        buyer, vendor = self.pair()
        n_lines = self.rng.randint(22, 26) if long_order else self.rng.randint(2, 7)
        return make_po(
            self.rng,
            po_number=self.po_number(fixed_number),
            buyer=buyer,
            vendor=vendor,
            po_date=date(2026, 1, 5) + timedelta(days=self.rng.randint(0, 240)),
            payment_terms=self.rng.choice(PAYMENT_TERMS),
            n_lines=n_lines,
        )

    def contact(self, country: Country) -> str:
        return self.fakers[country].name()

    # -- invoices ---------------------------------------------------------------
    def invoice_date(self, po: PoContext, after: date | None = None) -> date:
        if after is not None:
            return after + timedelta(days=self.rng.randint(12, 25))
        base = po.record.requested_delivery_date or po.record.po_date or date(2026, 6, 1)
        return base + timedelta(days=self.rng.randint(0, 6))

    def full_plan(self, po: PoContext, show_sku: bool) -> list[InvoiceLinePlan]:
        return [
            InvoiceLinePlan(i, li.quantity or Decimal(0), li.unit_price or Decimal(0), show_sku)
            for i, li in enumerate(po.record.line_items)
        ]

    def partial_plan(self, po: PoContext, show_sku: bool) -> list[InvoiceLinePlan]:
        n = len(po.record.line_items)
        keep = sorted(self.rng.sample(range(n), self.rng.randint(1, n - 1)))
        return [p for p in self.full_plan(po, show_sku) if p.po_line in keep]

    def split_plans(
        self, po: PoContext, sku_a: bool, sku_b: bool
    ) -> tuple[list[InvoiceLinePlan], list[InvoiceLinePlan]]:
        first: list[InvoiceLinePlan] = []
        second: list[InvoiceLinePlan] = []
        for i, li in enumerate(po.record.line_items):
            qty, price = li.quantity or Decimal(0), li.unit_price or Decimal(0)
            mode = self.rng.choice(("first", "second", "both") if qty >= 2 else ("first", "second"))
            if mode == "both":
                q1 = Decimal(int(qty) // 2)
                first.append(InvoiceLinePlan(i, q1, price, sku_a))
                second.append(InvoiceLinePlan(i, qty - q1, price, sku_b))
            elif mode == "first":
                first.append(InvoiceLinePlan(i, qty, price, sku_a))
            else:
                second.append(InvoiceLinePlan(i, qty, price, sku_b))
        if not first:
            first.append(replace(second.pop(0), show_sku=sku_a))
        if not second:
            second.append(replace(first.pop(), show_sku=sku_b))
        return first, second

    def show_sku(self, template: ModuleType) -> bool:
        policy: bool | None = template.INVOICE_SHOWS_SKU
        return self.rng.random() < 0.5 if policy is None else policy

    def expected_for(
        self,
        po_doc_id: str,
        po: PoContext,
        inv: InvoiceRecord,
        plan: list[InvoiceLinePlan],
        rules: list[MatchIssueRule],
    ) -> ExpectedMatch:
        """Apply this invoice to the PO's remaining balance and derive the expected outcome."""
        remaining = self.remaining_qty.setdefault(
            po_doc_id, [li.quantity or Decimal(0) for li in po.record.line_items]
        )
        value = self.remaining_value.setdefault(po_doc_id, po.record.total or Decimal(0))
        rules = list(rules)
        if (inv.total or Decimal(0)) > value + AMOUNT_TOLERANCE:
            rules.append("total_exceeds_remaining")
        for p in plan:
            remaining[p.po_line] -= p.quantity
        self.remaining_value[po_doc_id] = value - (inv.total or Decimal(0))
        if rules:
            return ExpectedMatch(status="mismatch", rules=rules)
        if any(q < 0 for q in remaining):
            raise AssertionError(f"clean invoice over-consumed {po_doc_id}")
        return ExpectedMatch(
            status="matched" if all(q == 0 for q in remaining) else "partial", rules=[]
        )


# ---------------------------------------------------------------------------
# Defect injection (single-document arithmetic errors)
# ---------------------------------------------------------------------------


def _typo_delta(rng: random.Random) -> Decimal:
    return Decimal(rng.choice(("10.00", "100.00", "1.00", "0.90", "9.00")))


def break_invoice_total(rng: random.Random, rec: InvoiceRecord) -> tuple[InvoiceRecord, Injection]:
    delta = _typo_delta(rng)
    old = rec.total or Decimal(0)
    new = old - delta
    return rec.model_copy(update={"total": new}), Injection(
        kind="arithmetic_total",
        detail=f"printed total {new} != subtotal + tax {old}",
    )


def break_invoice_line(
    rng: random.Random, rec: InvoiceRecord, tax_rate: Decimal
) -> tuple[InvoiceRecord, Injection]:
    """Print one line total too low; subtotal/tax/total stay consistent with printed lines."""
    i = rng.randrange(len(rec.line_items))
    li = rec.line_items[i]
    delta = _typo_delta(rng)
    old = li.line_total or Decimal(0)
    if old <= delta:
        delta = q2(old / 2)
    lines = list(rec.line_items)
    lines[i] = li.model_copy(update={"line_total": old - delta})
    subtotal = q2(sum((x.line_total or Decimal(0) for x in lines), Decimal(0)))
    tax = q2(subtotal * tax_rate)
    return rec.model_copy(
        update={
            "line_items": lines,
            "subtotal": subtotal,
            "tax_amount": tax,
            "total": subtotal + tax,
        }
    ), Injection(
        kind="arithmetic_line",
        line=i,
        detail=f"line total {old - delta} != qty x unit price {old}",
    )


def break_po_total(
    rng: random.Random, rec: PurchaseOrderRecord
) -> tuple[PurchaseOrderRecord, Injection]:
    delta = _typo_delta(rng)
    old = rec.total or Decimal(0)
    new = old - delta
    return rec.model_copy(update={"total": new}), Injection(
        kind="arithmetic_total", detail=f"printed total {new} != subtotal + tax {old}"
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

_EVAL_KINDS: tuple[tuple[Kind, int], ...] = (
    ("full", 8),
    ("partial", 3),
    ("split", 3),
    ("price_drift", 2),
    ("over_billed_qty", 2),
    ("wrong_vendor", 2),
    ("missing_po", 2),
    ("no_po", 2),
    ("uninvoiced", 7),
)
_SAMPLE_KINDS: tuple[Kind, ...] = ("full", "partial", "price_drift")
_SAMPLE_TEMPLATES = ((classic, classic), (modern, compact), (compact, modern))
"""(PO template, invoice template) per sample, so the samples show every layout."""


def _balanced(rng: random.Random, n: int) -> list[ModuleType]:
    out = [TEMPLATES[i % len(TEMPLATES)] for i in range(n)]
    rng.shuffle(out)
    return out


def build(seed: int = DEFAULT_SEED) -> Dataset:
    rng = random.Random(seed)
    fakers: dict[Country, Faker] = {}
    for i, (country, locale) in enumerate(sorted(_FAKER_LOCALE.items())):
        f = Faker(locale)
        f.seed_instance(seed + i)
        fakers[country] = f
    b = _Builder(rng=rng, fakers=fakers)

    pos: list[PoJob] = []
    invoices: list[InvoiceJob] = []
    po_seq = iter(range(1, 1000))
    inv_seq = iter(range(1, 1000))

    def po_id() -> str:
        return f"po_{next(po_seq):03d}"

    def inv_id() -> str:
        return f"inv_{next(inv_seq):03d}"

    # Scenario list: samples first (fixed), then eval kinds in shuffled order.
    eval_kinds: list[Kind] = [k for k, n in _EVAL_KINDS for _ in range(n)]
    rng.shuffle(eval_kinds)
    n_eval_pos = sum(1 for k in eval_kinds if k not in ("missing_po", "no_po"))
    n_eval_invs = sum(2 if k == "split" else 1 for k in eval_kinds if k != "uninvoiced")
    po_templates = _balanced(rng, n_eval_pos)
    inv_templates = _balanced(rng, n_eval_invs)
    scanned_pos = set(rng.sample(range(n_eval_pos), 5))
    scanned_invs = set(rng.sample(range(n_eval_invs), 6))
    defect_full = eval_kinds.index("full")
    defect_partial = eval_kinds.index("partial")
    defect_po = eval_kinds.index("uninvoiced")
    # Two long orders that run onto a second page: one invoiced in full, one never invoiced.
    long_orders = {
        next(i for i, k in enumerate(eval_kinds) if k == "full" and i != defect_full),
        next(i for i, k in enumerate(eval_kinds) if k == "uninvoiced" and i != defect_po),
    }

    deferred: list[Callable[[], None]] = []
    """Second halves of split POs, emitted after a few more invoices (still after the first)."""

    plan_items: list[tuple[Split, Kind, int]] = [
        ("sample", k, i) for i, k in enumerate(_SAMPLE_KINDS)
    ]
    eval_items: list[tuple[Split, Kind, int]] = [("eval", k, i) for i, k in enumerate(eval_kinds)]
    plan_items += eval_items

    po_t_iter = iter(po_templates)
    inv_t_iter = iter(inv_templates)
    po_counter = 0
    inv_counter = 0

    def next_po_template(sample: bool, idx: int) -> tuple[ModuleType, bool]:
        nonlocal po_counter
        if sample:
            return _SAMPLE_TEMPLATES[idx][0], False
        t = next(po_t_iter)
        scanned = po_counter in scanned_pos
        po_counter += 1
        return t, scanned

    def next_inv_template(sample: bool, idx: int, kind: Kind) -> tuple[ModuleType, bool]:
        nonlocal inv_counter
        if sample:
            # Showcase one scanned document among the samples: the PO-1042 invoice.
            return _SAMPLE_TEMPLATES[idx][1], kind == "partial"
        t = next(inv_t_iter)
        scanned = inv_counter in scanned_invs
        inv_counter += 1
        return t, scanned

    for split, kind, idx in plan_items:
        sample = split == "sample"

        if kind in ("missing_po", "no_po"):
            # A PO the dataset never contains.
            ctx = b.new_po()
            t, scanned = next_inv_template(sample, idx, kind)
            plan = (
                b.full_plan(ctx, b.show_sku(t))
                if b.rng.random() < 0.5
                else b.partial_plan(ctx, b.show_sku(t))
            )
            if kind == "missing_po":
                cited = b.po_number(lo=90000, hi=99999)
                inj = [Injection(kind="missing_po", detail=f"cites {cited}, which does not exist")]
                expected = ExpectedMatch(status="po_not_found", rules=["po_not_found"])
            else:
                cited, inj, expected = None, [], None
            d = b.invoice_date(ctx)
            rec = make_invoice(
                invoice_number=b.invoice_number(ctx.vendor, d),
                invoice_date=d,
                po=ctx,
                seller=ctx.vendor,
                po_number=cited,
                plan=plan,
            )
            invoices.append(
                InvoiceJob(
                    inv_id(),
                    split,
                    rec,
                    ctx.vendor,
                    ctx.buyer,
                    ctx.tax_rate,
                    ctx.record.payment_terms or "Net 30",
                    t,
                    scanned,
                    None,
                    inj,
                    expected,
                )
            )
            continue

        ctx = b.new_po(
            fixed_number="PO-1042" if sample and kind == "partial" else None,
            long_order=not sample and idx in long_orders,
        )
        t_po, scanned_po = next_po_template(sample, idx)
        po_job = PoJob(po_id(), split, ctx, t_po, scanned_po, b.contact(ctx.buyer.country))
        pos.append(po_job)
        terms = ctx.record.payment_terms or "Net 30"

        if kind == "uninvoiced":
            if not sample and idx == defect_po:
                new_rec, inj = break_po_total(b.rng, ctx.record)
                po_job.ctx = PoContext(new_rec, ctx.buyer, ctx.vendor, ctx.products, ctx.tax_rate)
                po_job.injected.append(inj)
            continue

        t_inv, scanned_inv = next_inv_template(sample, idx, kind)
        sku = b.show_sku(t_inv)

        if kind == "split":
            t_inv2, scanned_inv2 = next_inv_template(sample, idx, kind)
            plan_a, plan_b = b.split_plans(ctx, sku, b.show_sku(t_inv2))
            d1 = b.invoice_date(ctx)
            rec1 = make_invoice(
                invoice_number=b.invoice_number(ctx.vendor, d1),
                invoice_date=d1,
                po=ctx,
                seller=ctx.vendor,
                po_number=ctx.record.po_number,
                plan=plan_a,
            )
            exp1 = b.expected_for(po_job.doc_id, ctx, rec1, plan_a, [])
            invoices.append(
                InvoiceJob(
                    inv_id(),
                    split,
                    rec1,
                    ctx.vendor,
                    ctx.buyer,
                    ctx.tax_rate,
                    terms,
                    t_inv,
                    scanned_inv,
                    po_job.doc_id,
                    [],
                    exp1,
                )
            )

            def emit_second(
                ctx: PoContext = ctx,
                plan_b: list[InvoiceLinePlan] = plan_b,
                d1: date = d1,
                po_doc: str = po_job.doc_id,
                t: ModuleType = t_inv2,
                sc: bool = scanned_inv2,
                split: Split = split,
                terms: str = terms,
            ) -> None:
                d2 = b.invoice_date(ctx, after=d1)
                rec2 = make_invoice(
                    invoice_number=b.invoice_number(ctx.vendor, d2),
                    invoice_date=d2,
                    po=ctx,
                    seller=ctx.vendor,
                    po_number=ctx.record.po_number,
                    plan=plan_b,
                )
                exp2 = b.expected_for(po_doc, ctx, rec2, plan_b, [])
                invoices.append(
                    InvoiceJob(
                        inv_id(),
                        split,
                        rec2,
                        ctx.vendor,
                        ctx.buyer,
                        ctx.tax_rate,
                        terms,
                        t,
                        sc,
                        po_doc,
                        [],
                        exp2,
                    )
                )

            deferred.append(emit_second)
            continue

        seller = ctx.vendor
        injected: list[Injection] = []
        rules: list[MatchIssueRule] = []
        plan = b.partial_plan(ctx, sku) if kind == "partial" else b.full_plan(ctx, sku)

        if kind == "price_drift":
            candidates = [p for p in plan if p.unit_price >= Decimal(5)] or plan
            target = (
                max(candidates, key=lambda p: p.quantity * p.unit_price)
                if sample
                else (b.rng.choice(candidates))
            )
            # The public sample gets a clear +5%; eval drifts range over 3-8%.
            pct = Decimal(5) if sample else Decimal(b.rng.randint(30, 80)) / Decimal(10)
            new_price = q2(target.unit_price * (1 + pct / 100))
            actual_pct = ((new_price / target.unit_price - 1) * 100).quantize(Decimal("0.1"))
            plan = [
                InvoiceLinePlan(p.po_line, p.quantity, new_price, p.show_sku) if p is target else p
                for p in plan
            ]
            line = next(i for i, p in enumerate(plan) if p.po_line == target.po_line)
            injected.append(
                Injection(
                    kind="price_drift",
                    line=line,
                    detail=f"unit price {target.unit_price} -> {new_price} (+{actual_pct}%, "
                    f"tolerance {PRICE_TOLERANCE_PCT}%)",
                )
            )
            rules.append("unit_price_out_of_tolerance")
        elif kind == "over_billed_qty":
            target = b.rng.choice(plan)
            extra = max(Decimal(1), Decimal(int(target.quantity * Decimal("0.2"))))
            plan = [
                InvoiceLinePlan(p.po_line, p.quantity + extra, p.unit_price, p.show_sku)
                if p is target
                else p
                for p in plan
            ]
            line = next(i for i, p in enumerate(plan) if p.po_line == target.po_line)
            injected.append(
                Injection(
                    kind="over_billed_qty",
                    line=line,
                    detail=f"billed qty {target.quantity + extra} > ordered {target.quantity}",
                )
            )
            rules.append("qty_exceeds_remaining")
        elif kind == "wrong_vendor":
            seller = b.other_vendor(ctx.vendor)
            injected.append(
                Injection(
                    kind="wrong_vendor",
                    detail=f"billed by {seller.name}; PO was issued to {ctx.vendor.name}",
                )
            )
            rules.append("vendor_mismatch")

        d = b.invoice_date(ctx)
        rec = make_invoice(
            invoice_number=b.invoice_number(seller, d),
            invoice_date=d,
            po=ctx,
            seller=seller,
            po_number=ctx.record.po_number,
            plan=plan,
        )
        if not sample and kind == "full" and idx == defect_full:
            rec, inj = break_invoice_total(b.rng, rec)
            injected.append(inj)
        if not sample and kind == "partial" and idx == defect_partial:
            rec, inj = break_invoice_line(b.rng, rec, ctx.tax_rate)
            injected.append(inj)
        expected = b.expected_for(po_job.doc_id, ctx, rec, plan, rules)
        invoices.append(
            InvoiceJob(
                inv_id(),
                split,
                rec,
                seller,
                ctx.buyer,
                ctx.tax_rate,
                terms,
                t_inv,
                scanned_inv,
                po_job.doc_id,
                injected,
                expected,
            )
        )

        # Let a few invoices pass before emitting a pending split's second invoice.
        if deferred and b.rng.random() < 0.5:
            deferred.pop(0)()

    for emit in deferred:
        emit()

    _check(pos, invoices)
    return Dataset(seed=seed, pos=pos, invoices=invoices)


def _check(pos: Sequence[PoJob], invoices: Sequence[InvoiceJob]) -> None:
    """Invariants the rest of the system relies on."""
    if (len(pos), len(invoices)) != (30, 30):
        raise AssertionError(f"expected 30 POs + 30 invoices, got {len(pos)} + {len(invoices)}")
    samples = [j for j in (*pos, *invoices) if j.split == "sample"]
    if len(samples) != 6:
        raise AssertionError("expected 6 sample documents")
    po_ids = {p.doc_id: p for p in pos}
    for inv in invoices:
        if inv.references_po is not None:
            po = po_ids[inv.references_po]
            if po.split != inv.split:
                raise AssertionError(f"{inv.doc_id} crosses splits")
            if inv.record.po_number != po.ctx.record.po_number:
                raise AssertionError(f"{inv.doc_id} cites the wrong PO number")
    numbers = [p.ctx.record.po_number for p in pos]
    if len(set(numbers)) != len(numbers):
        raise AssertionError("duplicate PO numbers")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _doc_rng(seed: int, doc_id: str) -> random.Random:
    return random.Random(f"{seed}:{doc_id}")


def render(ds: Dataset, out_dir: Path) -> list[GroundTruth]:
    """Write PDFs + ground-truth JSON + manifest; return ground truth in ingest order."""
    for sub in ("samples", "eval"):
        d = out_dir / sub
        d.mkdir(parents=True, exist_ok=True)
        for stale in (*d.glob("*.pdf"), *d.glob("*.json")):
            stale.unlink()

    def folder(split: Split) -> str:
        return "samples" if split == "sample" else "eval"

    truths: list[GroundTruth] = []
    for job in ds.pos:
        rel = f"{folder(job.split)}/{job.doc_id}.pdf"
        pdf, pages = job.template.render_po(
            PoDoc(
                job.ctx.record,
                job.ctx.buyer,
                job.ctx.vendor,
                job.ctx.tax_rate,
                job.contact,
                company_style(job.ctx.buyer),
            )
        )
        if job.scanned:
            pdf = scanify(pdf, _doc_rng(ds.seed, job.doc_id), f"Purchase Order {job.doc_id}")
        (out_dir / rel).write_bytes(pdf)
        truths.append(
            GroundTruth(
                doc_id=job.doc_id,
                doc_type="purchase_order",
                file=rel,
                split=job.split,
                template=job.template.NAME,
                scanned=job.scanned,
                pages=pages,
                record=job.ctx.record,
                injected=job.injected,
            )
        )
    for job in ds.invoices:
        rel = f"{folder(job.split)}/{job.doc_id}.pdf"
        pdf, pages = job.template.render_invoice(
            InvoiceDoc(
                job.record,
                job.seller,
                job.buyer,
                job.tax_rate,
                job.payment_terms,
                company_style(job.seller),
            )
        )
        if job.scanned:
            pdf = scanify(pdf, _doc_rng(ds.seed, job.doc_id), f"Invoice {job.doc_id}")
        (out_dir / rel).write_bytes(pdf)
        truths.append(
            GroundTruth(
                doc_id=job.doc_id,
                doc_type="invoice",
                file=rel,
                split=job.split,
                template=job.template.NAME,
                scanned=job.scanned,
                pages=pages,
                record=job.record,
                references_po=job.references_po,
                injected=job.injected,
                expected_match=job.expected,
            )
        )

    for gt in truths:
        path = out_dir / gt.file.replace(".pdf", ".json")
        path.write_text(gt.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": ds.seed,
        "note": "Synthetic documents; all companies fictional. Ingest in this order "
        "(POs before the invoices that reference them).",
        "documents": [
            {
                "doc_id": gt.doc_id,
                "doc_type": gt.doc_type,
                "split": gt.split,
                "file": gt.file,
                "ground_truth": gt.file.replace(".pdf", ".json"),
                "template": gt.template,
                "scanned": gt.scanned,
                "pages": gt.pages,
                "injected": [i.kind for i in gt.injected],
                "expected_match": gt.expected_match.status if gt.expected_match else None,
            }
            for gt in truths
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return truths


def generate(out_dir: Path, seed: int = DEFAULT_SEED) -> list[GroundTruth]:
    return render(build(seed), out_dir)
