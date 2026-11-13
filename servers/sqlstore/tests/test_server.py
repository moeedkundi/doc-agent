"""The sqlstore server over MCP: tool list, structured rejections, typed writes, auth."""

import json
from typing import Any

from mcp import Client
from starlette.testclient import TestClient

from docagent_mcp_common import SECRET_HEADER, bind_config, build_app
from docagent_mcp_sqlstore.server import create_server

from .conftest import World, sample


def code(result: Any) -> str:
    assert result.is_error
    text = str(getattr(result.content[0], "text", ""))
    return json.loads(text.split(": ", 1)[1])["code"]


async def test_tool_list(world: World) -> None:
    async with Client(create_server(world.store)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "get_schema", "run_select", "save_record", "get_purchase_order", "save_match_result",
    }  # fmt: skip
    assert tools["run_select"].annotations is not None
    assert tools["run_select"].annotations.read_only_hint
    assert not any("exec" in name or "write" in name for name in tools)  # no raw SQL writes


async def test_run_select_over_mcp(world: World) -> None:
    async with Client(create_server(world.store)) as client:
        ok = await client.call_tool(
            "run_select",
            {"sql": "SELECT po_number FROM purchase_orders ORDER BY 1", "session_id": str(world.a)},
        )
        rejected = {
            q: code(await client.call_tool("run_select", {"sql": q, "session_id": str(world.a)}))
            for q in ("INSERT INTO invoices DEFAULT VALUES", "UPDATE invoices SET total = 0",
                      "DELETE FROM invoices", "DROP TABLE invoices", "SELECT 1; SELECT 2",
                      "SELECT * FROM sessions", "SELECT pg_sleep(5)")
        }  # fmt: skip
    assert ok.structured_content["rows"] == [[sample("po_001").po_number], ["PO-85044"]]  # type: ignore[union-attr]
    assert rejected == {
        "INSERT INTO invoices DEFAULT VALUES": "not_select",
        "UPDATE invoices SET total = 0": "not_select",
        "DELETE FROM invoices": "not_select",
        "DROP TABLE invoices": "not_select",
        "SELECT 1; SELECT 2": "multiple_statements",
        "SELECT * FROM sessions": "table_not_allowed",
        "SELECT pg_sleep(5)": "function_not_allowed",
    }


async def test_save_record_checks_doc_type(world: World) -> None:
    async with Client(create_server(world.store)) as client:
        res = await client.call_tool(
            "save_record",
            {
                "session_id": str(world.a),
                "document_id": str(world.docs["inv_003"]),
                "doc_type": "purchase_order",
                "record": sample("inv_003").model_dump(mode="json"),
            },
        )
    assert code(res) == "wrong_doc_type"


async def test_get_purchase_order_over_mcp(world: World) -> None:
    async with Client(create_server(world.store)) as client:
        res = await client.call_tool(
            "get_purchase_order", {"session_id": str(world.a), "po_number": "PO-85044"}
        )
        missing = await client.call_tool(
            "get_purchase_order", {"session_id": str(world.a), "po_number": "PO-99999"}
        )
    out = res.structured_content
    assert out["found"] and out["purchase_order"]["po_number"] == "PO-85044"
    assert len(out["prior_invoices"]) == 1
    assert missing.structured_content == {
        "found": False,
        "po_id": None,
        "purchase_order": None,
        "prior_invoices": [],
    }


def test_http_requires_the_shared_secret(world: World) -> None:
    secret = "test-secret-0123456789abcdef"
    bind = bind_config("U", {"U": "http://sqlstore:8103/mcp"})
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
        build_app(create_server(world.store), bind, secret), base_url="http://sqlstore:8103"
    ) as c:
        assert c.post("/mcp", json=init, headers=headers).status_code == 401
        assert (
            c.post("/mcp", json=init, headers={**headers, SECRET_HEADER: secret}).status_code == 200
        )
