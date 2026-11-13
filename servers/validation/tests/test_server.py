"""The validation server over MCP: in-memory client for tools, real ASGI app for auth."""

import json
from typing import Any

import pytest
from mcp import Client
from starlette.testclient import TestClient

from docagent_mcp_common import SECRET_HEADER, bind_config, build_app
from docagent_mcp_validation.server import create_server
from docagent_mcp_validation.settings import ValidationSettings
from docagent_schemas import InvoiceRecord, PurchaseOrderRecord

SECRET = "test-secret-0123456789abcdef"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


async def test_lists_three_tools_with_schemas(settings: ValidationSettings) -> None:
    async with Client(create_server(settings)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"validate_record", "list_rules", "match_invoice_to_po"}
    match = tools["match_invoice_to_po"]
    assert match.input_schema["required"] == ["invoice", "purchase_order"]
    assert match.output_schema is not None and "status" in match.output_schema["properties"]
    assert match.annotations is not None and match.annotations.read_only_hint


async def test_match_tool_returns_structured_result(
    drift_pair: tuple[PurchaseOrderRecord, InvoiceRecord], settings: ValidationSettings
) -> None:
    po, inv = drift_pair
    async with Client(create_server(settings)) as client:
        res = await client.call_tool(
            "match_invoice_to_po",
            {"invoice": inv.model_dump(mode="json"), "purchase_order": po.model_dump(mode="json")},
        )
    assert not res.is_error
    out: dict[str, Any] = res.structured_content
    assert out["status"] == "mismatch"
    assert sorted(i["rule"] for i in out["issues"]) == [
        "total_exceeds_remaining",
        "unit_price_out_of_tolerance",
    ]


async def test_validate_tool_and_structured_error(
    drift_pair: tuple[PurchaseOrderRecord, InvoiceRecord], settings: ValidationSettings
) -> None:
    po, inv = drift_pair
    async with Client(create_server(settings)) as client:
        ok = await client.call_tool(
            "validate_record", {"doc_type": "invoice", "record": inv.model_dump(mode="json")}
        )
        wrong = await client.call_tool(
            "validate_record", {"doc_type": "invoice", "record": po.model_dump(mode="json")}
        )
    assert ok.structured_content == {"valid": True, "issues": []}
    assert wrong.is_error
    text = str(getattr(wrong.content[0], "text", ""))
    assert json.loads(text.split(": ", 1)[1])["code"] == "wrong_doc_type"


# -- HTTP transport + shared secret -----------------------------------------------------


@pytest.fixture
def http(settings: ValidationSettings) -> Any:
    bind = bind_config("URL", {"URL": "http://validation:8102/mcp"})
    with TestClient(
        build_app(create_server(settings), bind, SECRET), base_url="http://validation:8102"
    ) as client:
        yield client


def test_rejects_missing_secret(http: TestClient) -> None:
    r = http.post("/mcp", json=INIT, headers=HEADERS)
    assert (r.status_code, r.json()) == (401, {"error": "unauthorized"})


def test_rejects_wrong_secret(http: TestClient) -> None:
    r = http.post("/mcp", json=INIT, headers={**HEADERS, SECRET_HEADER: SECRET + "x"})
    assert r.status_code == 401


def test_accepts_correct_secret(http: TestClient) -> None:
    r = http.post("/mcp", json=INIT, headers={**HEADERS, SECRET_HEADER: SECRET})
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "docagent-validation"


def test_rejects_unknown_host_header(http: TestClient) -> None:
    """DNS-rebinding protection: only the configured host names are served."""
    r = http.post(
        "/mcp", json=INIT, headers={**HEADERS, SECRET_HEADER: SECRET, "host": "evil.example:8102"}
    )
    assert r.status_code in (400, 421)
