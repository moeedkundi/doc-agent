"""The reference MCP server: FX conversion, vendor master and tax rates from static data."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from docagent_mcp_common import tool, tool_error
from docagent_mcp_reference import data
from docagent_schemas import FxConversion, TaxRate, VendorLookup

INSTRUCTIONS = (
    "Reference lookups from static, illustrative tables bundled with the server: currency "
    "conversion, the (fictional) vendor master and VAT/sales-tax rates. No live data."
)
PURE = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
Currency = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$", description="ISO 4217 code")]


def create_server() -> MCPServer:
    server = MCPServer("docagent-reference", instructions=INSTRUCTIONS, version="0.1.0")

    @tool(server, PURE)
    def convert_currency(
        amount: Decimal,
        from_currency: Currency,
        to_currency: Currency,
        date: Annotated[dt.date, Field(description="Use the rate valid on this day")],
    ) -> FxConversion:
        """Convert an amount between currencies with the static rate valid on `date`."""
        try:
            return data.convert(amount, from_currency, to_currency, date)
        except data.ReferenceError_ as exc:
            raise tool_error(exc.code, str(exc)) from None

    @tool(server, PURE)
    def lookup_vendor(
        name: Annotated[str | None, Field(max_length=200)] = None,
        tax_id: Annotated[str | None, Field(max_length=40)] = None,
    ) -> VendorLookup:
        """Find a vendor in the master by tax ID (exact) or name (fuzzy, legal form ignored).

        With both given, `tax_id_matches` says whether the tax ID agrees with the record found
        by name; a mismatch is worth a human look.
        """
        try:
            return data.lookup_vendor(name, tax_id)
        except data.ReferenceError_ as exc:
            raise tool_error(exc.code, str(exc)) from None

    @tool(server, PURE)
    def lookup_tax_rate(
        country: Annotated[str, Field(pattern=r"^[A-Za-z]{2}$", description="ISO 3166 alpha-2")],
        category: Literal["standard", "reduced"] = "standard",
    ) -> TaxRate:
        """VAT / sales-tax rate for a country (illustrative static table)."""
        try:
            return data.lookup_tax_rate(country, category)
        except data.ReferenceError_ as exc:
            raise tool_error(exc.code, str(exc)) from None

    return server
