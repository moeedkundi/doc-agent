"""Decimal arithmetic and the display formats the templates print."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

CENTS = Decimal("0.01")

MoneyStyle = Literal["symbol", "code", "eu"]
DateStyle = Literal["iso", "long", "us_slash", "eu_dot", "dmy_slash", "d_mon_y"]

_SYMBOL = {"USD": "$", "GBP": "£", "EUR": "€"}


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENTS, rounding=ROUND_HALF_UP)


def _group(amount: Decimal, thousands: str, decimal_sep: str) -> str:
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(q2(amount)):.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{thousands.join(groups)}{decimal_sep}{frac}"


def fmt_amount(amount: Decimal, style: MoneyStyle) -> str:
    """Number only, no currency marker."""
    return _group(amount, ".", ",") if style == "eu" else _group(amount, ",", ".")


def fmt_money(amount: Decimal, currency: str, style: MoneyStyle) -> str:
    if style == "symbol":
        return f"{_SYMBOL.get(currency, currency + ' ')}{fmt_amount(amount, style)}"
    if style == "eu":
        return f"{fmt_amount(amount, style)} {_SYMBOL.get(currency, currency)}"
    return f"{currency} {fmt_amount(amount, style)}"


def fmt_qty(q: Decimal) -> str:
    return f"{q:f}".rstrip("0").rstrip(".") if "." in f"{q:f}" else f"{q:f}"


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def fmt_date(d: date, style: DateStyle) -> str:
    match style:
        case "iso":
            return d.isoformat()
        case "long":
            return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"
        case "us_slash":
            return f"{d.month:02d}/{d.day:02d}/{d.year}"
        case "eu_dot":
            return f"{d.day:02d}.{d.month:02d}.{d.year}"
        case "dmy_slash":
            return f"{d.day:02d}/{d.month:02d}/{d.year}"
        case "d_mon_y":
            return f"{d.day} {_MONTHS[d.month - 1][:3]} {d.year}"
