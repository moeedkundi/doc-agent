"""The extraction MCP server: classify, extract and re-extract documents with a vision LLM.

Every model call goes through `docagent_gateway` (tier routing, budget caps, cost ledger).
The model gets no tools, so text inside a document cannot trigger actions (SPEC §8.2).
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic import Field as PydanticField

from docagent_gateway import (
    BudgetExceeded,
    Completion,
    Gateway,
    ImageInput,
    OutputParseError,
    ProviderError,
)
from docagent_mcp_common import tool, tool_error
from docagent_mcp_extraction import paths, prompts
from docagent_mcp_extraction.pages import PageContent, page_count, render_image, render_pages
from docagent_mcp_extraction.store import DocumentNotFound, DocumentStore, UnsupportedDocument
from docagent_schemas import (
    ClassificationResult,
    DocumentType,
    ExtractionResult,
    Field,
    FieldValue,
    Invoice,
    LlmUsage,
    ModelTier,
    PurchaseOrder,
    RecordDocType,
    ReextractResult,
)

INSTRUCTIONS = (
    "Reads uploaded invoices and purchase orders with a vision model. Call classify_document "
    "first, then extract_fields; use reextract_fields on the stronger tier for only the field "
    "paths that failed validation or had low confidence."
)
READS_DOCUMENT = ToolAnnotations(read_only_hint=True, idempotent_hint=False, open_world_hint=False)


@dataclass(frozen=True)
class ExtractionSettings:
    render_dpi: int = 150
    max_pages: int = 5


class _Classification(BaseModel):
    doc_type: DocumentType
    confidence: float = PydanticField(ge=0, le=1)
    reason: str


class _FieldAnswer(BaseModel):
    path: str
    value: str | None
    confidence: float = PydanticField(ge=0, le=1)
    source_page: int | None = None
    evidence: str | None = None


class _Reextraction(BaseModel):
    fields: list[_FieldAnswer]


_SCHEMAS: dict[RecordDocType, type[Invoice] | type[PurchaseOrder]] = {
    "invoice": Invoice,
    "purchase_order": PurchaseOrder,
}

DocumentId = Annotated[UUID, PydanticField(description="Id returned by the upload API")]
SessionId = Annotated[
    UUID | None, PydanticField(description="Visitor session, for the per-session budget")
]
RunId = Annotated[UUID | None, PydanticField(description="Ingest run, recorded in the ledger")]


def _parsed[T: BaseModel](c: Completion[T]) -> T:
    if c.parsed is None:  # the gateway always parses when given a schema
        raise tool_error("unparseable_output", "model returned no structured output")
    return c.parsed


def _usage(c: Completion[Any]) -> LlmUsage:
    tier: Literal["default", "strong"] = "strong" if c.tier == "strong" else "default"
    return LlmUsage(
        tier=tier,
        model=c.model,
        calls=c.calls,
        tokens_in=c.tokens_in,
        tokens_out=c.tokens_out,
        cost_usd=c.cost_usd,
        latency_ms=c.latency_ms,
    )


def create_server(
    gateway: Gateway, store: DocumentStore, settings: ExtractionSettings | None = None
) -> MCPServer:
    cfg = settings or ExtractionSettings()
    server = MCPServer("docagent-extraction", instructions=INSTRUCTIONS, version="0.1.0")

    def load_pages(document_id: UUID, wanted: list[int] | None = None) -> list[PageContent]:
        try:
            doc = store.load(document_id)
        except DocumentNotFound as exc:
            raise tool_error("document_not_found", str(exc)) from None
        except UnsupportedDocument as exc:
            raise tool_error("unsupported_document", str(exc)) from None
        if doc.mime == "application/pdf":
            n = page_count(doc.data)
            if n > cfg.max_pages:
                raise tool_error(
                    "too_many_pages", f"document has {n} pages; the limit is {cfg.max_pages}"
                )
            pages = render_pages(doc.data, cfg.render_dpi)
        else:
            pages = render_image(doc.data)
        if wanted:
            unknown = sorted(set(wanted) - {p.number for p in pages})
            if unknown:
                raise tool_error(
                    "invalid_pages", f"pages {unknown} do not exist (document has {len(pages)})"
                )
            pages = [p for p in pages if p.number in set(wanted)]
        return pages

    async def call[T: BaseModel](
        messages: list[dict[str, Any]],
        schema: type[T],
        images: list[PageContent],
        tier: ModelTier,
        session_id: UUID | None,
        run_id: UUID | None,
    ) -> Completion[T]:
        try:
            return await gateway.complete(
                messages,
                tier=tier,
                response_schema=schema,
                images=[ImageInput(p.png) for p in images],
                session_id=session_id,
                run_id=run_id,
            )
        except BudgetExceeded as exc:
            raise tool_error("budget_exceeded", str(exc)) from None
        except OutputParseError as exc:
            raise tool_error("unparseable_output", str(exc)) from None
        except ProviderError as exc:
            # Message is already redacted by the gateway.
            raise tool_error("provider_unavailable", str(exc)) from None

    @tool(server, READS_DOCUMENT)
    async def classify_document(
        document_id: DocumentId, session_id: SessionId = None, run_id: RunId = None
    ) -> ClassificationResult:
        """Decide whether a document is an invoice, a purchase order or unknown."""
        pages = load_pages(document_id)
        out = await call(
            prompts.classification_messages(pages),
            _Classification,
            pages[:1],
            "default",
            session_id,
            run_id,
        )
        parsed = _parsed(out)
        return ClassificationResult(
            doc_type=parsed.doc_type,
            confidence=parsed.confidence,
            reason=parsed.reason,
            usage=_usage(out),
        )

    @tool(server, READS_DOCUMENT)
    async def extract_fields(
        document_id: DocumentId,
        doc_type: RecordDocType,
        model_tier: ModelTier = "default",
        pages: Annotated[
            list[int] | None, PydanticField(description="1-based pages to read; default all")
        ] = None,
        session_id: SessionId = None,
        run_id: RunId = None,
    ) -> ExtractionResult:
        """Extract every field of an invoice or purchase order.

        Each value comes with confidence (0-1), source page and an evidence snippet.
        """
        content = load_pages(document_id, pages)
        out = await call(
            prompts.extraction_messages(doc_type, content),
            _SCHEMAS[doc_type],
            content,
            model_tier,
            session_id,
            run_id,
        )
        parsed = _parsed(out)
        return ExtractionResult(
            doc_type=doc_type,
            record=parsed,
            pages=[p.number for p in content],
            usage=_usage(out),
        )

    @tool(server, READS_DOCUMENT)
    async def reextract_fields(
        document_id: DocumentId,
        doc_type: RecordDocType,
        field_paths: Annotated[
            list[str],
            PydanticField(
                min_length=1,
                max_length=40,
                description='Paths like "total", "seller.tax_id", "line_items[1].unit_price"',
            ),
        ],
        model_tier: ModelTier = "strong",
        session_id: SessionId = None,
        run_id: RunId = None,
    ) -> ReextractResult:
        """Re-read only the given field paths (cheaper than a full re-extraction).

        Paths the model leaves out, or answers in the wrong format, come back with value
        null and confidence 0 so validation routes them to human review.
        """
        types: dict[str, Any] = {}
        for path in dict.fromkeys(field_paths):
            try:
                types[path] = paths.value_type(_SCHEMAS[doc_type], path)
            except paths.UnknownFieldPath as exc:
                raise tool_error("unknown_field_path", str(exc)) from None
        content = load_pages(document_id)
        out = await call(
            prompts.reextraction_messages(
                doc_type, content, {p: paths.describe_type(t) for p, t in types.items()}
            ),
            _Reextraction,
            content,
            model_tier,
            session_id,
            run_id,
        )
        parsed = _parsed(out)
        answers = {a.path: a for a in parsed.fields}
        fields: dict[str, Field[FieldValue]] = {}
        for path, annotation in types.items():
            answer = answers.get(path)
            if answer is None:
                fields[path] = Field[FieldValue](
                    value=None, confidence=0.0, evidence="not returned"
                )
                continue
            try:
                value = TypeAdapter(annotation).validate_python(answer.value)
            except ValidationError:
                fields[path] = Field[FieldValue](
                    value=None, confidence=0.0, evidence=f"unreadable value {answer.value!r}"
                )
                continue
            fields[path] = Field[FieldValue](
                value=value,
                confidence=answer.confidence,
                source_page=answer.source_page,
                evidence=answer.evidence,
            )
        return ReextractResult(doc_type=doc_type, fields=fields, usage=_usage(out))

    return server
