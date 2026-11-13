"""Reference server: static data only, lookups, conversions and the MCP surface."""

import ast
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from starlette.testclient import TestClient

from docagent_mcp_common import SECRET_HEADER, bind_config, build_app
from docagent_mcp_reference import data
from docagent_mcp_reference.server import create_server

PKG = Path(data.__file__).parent


def test_vendor_master_mirrors_the_generator_catalog() -> None:
    from evals.generator.catalog import CURRENCY, VENDORS

    master = data.vendors()
    assert [(v.legal_name, v.tax_id, v.country) for v in master] == [
        (c.name, c.tax_id, c.country) for c in VENDORS
    ]
    assert all(v.default_currency == CURRENCY[v.country] for v in master)  # type: ignore[index]
    assert len({v.vendor_id for v in master}) == 20


def test_only_static_bundled_data_no_network_or_db() -> None:
    imported: set[str] = set()
    for py in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(
        {
            "httpx",
            "httpx2",
            "requests",
            "urllib",
            "socket",
            "psycopg",
            "aiohttp",
            "docagent_gateway",
        }
    )
    notes = {
        n: json.loads((PKG / "data" / n).read_text())["note"].lower()
        for n in ("fx_rates.json", "tax_rates.json", "vendors.json")
    }
    assert "illustrative" in notes["fx_rates.json"] and "illustrative" in notes["tax_rates.json"]
    assert "fictional" in notes["vendors.json"]


# -- vendors ------------------------------------------------------------------------------


@pytest.mark.parametrize("tax_id", ["GB284719306", "gb 284 719 306", "GB-284-719-306"])
def test_vendor_by_tax_id(tax_id: str) -> None:
    out = data.lookup_vendor(None, tax_id)
    assert out.found and out.matched_on == "tax_id"
    assert out.vendor is not None and out.vendor.legal_name == "Brightledger Stationers Ltd"


@pytest.mark.parametrize(
    "name",
    ["Brightledger Stationers Ltd", "BRIGHTLEDGER STATIONERS LIMITED", "Brightledger Stationers"],
)
def test_vendor_by_name_variants(name: str) -> None:
    out = data.lookup_vendor(name, None)
    assert out.found and out.matched_on == "name" and out.tax_id_matches is None
    assert out.vendor is not None and out.vendor.vendor_id == "V-009"


def test_right_name_wrong_tax_id_is_flagged() -> None:
    out = data.lookup_vendor("Brightledger Stationers Ltd", "GB000000000")
    assert out.found and out.matched_on == "name" and out.tax_id_matches is False


def test_unknown_vendor() -> None:
    out = data.lookup_vendor("Acme Anvils Corporation", "US99-9999999")
    assert not out.found and out.vendor is None and out.name_score is not None


def test_vendor_lookup_needs_a_query() -> None:
    with pytest.raises(data.ReferenceError_) as exc:
        data.lookup_vendor(" ", None)
    assert exc.value.code == "missing_query"


# -- FX ------------------------------------------------------------------------------------


def test_same_currency_is_identity() -> None:
    out = data.convert(Decimal("123.45"), "EUR", "eur", date(2026, 5, 9))
    assert (out.amount, out.rate) == (Decimal("123.45"), Decimal("1"))


def test_cross_rate_via_usd_and_rate_date() -> None:
    out = data.convert(Decimal("100"), "GBP", "EUR", date(2026, 8, 29))
    per_usd = data.fx_table().per_usd[7]  # 2026-08-01
    assert out.rate_date == date(2026, 8, 1)
    assert out.rate == (per_usd["EUR"] / per_usd["GBP"]).quantize(Decimal("0.000001"))
    assert out.amount == (Decimal(100) * per_usd["EUR"] / per_usd["GBP"]).quantize(Decimal("0.01"))
    assert "illustrative" in out.source


def test_round_trip_is_stable_to_a_cent() -> None:
    there = data.convert(Decimal("5890.66"), "GBP", "USD", date(2026, 8, 29))
    back = data.convert(there.amount, "USD", "GBP", date(2026, 8, 29))
    assert abs(back.amount - Decimal("5890.66")) <= Decimal("0.01")


def test_dates_after_the_table_use_the_latest_rate() -> None:
    assert data.convert(Decimal(1), "USD", "EUR", date(2027, 6, 1)).rate_date == date(2026, 12, 1)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ((Decimal(1), "USD", "EUR", date(2025, 12, 31)), "rate_not_available"),
        ((Decimal(1), "USD", "XYZ", date(2026, 6, 1)), "unknown_currency"),
    ],
)
def test_fx_errors(args: tuple[Any, ...], code: str) -> None:
    with pytest.raises(data.ReferenceError_) as exc:
        data.convert(*args)
    assert exc.value.code == code


# -- tax -------------------------------------------------------------------------------------


def test_tax_rates_match_what_the_documents_charge() -> None:
    from evals.generator.catalog import TAX_RATE

    for country, rate in TAX_RATE.items():
        assert data.lookup_tax_rate(country, "standard").rate == rate


def test_reduced_rate_and_unknown_country() -> None:
    assert data.lookup_tax_rate("de", "reduced").rate == Decimal("0.07")
    with pytest.raises(data.ReferenceError_):
        data.lookup_tax_rate("ZZ", "standard")


# -- MCP surface ----------------------------------------------------------------------------


async def test_tools_over_mcp() -> None:
    async with Client(create_server()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        fx = await client.call_tool(
            "convert_currency",
            {"amount": "100", "from_currency": "GBP", "to_currency": "USD", "date": "2026-08-29"},
        )
        bad = await client.call_tool("lookup_tax_rate", {"country": "ZZ"})
    assert names == {"convert_currency", "lookup_vendor", "lookup_tax_rate"}
    assert fx.structured_content["rate_date"] == "2026-08-01"
    assert bad.is_error and '"code": "unknown_tax_rate"' in str(getattr(bad.content[0], "text", ""))


def test_http_requires_the_shared_secret() -> None:
    secret = "test-secret-0123456789abcdef"
    bind = bind_config("U", {"U": "http://reference:8104/mcp"})
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    headers = {"accept": "application/json, text/event-stream"}
    with TestClient(
        build_app(create_server(), bind, secret), base_url="http://reference:8104"
    ) as c:
        assert c.post("/mcp", json=init, headers=headers).status_code == 401
        ok = c.post("/mcp", json=init, headers={**headers, SECRET_HEADER: secret})
        assert ok.status_code == 200
