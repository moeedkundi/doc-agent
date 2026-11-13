"""Static bundled reference data: FX table, fictional vendor master, tax rates.

Loaded from JSON files shipped inside the package. There are no network calls and no
database. Every value is illustrative, as the `source` field on each answer says.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from importlib import resources
from typing import Any, Literal

from rapidfuzz import fuzz

from docagent_schemas import STATIC_SOURCE, FxConversion, TaxRate, VendorLookup, VendorRecord

_LEGAL_FORMS = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|plc|llp|gmbh|ag|b\s?v|nv|co|corp|corporation|"
    r"company|pc|sa)\b"
)
NAME_MATCH_MIN_SCORE = 85.0


class ReferenceError_(LookupError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _load(name: str) -> dict[str, Any]:
    text = resources.files("docagent_mcp_reference").joinpath("data", name).read_text("utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


@dataclass(frozen=True)
class FxTable:
    dates: tuple[date, ...]
    per_usd: tuple[dict[str, Decimal], ...]

    def rates_on(self, day: date) -> tuple[date, dict[str, Decimal]]:
        applicable = [i for i, d in enumerate(self.dates) if d <= day]
        if not applicable:
            raise ReferenceError_(
                "rate_not_available", f"no rate before {self.dates[0]} (requested {day})"
            )
        i = applicable[-1]
        return self.dates[i], self.per_usd[i]


@cache
def fx_table() -> FxTable:
    raw = _load("fx_rates.json")
    rows = sorted(raw["rates"], key=lambda r: r["date"])
    return FxTable(
        dates=tuple(date.fromisoformat(r["date"]) for r in rows),
        per_usd=tuple({c: Decimal(v) for c, v in r["per_usd"].items()} for r in rows),
    )


@cache
def vendors() -> tuple[VendorRecord, ...]:
    return tuple(VendorRecord.model_validate(v) for v in _load("vendors.json")["vendors"])


@cache
def tax_rates() -> tuple[TaxRate, ...]:
    return tuple(
        TaxRate.model_validate({**r, "source": STATIC_SOURCE})
        for r in _load("tax_rates.json")["rates"]
    )


def convert(amount: Decimal, from_ccy: str, to_ccy: str, day: date) -> FxConversion:
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    rate_date, per_usd = fx_table().rates_on(day)
    for ccy in (from_ccy, to_ccy):
        if ccy not in per_usd:
            raise ReferenceError_(
                "unknown_currency",
                f"{ccy} is not in the static table ({', '.join(sorted(per_usd))})",
            )
    rate = (per_usd[to_ccy] / per_usd[from_ccy]).quantize(Decimal("0.000001"), ROUND_HALF_UP)
    converted = (amount * per_usd[to_ccy] / per_usd[from_ccy]).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )
    return FxConversion(
        amount=converted,
        from_currency=from_ccy,
        to_currency=to_ccy,
        rate=rate,
        rate_date=rate_date,
        source=STATIC_SOURCE,
    )


def _norm_name(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return " ".join(_LEGAL_FORMS.sub(" ", text).split())


def _norm_tax_id(tax_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", tax_id.upper())


def lookup_vendor(name: str | None, tax_id: str | None) -> VendorLookup:
    if not (name and name.strip()) and not (tax_id and tax_id.strip()):
        raise ReferenceError_("missing_query", "give a vendor name, a tax_id, or both")
    master = vendors()
    by_tax: VendorRecord | None = None
    if tax_id:
        wanted = _norm_tax_id(tax_id)
        by_tax = next((v for v in master if _norm_tax_id(v.tax_id) == wanted), None)

    by_name: VendorRecord | None = None
    score: float | None = None
    if name:
        scored = [
            (fuzz.token_sort_ratio(_norm_name(name), _norm_name(v.legal_name)), v) for v in master
        ]
        best_score, best = max(scored, key=lambda s: s[0])
        score = round(float(best_score), 1)
        if best_score >= NAME_MATCH_MIN_SCORE:
            by_name = best

    if by_tax is not None:
        return VendorLookup(
            found=True,
            vendor=by_tax,
            matched_on="tax_id",
            name_score=score,
            tax_id_matches=True,
        )
    if by_name is not None:
        agrees = None if not tax_id else _norm_tax_id(tax_id) == _norm_tax_id(by_name.tax_id)
        return VendorLookup(
            found=True, vendor=by_name, matched_on="name", name_score=score, tax_id_matches=agrees
        )
    return VendorLookup(found=False, name_score=score)


def lookup_tax_rate(country: str, category: Literal["standard", "reduced"]) -> TaxRate:
    country = country.upper()
    for r in tax_rates():
        if r.country == country and r.category == category:
            return r
    raise ReferenceError_(
        "unknown_tax_rate", f"no {category} rate for {country} in the static table"
    )
