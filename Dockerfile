# One image for every backend service (API + agent, the 4 MCP servers, migrations, cleanup);
# docker-compose.prod.yml picks the command. Workspace packages are installed editable from
# /app, so config/, db/migrations, the sample PDFs, replay fixtures and the eval report are
# read from the same paths as in development.

FROM ghcr.io/astral-sh/uv:0.8-python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first (cached unless the lockfile or a member's pyproject changes).
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY servers/ servers/
COPY agent/ agent/
COPY api/ api/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY config/ config/
COPY db/ db/
COPY evals/dataset/samples/ evals/dataset/samples/
COPY evals/reports/latest.json evals/reports/latest.md evals/reports/

FROM python:3.12-slim-bookworm
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /data/uploads && chown app:app /data/uploads
COPY --from=build --chown=app:app /app /app
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DOCAGENT_CONFIG_DIR=/app/config \
    UPLOAD_DIR=/data/uploads
USER app
CMD ["python", "-m", "docagent_api", "--host", "0.0.0.0", "--port", "8000"]
