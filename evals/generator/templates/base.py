"""Inputs every template receives."""

from dataclasses import dataclass
from decimal import Decimal

from docagent_schemas import InvoiceRecord, PurchaseOrderRecord
from evals.generator.catalog import Company
from evals.generator.money import fmt_money
from evals.generator.render import Style

FICTION_NOTE = "Synthetic document generated for the DocAgent demo. All companies are fictional."


@dataclass(frozen=True)
class PoDoc:
    record: PurchaseOrderRecord
    buyer: Company
    vendor: Company
    tax_rate: Decimal
    contact: str
    """Name of the buyer's procurement contact (printed, not extracted)."""
    style: Style


@dataclass(frozen=True)
class InvoiceDoc:
    record: InvoiceRecord
    seller: Company
    buyer: Company
    tax_rate: Decimal
    payment_terms: str
    style: Style


def tax_label(company: Company, rate: Decimal) -> str:
    pct = f"{rate * 100:.2f}".rstrip("0").rstrip(".")
    return f"{'Sales tax' if company.country == 'US' else 'VAT'} ({pct}%)"


def tax_id_label(company: Company) -> str:
    return "EIN" if company.country == "US" else "VAT No."


def money(amount: Decimal | None, currency: str | None, style: Style) -> str:
    return "" if amount is None else fmt_money(amount, currency or "", style.money_style)
