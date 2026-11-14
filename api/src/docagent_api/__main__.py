"""`uv run --env-file .env python -m docagent_api [--host H] [--port P]`

Runs uvicorn on a selector event loop (psycopg's async driver needs one on Windows).
"""

import argparse
import asyncio
import logging
import os

import uvicorn

from docagent_api.app import create_app
from docagent_api.settings import ApiSettings


def main() -> None:
    parser = argparse.ArgumentParser(description="DocAgent API")
    parser.add_argument("--host", default=os.environ.get("API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("API_PORT", "8000")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = create_app(ApiSettings.from_env())
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="info"))
    if os.name == "nt":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
