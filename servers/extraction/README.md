# docagent-extraction (MCP server)

Reads uploaded invoices and purchase orders with a vision LLM and returns typed, per-field
results: every value carries a **confidence** (0–1), the **source page** and a short
**evidence** snippet. All model calls go through `packages/gateway`, which handles tier
routing, retries, budget caps and the cost ledger. The model is given no tools, so
instructions hidden inside a document can't trigger actions. Document text is fenced and
labelled as untrusted in every prompt.

## Run

```bash
uv run --env-file .env python -m servers.extraction        # or: python -m docagent_mcp_extraction
```

| Env var | Purpose |
|---|---|
| `MCP_EXTRACTION_URL` | e.g. `http://extraction:8101/mcp`. The server takes its **port and path** from this |
| `MCP_BIND_HOST` | bind address, default `127.0.0.1` (`0.0.0.0` only inside the private compose network) |
| `MCP_SHARED_SECRET` | required, ≥ 16 chars. Every request must send it as the `x-mcp-secret` header, or gets `401` |
| `UPLOAD_DIR` | where the API stores uploads as `<document_id>.pdf/.png/.jpg` |
| `GEMINI_API_KEY`, `DAILY_BUDGET_USD`, `SESSION_BUDGET_USD` | gateway; the server refuses to start without budget caps |
| `DATABASE_APP_URL` (or `DATABASE_URL`) | cost ledger (`llm_ledger`) |

Transport is Streamable HTTP (stateless, JSON responses), and DNS-rebinding protection only
accepts the host from the URL plus localhost. This is an internal service: never publish its
port.

## Tools

| Tool | Input | Output |
|---|---|---|
| `classify_document` | `document_id` (UUID), `session_id?`, `run_id?` | `ClassificationResult {doc_type: invoice\|purchase_order\|unknown, confidence, reason, usage}` |
| `extract_fields` | `document_id`, `doc_type` (`invoice`\|`purchase_order`), `model_tier` (`default`\|`strong`, default `default`), `pages?` (1-based), `session_id?`, `run_id?` | `ExtractionResult {doc_type, record: Invoice\|PurchaseOrder, pages, usage}`. Every leaf of `record` is `{value, confidence, source_page, evidence}` |
| `reextract_fields` | `document_id`, `doc_type`, `field_paths` (1–40, e.g. `"total"`, `"seller.tax_id"`, `"line_items[1].unit_price"`), `model_tier` (default **`strong`**), `session_id?`, `run_id?` | `ReextractResult {doc_type, fields: {path: Field}, usage}`. Only the requested paths. A path the model skips or answers in the wrong format comes back `value: null, confidence: 0`, which validation sends to review |

`usage` = `{tier, model, calls, tokens_in, tokens_out, cost_usd, latency_ms}` is what the
call spent through the gateway. The agent logs it as a trace event. `session_id` enables the
per-session budget; `run_id` is stored on the ledger rows.

The output types are defined in `packages/schemas` (`docagent_schemas.tools`,
`docagent_schemas.extracted`), and their JSON Schemas are in `packages/schemas/json/`.

### Errors

Tool errors come back as `isError: true` with a JSON body `{"code": ..., "message": ...}`:

| `code` | When |
|---|---|
| `document_not_found` | no upload with that id |
| `unsupported_document` | file isn't a PDF/PNG/JPEG by magic bytes |
| `too_many_pages` | over `uploads.max_pages` in `config/limits.yaml` |
| `invalid_pages` | `pages` names a page the document doesn't have |
| `unknown_field_path` | `reextract_fields` path isn't a field of the schema |
| `budget_exceeded` | daily or per-session cap reached, so nothing was sent to the provider |
| `provider_unavailable` | provider kept failing after retries and fallback (message redacted) |
| `unparseable_output` | model output didn't match the schema even after one repair attempt |

A `document_id` that isn't a UUID is rejected by the input schema, so a path can never be
passed.

## Example

MCP Inspector (CLI):

```bash
npx @modelcontextprotocol/inspector@latest --cli http://127.0.0.1:8101/mcp --transport http \
  --header "x-mcp-secret: $MCP_SHARED_SECRET" \
  --method tools/call --tool-name extract_fields \
  --tool-arg document_id=e4b81445-24bc-4d87-97cb-f1d673a1a456 --tool-arg doc_type=invoice
```

Python (`mcp` ≥ 2.3):

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

http = httpx2.AsyncClient(headers={"x-mcp-secret": secret})
async with Client(streamable_http_client("http://127.0.0.1:8101/mcp", http_client=http)) as c:
    res = await c.call_tool("extract_fields", {"document_id": doc_id, "doc_type": "invoice"})
    res.structured_content["record"]["total"]
    # {"value": 5890.66, "confidence": 1, "source_page": 1, "evidence": "5,890.66"}
```

## Tests

`uv run pytest servers/extraction`. These tests use the real gateway with a fake provider:
no network, no key.
