"""`python -m docagent_mcp_validation`: serve on the port in MCP_VALIDATION_URL."""

from docagent_mcp_common import serve
from docagent_mcp_validation.server import create_server
from docagent_mcp_validation.settings import load_settings


def main() -> None:
    serve(create_server(load_settings()), "MCP_VALIDATION_URL")


if __name__ == "__main__":
    main()
