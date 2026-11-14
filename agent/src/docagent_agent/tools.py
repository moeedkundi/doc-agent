"""MCP access for the agent, with a fixed tool set per graph node (SPEC §8.2).

`McpHub` holds one official `mcp.Client` per server over Streamable HTTP (with the shared
secret header). Nodes never see the hub: each gets a `NodeTools` that can call only the tools
listed for it in `NODE_TOOLS`, so e.g. `classify` and `extract` cannot reach sqlstore even
if a document tried to talk them into it.
"""

import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

Server = Literal["extraction", "validation", "sqlstore", "reference"]

TOOL_SERVER: dict[str, Server] = {
    "classify_document": "extraction",
    "extract_fields": "extraction",
    "reextract_fields": "extraction",
    "validate_record": "validation",
    "match_invoice_to_po": "validation",
    "list_rules": "validation",
    "get_schema": "sqlstore",
    "run_select": "sqlstore",
    "save_record": "sqlstore",
    "get_purchase_order": "sqlstore",
    "save_match_result": "sqlstore",
    "convert_currency": "reference",
    "lookup_vendor": "reference",
    "lookup_tax_rate": "reference",
}

NODE_TOOLS: dict[str, frozenset[str]] = {
    "classify": frozenset({"classify_document"}),
    "extract": frozenset({"extract_fields"}),
    "validate": frozenset({"validate_record", "lookup_vendor"}),
    "reextract": frozenset({"reextract_fields"}),
    "human_review": frozenset(),
    "validate_final": frozenset({"validate_record"}),
    "save": frozenset({"save_record"}),
    "po_match": frozenset({"get_purchase_order", "match_invoice_to_po", "save_match_result"}),
    "summarize": frozenset(),
    "reject_unknown": frozenset(),
    # ask graph (§7.2): the model plans/writes SQL and phrases answers via the gateway; the
    # only tools are the read-only schema, the guarded SELECT and the static FX table.
    "ask_plan": frozenset({"get_schema"}),
    "ask_sql": frozenset(),
    "ask_run": frozenset({"run_select"}),
    "ask_convert": frozenset({"convert_currency"}),
    "ask_answer": frozenset(),
}
"""The only tools each node may call. Tested: classify/extract can't reach sqlstore."""


class ToolNotAllowed(PermissionError):
    pass


class ToolCallError(RuntimeError):
    """A tool returned isError; `code` is the server's structured error code."""

    def __init__(self, tool: str, code: str, message: str) -> None:
        self.tool = tool
        self.code = code
        super().__init__(f"{tool}: {code}: {message}")


class ToolCaller(Protocol):
    async def call(self, tool: str, args: Mapping[str, Any]) -> Any: ...


def parse_error(tool: str, text: str) -> ToolCallError:
    """Servers raise ToolError with a JSON {code, message}; the SDK prefixes it."""
    body = text.split(": ", 1)[1] if text.startswith("Error executing tool") else text
    try:
        data = json.loads(body)
        return ToolCallError(tool, str(data.get("code", "tool_error")), str(data.get("message")))
    except (json.JSONDecodeError, AttributeError):
        return ToolCallError(tool, "tool_error", body)


class McpHub:
    """Open MCP clients to the four servers for the life of an `async with` block."""

    def __init__(self, urls: Mapping[Server, str], secret: str, timeout_s: float = 180) -> None:
        self._urls = dict(urls)
        self._secret = secret
        self._timeout = timeout_s
        self._clients: dict[Server, Client] = {}
        self._stack = AsyncExitStack()

    @classmethod
    def in_process(cls, servers: Mapping[Server, Any]) -> "McpHub":
        """Connect to MCPServer objects in memory (tests): same calls, no HTTP."""
        hub = cls({}, "")
        hub._in_process = dict(servers)
        return hub

    _in_process: dict[Server, Any] = {}  # noqa: RUF012 - replaced per instance

    async def __aenter__(self) -> "McpHub":
        try:
            for server, mcp_server in self._in_process.items():
                client = Client(mcp_server)
                self._clients[server] = await self._stack.enter_async_context(client)
            for server, url in self._urls.items():
                http = httpx2.AsyncClient(
                    headers={"x-mcp-secret": self._secret}, timeout=self._timeout
                )
                await self._stack.enter_async_context(http)
                client = Client(streamable_http_client(url, http_client=http))
                self._clients[server] = await self._stack.enter_async_context(client)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def call(self, tool: str, args: Mapping[str, Any]) -> Any:
        server = TOOL_SERVER[tool]
        result = await self._clients[server].call_tool(tool, dict(args))
        if result.is_error:
            text = "".join(str(getattr(c, "text", "")) for c in result.content)
            raise parse_error(tool, text)
        out: Any = result.structured_content
        if isinstance(out, dict) and set(cast(dict[str, Any], out)) == {"result"}:
            return cast(dict[str, Any], out)["result"]  # list-returning tools
        return cast(Any, out)


@dataclass(frozen=True)
class NodeTools:
    """A node's view of the tools: only its allowlist, nothing else."""

    node: str
    caller: ToolCaller

    @property
    def allowed(self) -> frozenset[str]:
        return NODE_TOOLS[self.node]

    async def call(self, tool: str, **args: Any) -> Any:
        if tool not in self.allowed:
            raise ToolNotAllowed(f"node {self.node!r} may not call {tool!r}")
        return await self.caller.call(tool, args)
