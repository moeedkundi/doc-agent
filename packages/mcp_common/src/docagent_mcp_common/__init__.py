"""Shared plumbing for DocAgent's MCP servers.

- `SharedSecretMiddleware`: every HTTP request must carry `x-mcp-secret: $MCP_SHARED_SECRET`.
- `serve()`: run an `MCPServer` over Streamable HTTP on the port from its `MCP_*_URL` env var.
- `tool_error()`: a `ToolError` with a JSON body `{"code": ..., "message": ...}` so the agent
  can branch on `code` (the model sees the same text).
- `load_limits()`: `config/limits.yaml` as a dict.
"""

import hmac
import inspect
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("docagent.mcp")

SECRET_HEADER = "x-mcp-secret"  # noqa: S105 - a header name, not a secret
MIN_SECRET_LENGTH = 16


class SharedSecretMiddleware:
    """Pure ASGI middleware: reject HTTP requests whose secret header doesn't match.

    Lifespan events pass through untouched so the MCP session manager still starts.
    Comparison is constant-time; the secret is never logged.
    """

    def __init__(self, app: ASGIApp, secret: str, header: str = SECRET_HEADER) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"shared secret must be at least {MIN_SECRET_LENGTH} characters")
        self._app = app
        self._secret = secret.encode()
        self._header = header.lower().encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            supplied = b""
            for name, value in scope.get("headers", []):
                if name == self._header:
                    supplied = value
                    break
            if not hmac.compare_digest(supplied, self._secret):
                body = json.dumps({"error": "unauthorized"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self._app(scope, receive, send)


def tool[F: Callable[..., Any]](
    server: MCPServer[Any], annotations: ToolAnnotations | None = None
) -> Callable[[F], F]:
    """`@server.tool()` with the docstring dedented (MCPServer passes it through verbatim)."""

    def register(fn: F) -> F:
        description = inspect.cleandoc(fn.__doc__) if fn.__doc__ else None
        return server.tool(annotations=annotations, description=description)(fn)

    return register


def tool_error(code: str, message: str) -> ToolError:
    return ToolError(json.dumps({"code": code, "message": message}))


@dataclass(frozen=True)
class BindConfig:
    host: str
    port: int
    path: str
    public_host: str
    """Host name clients use (from the URL), allowed by DNS-rebinding protection."""


def bind_config(url_env: str, env: Mapping[str, str] | None = None) -> BindConfig:
    """Port and path from e.g. MCP_EXTRACTION_URL=http://extraction:8101/mcp.

    Binds to MCP_BIND_HOST (default 127.0.0.1). In docker compose this is 0.0.0.0 on the
    private network only; no `ports:` mapping is ever published for MCP servers.
    """
    env = os.environ if env is None else env
    url = env.get(url_env)
    if not url:
        raise LookupError(f"{url_env} is not set")
    parts = urlsplit(url)
    if parts.port is None or parts.hostname is None:
        raise ValueError(f"{url_env} must include a host and port, e.g. http://name:8101/mcp")
    return BindConfig(
        host=env.get("MCP_BIND_HOST", "127.0.0.1"),
        port=parts.port,
        path=parts.path or "/mcp",
        public_host=parts.hostname,
    )


def shared_secret(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    secret = env.get("MCP_SHARED_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise LookupError(
            f"MCP_SHARED_SECRET must be set (at least {MIN_SECRET_LENGTH} characters)"
        )
    return secret


def build_app(server: MCPServer[Any], bind: BindConfig, secret: str) -> ASGIApp:
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{bind.public_host}:{bind.port}", "127.0.0.1:*", "localhost:*"],
        allowed_origins=[],
    )
    app = server.streamable_http_app(
        streamable_http_path=bind.path,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=bind.host,
    )
    return SharedSecretMiddleware(app, secret)


def serve(server: MCPServer[Any], url_env: str) -> None:
    """Entry point for `python -m <server>`: validate config, then run uvicorn."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        bind = bind_config(url_env)
        secret = shared_secret()
    except (LookupError, ValueError) as exc:
        print(f"{server.name}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    log.info("%s listening on http://%s:%d%s", server.name, bind.host, bind.port, bind.path)
    uvicorn.run(build_app(server, bind, secret), host=bind.host, port=bind.port, log_level="info")


def load_limits(config_dir: Path | None = None) -> dict[str, Any]:
    config_dir = config_dir or Path(os.environ.get("DOCAGENT_CONFIG_DIR", "config"))
    with (config_dir / "limits.yaml").open(encoding="utf-8") as f:
        data: object = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("limits.yaml must contain a mapping")
    return dict(data)  # pyright: ignore[reportUnknownArgumentType]


__all__ = [
    "SECRET_HEADER",
    "BindConfig",
    "SharedSecretMiddleware",
    "bind_config",
    "build_app",
    "load_limits",
    "serve",
    "shared_secret",
    "tool",
    "tool_error",
]
