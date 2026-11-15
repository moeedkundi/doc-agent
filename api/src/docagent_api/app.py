"""The DocAgent HTTP API (SPEC §9).

Sessions: an anonymous session is created on the first request and kept in an HttpOnly,
SameSite=Lax cookie; every DB call runs with `app.session_id` set, so RLS applies.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from functools import partial
from typing import Annotated, Any
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from docagent_agent.ingest import ReviewDecision
from docagent_agent.runtime import AgentConfig, Runtime, build_runtime
from docagent_agent.tools import McpHub, Server
from docagent_agent.trace import PgTraceSink
from docagent_api.db import Db
from docagent_api.documents import mime_of, page_image
from docagent_api.events import stream_run
from docagent_api.guards import ApiError, RateLimiter, check_upload, hash_ip, parse_rate
from docagent_api.runs import ReplayFixtures, RunService
from docagent_api.settings import ApiSettings
from docagent_gateway import BudgetExceeded, Gateway, PostgresLedger, load_config

log = logging.getLogger("docagent.api")
COOKIE = "docagent_session"
SAMPLE_IDS = ("po_001", "po_002", "po_003", "inv_001", "inv_002", "inv_003")


@dataclass
class Services:
    db: Db
    runs: RunService
    gateway: Gateway | None
    limiter: RateLimiter
    mcp_probe: Callable[[], Awaitable[dict[str, str]]]


class RunRequest(BaseModel):
    document_id: UUID
    replay: bool = Field(default=False, description="Replay the recorded run of a sample")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def _error(e: ApiError, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": e.code, "message": e.message, **e.extra}, e.status, headers=headers
    )


def _budget_error(exc: BudgetExceeded) -> ApiError:
    scope = "today's demo budget" if exc.scope == "daily" else "this session's budget"
    return ApiError(
        402,
        "budget_exceeded",
        f"The LLM budget for {scope} is used up. You can still watch recorded "
        "runs of the sample documents.",
        scope=exc.scope,
        replay_available=True,
        replay_samples=list(SAMPLE_IDS),
    )


def _replay_only_error() -> ApiError:
    return ApiError(
        503,
        "replay_only",
        "Live processing is off right now; the demo is "
        "replaying recorded runs of the sample documents.",
        replay_available=True,
        replay_samples=list(SAMPLE_IDS),
    )


async def probe_mcp(urls: dict[str, str], secret: str, timeout_s: float = 3.0) -> dict[str, str]:
    async def one(name: str, url: str) -> tuple[str, str]:
        if not url:
            return name, "not configured"
        try:
            async with asyncio.timeout(timeout_s), McpHub({name: url}, secret, timeout_s):  # type: ignore[dict-item]
                return name, "ok"
        except Exception as exc:
            return name, f"unreachable ({type(exc).__name__})"

    results = await asyncio.gather(*(one(n, u) for n, u in urls.items()))
    return dict(results)


def create_app(settings: ApiSettings, services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            if services is not None:
                app.state.services = services
            else:
                app.state.services = await _build_services(settings, stack)
            yield
            await app.state.services.runs.drain()

    app = FastAPI(title="DocAgent API", version="0.1.0", lifespan=lifespan)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Last-Event-ID"],
        )
    # JSON is compressed; Starlette leaves text/event-stream alone, so SSE still streams.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    def svc(request: Request) -> Services:
        return request.app.state.services

    def client_key(request: Request) -> str:
        ip = request.client.host if request.client else "unknown"
        if settings.trust_proxy and (fwd := request.headers.get("x-forwarded-for")):
            ip = fwd.split(",")[0].strip()
        return hash_ip(ip, settings.ip_hash_salt)

    def limit(request: Request, rule: str) -> None:
        retry = svc(request).limiter.hit(rule, client_key(request))
        if retry is not None:
            what = "runs" if rule == "runs" else "questions"
            count, window = svc(request).limiter.rules[rule]
            raise ApiError(
                429,
                "rate_limited",
                f"Slow down a little: you can start {count} {what} every "
                f"{int(window // 60)} minutes. Try again in {retry} seconds.",
                retry_after_seconds=retry,
            )

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        retry = exc.extra.get("retry_after_seconds")
        return _error(exc, {"Retry-After": str(retry)} if retry else None)

    @app.exception_handler(BudgetExceeded)
    async def budget_error(_: Request, exc: BudgetExceeded) -> JSONResponse:
        return _error(_budget_error(exc))

    @app.middleware("http")
    async def session_cookie(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
            return await call_next(request)
        db = svc(request).db
        sid: UUID | None = None
        raw = request.cookies.get(COOKIE)
        if raw:
            try:
                candidate = UUID(raw)
                if await asyncio.to_thread(db.session_active, candidate):
                    sid = candidate
            except ValueError:
                sid = None
        created = sid is None
        if sid is None:
            sid = await asyncio.to_thread(
                db.create_session, client_key(request), settings.session_ttl_hours
            )
        request.state.session_id = sid
        response = await call_next(request)
        if created:
            response.set_cookie(
                COOKIE,
                str(sid),
                max_age=settings.session_ttl_hours * 3600,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
                domain=settings.cookie_domain,
                path="/",
            )
        return response

    def sid_of(request: Request) -> UUID:
        return request.state.session_id

    # -- session & samples --------------------------------------------------------------------
    @app.post("/api/session")
    async def session(request: Request) -> dict[str, str]:
        """Ensure an anonymous session exists (the cookie is set on first contact)."""
        return {"session_id": str(sid_of(request))}

    @app.get("/api/samples")
    async def samples() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        fixtures = app.state.services.runs.fixtures.by_sample
        for sample_id in SAMPLE_IDS:
            truth = json.loads((settings.samples_dir / f"{sample_id}.json").read_text("utf-8"))
            record: dict[str, Any] = truth["record"]
            party: dict[str, Any] = record.get("seller") or record.get("vendor") or {}
            expected: dict[str, Any] = truth.get("expected_match") or {}
            out.append(
                {
                    "id": sample_id,
                    "doc_type": truth["doc_type"],
                    "template": truth["template"],
                    "scanned": truth["scanned"],
                    "number": record.get("invoice_number") or record.get("po_number"),
                    "party": party.get("name"),
                    "currency": record.get("currency"),
                    "total": record.get("total"),
                    "lines": len(record.get("line_items") or []),
                    "references_po": record.get("po_number")
                    if truth["doc_type"] == "invoice"
                    else None,
                    "expected_match": expected.get("status"),
                    "replay_available": sample_id in fixtures,
                }
            )
        return out

    @app.post("/api/samples/{sample_id}/document", status_code=201)
    async def sample_document(sample_id: str, request: Request) -> dict[str, Any]:
        if sample_id not in SAMPLE_IDS:
            raise ApiError(404, "sample_not_found", f"No sample {sample_id!r}.")
        data = (settings.samples_dir / f"{sample_id}.pdf").read_bytes()
        return await _store(request, data, f"{sample_id}.pdf", sample_id)

    # -- documents ----------------------------------------------------------------------------
    @app.post("/api/documents", status_code=201)
    async def upload(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        if settings.replay_only:
            raise _replay_only_error()
        data = await file.read(settings.max_upload_bytes + 1)
        return await _store(request, data, file.filename or "upload", None)

    async def _store(
        request: Request, data: bytes, filename: str, sample_id: str | None
    ) -> dict[str, Any]:
        checked = check_upload(
            data, filename, max_bytes=settings.max_upload_bytes, max_pages=settings.max_pages
        )
        doc_id = uuid4()
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        stored = f"{doc_id}{checked.ext}"
        await asyncio.to_thread((settings.upload_dir / stored).write_bytes, data)
        await asyncio.to_thread(
            svc(request).db.insert_document,
            sid_of(request),
            doc_id,
            filename[:200],
            stored,
            checked.pages,
            sample_id,
        )
        return {
            "document_id": str(doc_id),
            "filename": filename,
            "pages": checked.pages,
            "type": checked.mime,
            "sample_id": sample_id,
        }

    async def _document(request: Request, document_id: UUID) -> dict[str, Any]:
        doc = await asyncio.to_thread(svc(request).db.get_document, sid_of(request), document_id)
        if doc is None:
            raise ApiError(404, "document_not_found", "No such document in this session.")
        return doc

    @app.get("/api/documents/{document_id}")
    async def document_meta(document_id: UUID, request: Request) -> dict[str, Any]:
        doc = await _document(request, document_id)
        return {
            "document_id": str(doc["id"]),
            "filename": doc["filename"],
            "pages": doc["pages"],
            "type": mime_of(doc["storage_path"]),
            "sample_id": doc["sample_id"],
        }

    @app.get("/api/documents/{document_id}/file")
    async def document_file(document_id: UUID, request: Request) -> FileResponse:
        doc = await _document(request, document_id)
        return FileResponse(settings.upload_dir / doc["storage_path"], filename=doc["filename"])

    @app.get("/api/documents/{document_id}/pages/{page}")
    async def document_page(document_id: UUID, page: int, request: Request) -> Response:
        """One page as an image for the viewer (PDF pages as WebP; images served as they are)."""
        doc = await _document(request, document_id)
        if not 1 <= page <= int(doc["pages"] or 1):
            raise ApiError(404, "page_not_found", "No such page.")
        path = settings.upload_dir / doc["storage_path"]
        headers = {"Cache-Control": "private, max-age=86400"}
        if mime_of(doc["storage_path"]) != "application/pdf":
            return FileResponse(path, media_type=mime_of(doc["storage_path"]), headers=headers)
        image = await asyncio.to_thread(page_image, path, page)
        return FileResponse(image, media_type="image/webp", headers=headers)

    # -- runs ---------------------------------------------------------------------------------
    @app.post("/api/runs", status_code=202)
    async def start_run(body: RunRequest, request: Request) -> dict[str, Any]:
        limit(request, "runs")
        s, sid = svc(request), sid_of(request)
        doc = await asyncio.to_thread(s.db.get_document, sid, body.document_id)
        if doc is None:
            raise ApiError(404, "document_not_found", "No such document in this session.")
        replay = settings.replay_only or body.replay
        if replay:
            fixture = s.runs.fixtures.by_sample.get(doc["sample_id"] or "")
            if fixture is None:
                raise (
                    _replay_only_error()
                    if settings.replay_only
                    else ApiError(
                        409, "no_recording", "Only the sample documents have recorded runs."
                    )
                )
            run_id = await asyncio.to_thread(
                s.db.create_run, sid, kind="ingest", document_id=body.document_id, replay=True
            )
            s.runs.start_replay(sid, run_id, fixture)
        else:
            if s.gateway is not None:
                await s.gateway.check_budget(sid)  # BudgetExceeded -> 402
            run_id = await asyncio.to_thread(
                s.db.create_run, sid, kind="ingest", document_id=body.document_id
            )
            s.runs.start_ingest(sid, run_id, body.document_id)
        return {"run_id": str(run_id), "replay": replay, "events": f"/api/runs/{run_id}/events"}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: UUID, request: Request) -> dict[str, Any]:
        s, sid = svc(request), sid_of(request)
        run = await asyncio.to_thread(s.db.get_run, sid, run_id)
        if run is None:
            raise ApiError(404, "run_not_found", "No such run in this session.")
        status = run["status"]
        review = await s.runs.review_payload(run_id) if status == "awaiting_review" else None
        if status == "awaiting_review" and review is None:
            status = "running"  # paused, but the interrupt isn't checkpointed yet
        return {
            "id": str(run["id"]),
            "kind": run["kind"],
            "status": status,
            "replay": run["replay"],
            "document_id": run["document_id"] and str(run["document_id"]),
            "question": run["question"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "total_cost_usd": str(run["total_cost_usd"]),
            "escalated": run["escalated"],
            "error": {
                "code": run["error_code"],
                "message": run["error"],
                "replay_available": run["error_code"] == "budget_exceeded",
            }
            if run["error_code"]
            else None,
            "result": run["result_json"],
            "review": review,
        }

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: UUID, request: Request, after: int | None = None
    ) -> EventSourceResponse:
        s, sid = svc(request), sid_of(request)
        if await asyncio.to_thread(s.db.get_run, sid, run_id) is None:
            raise ApiError(404, "run_not_found", "No such run in this session.")
        last = request.headers.get("last-event-id")
        start = int(last) if last and last.isdigit() else (after or 0)
        return EventSourceResponse(
            stream_run(s.db, sid, run_id, start, s.runs.review_payload), ping=15
        )

    @app.post("/api/runs/{run_id}/resume", status_code=202)
    async def resume(run_id: UUID, decision: ReviewDecision, request: Request) -> dict[str, Any]:
        s, sid = svc(request), sid_of(request)
        run = await asyncio.to_thread(s.db.get_run, sid, run_id)
        if run is None:
            raise ApiError(404, "run_not_found", "No such run in this session.")
        if run["status"] != "awaiting_review":
            raise ApiError(409, "not_awaiting_review", f"The run is {run['status']}.")
        if decision.action == "edit" and decision.record is None:
            raise ApiError(422, "record_required", "An edit needs the full edited record.")
        await s.runs.sink.set_run(run_id, sid, status="running")  # a second resume gets 409
        s.runs.resume(sid, run_id, decision)
        return {"run_id": str(run_id), "status": "running"}

    # -- ask ----------------------------------------------------------------------------------
    @app.post("/api/ask")
    async def ask(body: AskRequest, request: Request) -> EventSourceResponse:
        limit(request, "questions")
        if settings.replay_only:
            raise _replay_only_error()
        s, sid = svc(request), sid_of(request)
        if s.gateway is not None:
            await s.gateway.check_budget(sid)
        run_id = await asyncio.to_thread(s.db.create_run, sid, kind="ask", question=body.question)
        s.runs.start_ask(sid, run_id, body.question)
        return EventSourceResponse(
            stream_run(s.db, sid, run_id, 0, s.runs.review_payload),
            ping=15,
            headers={"X-Run-Id": str(run_id)},
        )

    @app.get("/api/limits")
    async def limits() -> dict[str, Any]:
        """What the UI tells visitors before they upload (from config/limits.yaml)."""
        count, window = parse_rate(settings.run_limit)
        return {
            "max_upload_bytes": settings.max_upload_bytes,
            "max_pages": settings.max_pages,
            "file_types": ["PDF", "PNG", "JPEG"],
            "runs_per_window": count,
            "window_minutes": int(window // 60),
            "session_ttl_hours": settings.session_ttl_hours,
            "replay_only": settings.replay_only,
        }

    # -- evals & health -------------------------------------------------------------------------
    @app.get("/api/evals/latest")
    async def evals_latest(format: str = "json") -> Any:
        """The latest full eval report (evals/reports/latest.json, or `?format=md`)."""
        path = settings.evals_report if format != "md" else settings.evals_report.with_suffix(".md")
        if not path.is_file():
            raise ApiError(404, "no_report", "No eval report yet.")
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        if format == "md":
            return Response(text, media_type="text/markdown; charset=utf-8")
        return json.loads(text)

    @app.get("/api/health")
    async def health(request: Request) -> JSONResponse:
        s = svc(request)
        try:
            await asyncio.to_thread(s.db.ping)
            db_status = "ok"
        except Exception as exc:
            db_status = f"error ({type(exc).__name__})"
        mcp = await s.mcp_probe()
        midnight = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
        try:
            spent = await asyncio.to_thread(s.db.spent_since, midnight)
        except Exception:
            spent = None
        budget = {
            "daily_cap_usd": str(settings.daily_budget_usd),
            "spent_today_usd": None if spent is None else str(spent),
            "remaining_today_usd": None
            if spent is None
            else str(max(Decimal(0), settings.daily_budget_usd - spent)),
        }
        live_ok = settings.replay_only or all(v == "ok" for v in mcp.values())
        ok = db_status == "ok" and live_ok
        body = {
            "status": "ok" if ok else "degraded",
            "replay_only": settings.replay_only,
            "db": db_status,
            "mcp": mcp,
            "budget": budget,
        }
        return JSONResponse(body, 200 if ok else 503)

    return app


async def _build_services(settings: ApiSettings, stack: AsyncExitStack) -> Services:
    connect = partial(psycopg.connect, settings.app_dsn, autocommit=True, connect_timeout=5)
    db = Db(connect)
    sink = PgTraceSink(connect)
    fixtures = ReplayFixtures.load(settings.fixtures_dir)
    runtime: Runtime | None = None
    gateway: Gateway | None = None
    if not settings.replay_only:
        cfg = AgentConfig.from_env()
        checkpointer = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(settings.checkpoint_dsn)
        )
        await checkpointer.setup()
        hub = await stack.enter_async_context(McpHub(cfg.mcp_urls, cfg.mcp_secret))
        gateway = Gateway(load_config(), PostgresLedger(settings.app_dsn))
        runtime = build_runtime(cfg, hub, checkpointer, gateway, connect)
    urls: dict[Server, str] = {k: v for k, v in settings.mcp_urls.items()}  # type: ignore[misc]
    return Services(
        db=db,
        runs=RunService(db, sink, runtime, fixtures, settings.replay_speed),
        gateway=gateway,
        limiter=RateLimiter(
            {
                "runs": parse_rate(settings.run_limit),
                "questions": parse_rate(settings.question_limit),
            }
        ),
        mcp_probe=partial(probe_mcp, urls, settings.mcp_secret),  # type: ignore[arg-type]
    )
