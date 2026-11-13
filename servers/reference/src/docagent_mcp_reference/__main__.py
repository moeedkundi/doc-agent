"""`python -m docagent_mcp_reference`: serve on the port in MCP_REFERENCE_URL."""

from docagent_mcp_common import serve
from docagent_mcp_reference.server import create_server


def main() -> None:
    serve(create_server(), "MCP_REFERENCE_URL")


if __name__ == "__main__":
    main()
