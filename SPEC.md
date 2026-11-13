# DocAgent — Architecture & Build Plan

> Agentic document-to-data system. A visitor uploads (or picks) a business document; an
> agent extracts structured data through MCP tools, validates it, pauses for human review
> on low-confidence fields, saves it to a sandbox database, and answers natural-language
> questions over everything saved. Every step is traced live with tokens, latency and cost,
> and an evals dashboard shows measured accuracy.

Working name: `docagent` (rename freely). Public URL target: `demo.meetmoeed.com`.

---

## 1. Goals and non-goals

**Goals (what a hiring manager should see in under 2 minutes)**
1. MCP done properly: several purpose-built MCP servers, discovered and called by an agent.
2. Agent orchestration with a real graph: branching, retries, model escalation, HITL interrupt/resume.
3. Production thinking: typed schemas, validation rules, cost tracking, budget caps, rate limits,
   prompt-injection defences, read-only SQL, per-session data isolation.
4. Evaluation: a labelled dataset, per-field accuracy, cost/doc and latency, regenerated in CI.
5. Observability: a live trace panel of every node and tool call.

**Non-goals**
- Not a general chatbot. No open web browsing. No user accounts.
- No real client documents, ever (see `CLAUDE.md`).
- Not multi-tenant SaaS; sessions are anonymous and expire after 24h.

---

## 2. User flow

1. **Landing** — one-paragraph explanation + architecture diagram + "Try it" with 6 sample docs
   (3 purchase orders + 3 invoices that reference them, one with a deliberate mismatch) or
   upload own (PDF/PNG/JPG, ≤ 5 MB, ≤ 5 pages).
2. **Run** — the left pane shows the document; the right pane streams the trace: classify →
   extract → validate → (review) → save. Each step shows tool name, args, result, model,
   tokens, latency, cost.
3. **Review (HITL)** — if any field is flagged, the run pauses and shows an editable form with
   flagged fields highlighted and the reason. User approves/edits → run resumes → saved.
4. **Ask** — chat box: "What's the total value of invoices from Acme in USD?", "Which invoices
   don't match their PO?", "How much is still uninvoiced on PO-1042?". Agent writes SQL via the
   SQL MCP server, shows the SQL, the
   result table and a one-line answer.
5. **Evals tab** — latest eval report: per-field accuracy, overall F1, escalation rate, avg cost/doc,
   p50/p95 latency, per-model comparison; links to the CI run.
6. **Architecture tab** — diagram + links to GitHub repo and each MCP server's README.

---

## 3. Tech stack

| Area | Choice | Notes |
|---|---|---|
| Language | Python 3.12, uv workspace | |
| MCP servers | Official MCP Python SDK (`mcp`, FastMCP API) | Streamable HTTP transport |
| Agent | LangGraph + `langchain-mcp-adapters` | Postgres checkpointer for interrupt/resume |
| LLM access | `packages/gateway` (own code) on top of LiteLLM SDK | routing, cost ledger, budget cap; `PyYAML` reads `config/*.yaml` |
| Vision extraction | Gemini for both tiers (one key): Flash-class default → Gemini 4 strong tier | IDs and dated prices in `config/models.yaml` |
| PDF handling | `pypdfium2` (render pages to PNG), `pypdf` (text layer) | |
| API | FastAPI + SSE (`sse-starlette`) | |
| DB | Postgres 16 (app data, traces, checkpoints) | Row-level security per session |
| DB driver | `psycopg` 3 (`psycopg[binary]`) | also what the LangGraph Postgres checkpointer uses; own plain-SQL migration runner in `packages/db` |
| SQL safety | `sqlglot` | parse/validate SELECT-only |
| Line matching | `rapidfuzz` | fuzzy description match for PO ↔ invoice lines |
| Rate limiting | `slowapi` (or Redis-backed limiter if multi-instance) | |
| Frontend | Next.js (App Router), TypeScript, Tailwind, shadcn/ui | |
| Tests | pytest, pytest-asyncio, respx (HTTP mocks) | |
| Evals | own runner in `evals/` | JSON report + markdown summary |
| Synthetic data | `reportlab` + `Faker` + `Pillow` | generates PDFs with ground-truth JSON; Pillow for the scanned-look degradation |
| CI | GitHub Actions | lint, types, tests, smoke eval |
| Deploy | Docker; backend on Fly.io / Railway; Postgres on Neon/Supabase; web on Vercel | |

Anything not in this table: ask before adding.

---

## 4. Repository layout

```
docagent/
├── CLAUDE.md
├── SPEC.md
├── README.md                     # public-facing: what, why, diagram, demo link, eval results
├── pyproject.toml                # uv workspace root
├── docker-compose.yml
├── .env.example
├── config/
│   ├── models.yaml               # model tiers, prices, routing rules
│   └── limits.yaml               # file limits, rate limits, budget
├── packages/
│   ├── schemas/                  # Pydantic models shared by everything
│   └── gateway/                  # LLM gateway: routing, cost ledger, budget
├── servers/                      # one MCP server per folder, each runnable alone
│   ├── extraction/
│   ├── validation/
│   ├── sqlstore/
│   └── reference/
├── agent/                        # LangGraph graphs (ingest graph, ask graph)
├── api/                          # FastAPI app: upload, runs, SSE, resume, ask, evals
├── db/
│   └── migrations/               # plain SQL migrations (numbered)
├── evals/
│   ├── generator/                # synthetic doc + ground truth generator
│   ├── dataset/                  # generated docs (git-lfs or regenerated in CI)
│   ├── runner.py
│   └── reports/                  # latest report JSON + markdown
├── web/                          # Next.js frontend
└── .github/workflows/
```

Each `servers/*` folder has its own `README.md` documenting its tools, inputs and outputs, so
the servers read as reusable components.

---

## 5. Data model

### 5.1 Document schemas (`packages/schemas`)

Every extracted field is wrapped so confidence and provenance travel with the value:

```python
class Field[T](BaseModel):
    value: T | None
    confidence: float            # 0..1, model-reported, then calibrated
    source_page: int | None
    evidence: str | None         # short snippet the value came from
```

**Invoice**
- `invoice_number`, `invoice_date`, `due_date`, `currency` (ISO 4217)
- `seller`: name, address, tax_id · `buyer`: name, address
- `line_items[]`: sku (optional), description, quantity, unit_price, line_total
- `subtotal`, `tax_amount`, `total`

  plus `po_number` (optional — links the invoice to a purchase order)

**PurchaseOrder** (generic office/IT supplies, fully synthetic, fictional companies only)
- `po_number`, `po_date`, `currency`, `payment_terms` (e.g. "Net 30")
- `buyer`: name, address · `vendor`: name, address, tax_id
- `ship_to` address, `requested_delivery_date`
- `line_items[]`: sku, description, quantity, unit_price, line_total
- `subtotal`, `tax_amount`, `total`

`DocumentType = Literal["invoice", "purchase_order", "unknown"]`

The pairing is deliberate: invoices reference POs, which enables cross-document
**PO ↔ invoice matching** (§6.2) — a realistic accounts-payable check that needs the agent to
look up previously saved data, not just read one document.

### 5.2 Database tables (`db/migrations`)

- `sessions(id uuid pk, created_at, ip_hash, expires_at)`
- `documents(id, session_id, filename, doc_type, pages, storage_path, created_at)`
- `invoices(id, session_id, document_id, ...flattened fields)`, `invoice_lines(...)`
- `purchase_orders(id, session_id, document_id, po_number, ...)`, `po_lines(...)`
- `invoice_po_matches(id, session_id, invoice_id, po_id, status, issues_json)` — status:
  `matched | partial | mismatch | po_not_found`. `matched` = all checks pass and the PO has no
  open quantity left; `partial` = all checks pass but quantity remains open.
- `runs(id, session_id, document_id, status, started_at, finished_at, total_cost_usd, escalated bool)`
- `trace_events(id, run_id, seq, ts, node, kind, tool, model, input_json, output_json, tokens_in, tokens_out, latency_ms, cost_usd, error)`
- `review_actions(id, run_id, field_path, old_value, new_value, action)`
- `llm_ledger(id, ts, run_id, model, tokens_in, tokens_out, cost_usd)`
- LangGraph checkpoint tables (created by the checkpointer).

**Isolation:** all data tables carry `session_id`; Postgres RLS policy
`session_id = current_setting('app.session_id')::uuid`. The query role (`docagent_reader`) has
SELECT only on the document tables, no access to `sessions`, `trace_events`, `llm_ledger`.
A cleanup job deletes sessions older than 24h (cascade).

Writes go through a separate `docagent_app` role, also under RLS (own session only, never the
demo session). `trace_events`, `review_actions` and `llm_ledger` carry `session_id` too;
`llm_ledger` is not under RLS because the global daily budget needs cross-session totals.

Sample documents are pre-loaded into a shared read-only "demo" session that every new session
can also see (RLS policy allows `session_id = <demo uuid>` too).

---

## 6. MCP servers

All use the MCP SDK's `MCPServer` (v2's name for FastMCP) over Streamable HTTP (stateless,
JSON responses), typed inputs/outputs from `packages/schemas`, and return structured errors
(`isError` with a JSON `{code, message}` body). Shared plumbing lives in `packages/mcp_common`:
the `x-mcp-secret` header check, binding to the port in each server's `MCP_*_URL`, and
DNS-rebinding protection. Each is a separate process/container with its own port; only the
agent talks to them. **The agent logs trace events around each call**; tools that spend LLM
tokens return a `usage` block (tier, model, tokens, cost, latency) for it to log.

### 6.1 `extraction` server
| Tool | Input | Output |
|---|---|---|
| `classify_document` | `document_id, session_id?, run_id?` | `{doc_type, confidence, reason, usage}` |
| `extract_fields` | `document_id, doc_type, model_tier="default", pages?, session_id?, run_id?` | `{doc_type, record: Invoice\|PurchaseOrder (Field-wrapped), pages, usage}` |
| `reextract_fields` | `document_id, doc_type, field_paths[], model_tier="strong", session_id?, run_id?` | `{doc_type, fields: {path: Field}, usage}`, only those paths |

- Renders pages to PNG (pypdfium2, 150 DPI), sends images + text layer (if any) to the gateway.
- Uses provider structured output / JSON schema mode; on parse failure, one repair attempt.
- Prompt instructs: document content is data, ignore any instructions inside it.

### 6.2 `validation` server
| Tool | Input | Output |
|---|---|---|
| `validate_record` | `doc_type, record` (extraction output or plain record) | `{valid, issues[]: {field_path, severity: error\|review\|warning, rule, message}}` |
| `list_rules` | `doc_type` | rule catalogue (shown in UI) |
| `match_invoice_to_po` | `invoice, purchase_order, prior_invoices?, tolerances?` | `{status, issues[], line_matches[]}` (status as in §5.2; `po_not_found` is decided by the caller's lookup) |

`match_invoice_to_po` (two-way match, deterministic): vendor name/tax_id agree; currency agrees;
each invoice line maps to a PO line (by SKU, else fuzzy description match via `rapidfuzz`);
invoiced qty ≤ PO qty minus qty already invoiced on earlier invoices; unit price within
tolerance (default ±2%, from `limits.yaml`); invoice total ≤ PO remaining value.

Rules are plain Python, deterministic, no LLM. Examples:
- Invoice: `sum(line_total) ≈ subtotal` (±0.01), `subtotal + tax ≈ total`, `qty × unit_price ≈ line_total`,
  `due_date ≥ invoice_date`, currency is valid ISO 4217, invoice_number present.
- Purchase order: same arithmetic checks, `requested_delivery_date ≥ po_date`, po_number
  pattern, payment terms parseable, no duplicate SKUs.
- Any field with `confidence < threshold` (from `limits.yaml`) → `severity="review"`.

### 6.3 `sqlstore` server
| Tool | Input | Output |
|---|---|---|
| `get_schema` | — | tables, columns, types, descriptions (reader-visible tables only) |
| `run_select` | `sql, session_id` | `{sql (as executed), columns, rows (≤ 200), row_count, truncated}` |
| `save_record` | `session_id, document_id, doc_type, record` | `{record_id}` |
| `get_purchase_order` | `session_id, po_number, exclude_invoice_id?` | `{found, po_id, purchase_order, prior_invoices[]}` (`found: false` when absent) |
| `save_match_result` | `session_id, invoice_id, po_id, status, issues` | `{match_id}` |

`run_select` safety pipeline: parse with sqlglot → reject unless single SELECT (and no
write/lock/INTO node anywhere, e.g. data-modifying CTEs) → reject non-allowlisted
tables/functions (functions are allowlisted by sqlglot node class, plus a short name list
for functions sqlglot doesn't model, so `pg_sleep` and friends fail closed) → inject/clamp
`LIMIT 200` → run as `docagent_reader` with `SET LOCAL app.session_id`,
`statement_timeout = 3s`, read-only transaction. Each layer is tested on its own too.
`save_record` uses parameterised inserts only.

### 6.4 `reference` server
| Tool | Input | Output |
|---|---|---|
| `convert_currency` | `amount, from_currency, to_currency, date` | `{amount, from_currency, to_currency, rate, rate_date, source}` |
| `lookup_vendor` | `name?, tax_id?` | `{found, vendor?: {vendor_id, legal_name, tax_id, country, default_currency, payment_terms}, matched_on, name_score, tax_id_matches}` |
| `lookup_tax_rate` | `country, category="standard"` | `{rate, effective_date}` |

Uses **static bundled datasets** (a fixed FX rate table, a fictional vendor master of ~20
companies, a small tax-rate table), so the
demo has no external API dependency. Document clearly that rates are illustrative.

---

## 7. Agent (LangGraph)

Two graphs in `agent/`, both loading tools from the MCP servers via `langchain-mcp-adapters`
(`MultiServerMCPClient`), Postgres checkpointer, `thread_id = run_id`.

### 7.1 Ingest graph

```
START → classify ──unknown──→ reject_unknown → END
            │
            ▼
         extract (default tier)
            │
            ▼
         validate ──errors on specific fields & attempts < 2──→ reextract (strong tier) → validate
            │
            ├── needs review ──→ human_review (interrupt) ──→ validate_final
            │                                                     │
            ▼                                                     ▼
          save ←──────────────────────────────────────────────────┘
            │
            ├── doc_type == invoice & po_number present ──→ po_match → (mismatch? flag in summary)
            │                                                  │
            ▼                                                  ▼
         summarize ←───────────────────────────────────────────┘ → END
```

- `po_match`: `get_purchase_order` → if found, `match_invoice_to_po` → `save_match_result`;
  if not found, record `po_not_found`. A mismatch doesn't block saving — it's surfaced in the
  summary and the UI shows a side-by-side PO vs invoice diff.
- `validate` also calls `lookup_vendor` (reference server); unknown vendor or tax_id mismatch
  becomes a `review` issue. The agent chooses between tools here, which is the point.
- State: `run_id, session_id, document_id, doc_type, record, issues, attempts, escalated, review_payload, match_result`.
- `reextract` only re-asks for failing/low-confidence field paths (cheaper; shows routing).
- `human_review` uses LangGraph `interrupt()`; API resumes with `Command(resume=edited_record)`.
- If no issues → skip review and save directly.
- Every node emits trace events (start/end, tokens, cost) to `trace_events` and to the SSE stream.

### 7.2 Ask graph (Q&A over saved data)

```
START → plan (get_schema, decide if conversion/lookup needed)
      → write_sql → run_select ──error & retries < 2──→ write_sql
      → (optional) convert_currency / lookup tools
      → answer (one sentence + table) → END
```
- The UI always shows the executed SQL.
- Refuse politely for questions unrelated to the saved documents.

---

## 8. Gateway, cost and safety

### 8.1 `packages/gateway`
- `complete(messages, *, tier, response_schema=None, images=None, run_id, session_id)` — single entry point.
- Resolves tier → model from `config/models.yaml`:
  ```yaml
  tiers:
    default:
      model: gemini-3.6-flash
      provider: gemini
      api_key_env: GEMINI_API_KEY
      reasoning_effort: low
      prices:                      # dated periods; first whose `until` hasn't passed applies
        - { until: 2026-12-31, input_per_mtok: 0.75, output_per_mtok: 3.75 }
        - { input_per_mtok: 1.50, output_per_mtok: 7.50 }
    strong: { model: gemini-4-argon, ... }
  fallback_order: [default, strong]
  ```
  (re-check model IDs and prices against the provider's docs whenever they change.)
- Writes every call to `llm_ledger`; computes cost from token usage × price table.
- **Budget:** global daily cap (`DAILY_BUDGET_USD`) and per-session cap. When exceeded → raise
  `BudgetExceeded`; API returns a friendly message and the UI switches to "replay mode" (see 8.3).
- Retries with backoff on 429/5xx; timeout per call. A provider retry hint longer than the
  max backoff (e.g. a daily quota) skips retries and goes straight to the fallback tier.
- A call is refused unless `call_reserve_usd` (limits.yaml) of budget remains, so overshoot is
  bounded by one call. Tokens out include thinking tokens (billed at the output price).
- Provider errors are re-raised as `ProviderError` with keys redacted and the original
  exception detached; keys are `SecretStr` in config.

### 8.2 Security / abuse
- Upload limits from `limits.yaml`: types PDF/PNG/JPG, ≤ 5 MB, ≤ 5 pages; verify magic bytes.
- Rate limit per IP (hashed): e.g. 5 runs / 10 min, 20 questions / 10 min.
- Optional Cloudflare Turnstile on first run of a session.
- Prompt injection: document text is wrapped and labelled as untrusted in prompts; the extraction
  step has **no tools**; the agent's tool set is fixed per node (classify/extract nodes can't call
  SQL); SQL is SELECT-only on the reader role; no network-reaching tools exist.
- MCP servers bind to the internal network only; shared secret header between agent and servers.
- Uploaded files stored in a private bucket/volume, deleted with the session.

### 8.3 Replay mode
Pre-recorded runs of the 6 sample docs (trace events + results saved as JSON fixtures). If the
budget is exhausted or the LLM provider is down, the UI replays these with real timing so the
demo always works. Label it clearly: "Replaying a recorded run".

---

## 9. API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/session` | create anonymous session (cookie) |
| GET | `/api/samples` | list sample docs |
| POST | `/api/documents` | upload file → `document_id` |
| POST | `/api/runs` | start ingest run `{document_id}` → `run_id` |
| GET | `/api/runs/{id}/events` | **SSE** stream of trace events + status changes |
| GET | `/api/runs/{id}` | run state, record, issues |
| POST | `/api/runs/{id}/resume` | submit reviewed record → resumes graph |
| POST | `/api/ask` | question → SSE stream (trace + answer + sql + rows) |
| GET | `/api/evals/latest` | latest eval report JSON |
| GET | `/api/health` | liveness + MCP server reachability + budget remaining |

SSE event shape:
```json
{"run_id": "...", "seq": 12, "node": "extract", "kind": "tool_end",
 "tool": "extract_fields", "model": "...", "tokens_in": 1834, "tokens_out": 412,
 "latency_ms": 2210, "cost_usd": 0.0009, "payload": {...}}
```

---

## 10. Build plan (phases)

Each phase ends with "Done when" checks. Tick boxes as you go.

### Phase 0 — Skeleton
- [x] uv workspace, ruff, pyright, pytest configured; `packages/`, `servers/`, `agent/`, `api/` stubs
- [x] `docker-compose.yml` with Postgres + placeholders for each service
- [x] `.env.example`, `config/models.yaml`, `config/limits.yaml`
- [x] GitHub Actions: lint + types + tests
**Done when:** `docker compose up` starts Postgres; `uv run pytest` passes; CI is green.

### Phase 1 — Schemas, DB, synthetic data
- [x] Pydantic schemas (§5.1) with JSON-schema export
- [x] SQL migrations (§5.2) incl. RLS policies, reader role, cleanup function
- [x] `evals/generator`: produce N POs + N invoices (most referencing a generated PO; ~30% with
      injected mismatches: price drift, over-billed qty, wrong vendor, missing PO) as PDFs with varied layouts (≥ 3
      templates each), some scanned-look (rotation/noise), plus ground-truth JSON; seeded & reproducible
- [x] Generate 60 docs (30/30): 6 become the public samples, rest the eval set
**Done when:** migrations apply cleanly; generator is deterministic for a fixed seed; RLS test
proves session A cannot read session B's rows.

### Phase 2 — Gateway
- [x] `complete()` with tier routing, structured output, image input, retries, timeouts
- [x] Cost ledger + daily and per-session budget enforcement
- [x] Unit tests with mocked provider responses
**Done when:** a script extracts one sample invoice end-to-end through the gateway and the ledger
row shows correct cost; budget test raises `BudgetExceeded`.

### Phase 3 — MCP servers
- [x] `extraction` (classify, extract, reextract)
- [x] `validation` (all rules in §6.2 + `list_rules`)
- [x] `sqlstore` (get_schema, run_select with full safety pipeline, save_record)
- [x] `reference` (static datasets)
- [x] Per-server README; each server runnable alone and testable with MCP Inspector
**Done when:** each server's tests pass; sqlglot tests reject INSERT/UPDATE/DELETE/DDL, multi-
statement, non-allowlisted tables; the MCP Inspector lists all tools for each server.

### Phase 4 — Agent graphs
- [ ] Ingest graph (§7.1) with escalation, interrupt/resume, Postgres checkpointer
- [ ] Ask graph (§7.2) with SQL retry
- [ ] PO ↔ invoice matching node
- [ ] Trace event emission from every node
**Done when:** a CLI command runs a sample doc to completion; a doc with an injected arithmetic
error pauses for review and resumes correctly; ingesting a PO then its invoice yields `matched`,
and the mismatch sample yields `mismatch` with the right issue; 5 canned questions return
correct answers.

### Phase 5 — API
- [ ] Endpoints in §9, SSE streaming, sessions via cookie
- [ ] Upload validation, rate limits, budget errors → friendly responses
- [ ] Replay mode fixtures recorded from real runs
**Done when:** API integration tests cover upload → run → review → resume → ask; replay works
with LLM calls disabled.

### Phase 6 — Evals
- [ ] `evals/runner.py`: runs the ingest graph (auto-approve review) over the eval set, POs
      before their invoices
- [ ] Metrics: per-field exact/normalised match, per-doc-type F1, **match-status accuracy and
      mismatch recall** (did it catch every injected mismatch?), escalation rate, review rate,
      avg/max cost per doc, p50/p95 latency, broken down by model tier
- [ ] Writes `evals/reports/latest.json` + `latest.md`
- [ ] CI: smoke eval on 6 docs on every PR (fails if accuracy drops > 5 pts); full eval manual/nightly
**Done when:** the report reproduces within noise on two consecutive runs; `/api/evals/latest` serves it.

### Phase 7 — Frontend
- [ ] Landing with architecture diagram + sample picker + upload
- [ ] Run view: document viewer (left), live trace timeline (right) with expandable tool calls,
      running cost/tokens counter
- [ ] Review form with flagged fields highlighted, reason, edit/approve
- [ ] Ask chat with SQL + result table
- [ ] Evals tab (charts) and Architecture tab
- [ ] Mobile-friendly; dark/light; replay-mode banner
**Done when:** full flow works against local backend; Lighthouse ≥ 90 performance/accessibility.

### Phase 8 — Deploy & polish
- [ ] Dockerfiles; deploy backend (API + agent + 4 MCP servers on private network) and Postgres
- [ ] Web on Vercel at `demo.meetmoeed.com`; CORS + cookies configured
- [ ] Session cleanup cron; uptime check on `/api/health`
- [ ] Public `README.md`: problem, demo GIF, architecture diagram, eval table, design decisions,
      "what I'd do in production" section (on-prem VLM, SSO, audit log, PII redaction)
- [ ] Link from meetmoeed.com Projects + replace old chatbot page
**Done when:** a fresh visitor can complete upload → review → ask in under 2 minutes, and the
demo survives budget exhaustion via replay mode.

---

## 11. Environment variables (`.env.example`)

```
POSTGRES_HOST_PORT=5433
DATABASE_URL=postgresql://docagent:docagent@127.0.0.1:5433/docagent
DATABASE_APP_URL=postgresql://docagent_app:...@127.0.0.1:5433/docagent
DATABASE_READER_URL=postgresql://docagent_reader:...@127.0.0.1:5433/docagent
GEMINI_API_KEY=
STRONG_MODEL_API_KEY=            # if the strong tier is a different provider
MCP_SHARED_SECRET=
MCP_EXTRACTION_URL=http://extraction:8101/mcp
MCP_VALIDATION_URL=http://validation:8102/mcp
MCP_SQLSTORE_URL=http://sqlstore:8103/mcp
MCP_REFERENCE_URL=http://reference:8104/mcp
DAILY_BUDGET_USD=3.00
SESSION_BUDGET_USD=0.25
UPLOAD_DIR=/data/uploads          # compose; data/uploads for local runs
MCP_BIND_HOST=127.0.0.1          # 0.0.0.0 only on the private compose network
TURNSTILE_SECRET=                # optional
ALLOWED_ORIGINS=https://demo.meetmoeed.com,http://localhost:3000
```

---

## 12. Portfolio framing (for README and the website card)

- **One-liner:** "An agentic document-to-data pipeline on MCP — extraction, validation,
  human review and SQL Q&A, with live traces, cost control and measured accuracy."
- Show on the card: eval accuracy, avg cost/doc, p95 latency, number of MCP tools.
- Design decisions worth writing up: why deterministic validation instead of LLM-judging;
  selective re-extraction instead of full retry; RLS for isolation; replay mode; why MCP
  servers are separate processes; what changes for on-prem (VLM on GPU, no external APIs).

---

## 13. Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-11-14 | Replaced ProductSpecSheet with PurchaseOrder + PO↔invoice matching | Spec sheets were too close to NDA-covered work; PO matching adds a cross-document agent step |
| 2026-11-14 | Compose publishes Postgres on host port 5433 (`POSTGRES_HOST_PORT`), bound to 127.0.0.1 | 5432 is often taken by a locally installed Postgres; container-internal port stays 5432 |
| 2026-11-14 | Python packages use `docagent_*` module names under `src/` (e.g. `docagent_schemas`, `docagent_mcp_extraction`) | Avoid generic top-level names like `schemas`/`api` clashing across the workspace |
| 2026-11-14 | Not-yet-built compose services live in a `todo` profile | Plain `docker compose up` stays clean until each service exists; remove the profile as each phase lands |
| 2026-11-14 | `ruff format` excludes `*.md` | ruff ≥ 0.17 formats Markdown code blocks and would rewrite SPEC.md examples |
| 2026-11-14 | `config/models.yaml` holds placeholder model IDs until Phase 2 | IDs/prices must be taken from official provider docs when the gateway is built |
| 2026-11-14 | Added `psycopg` 3 and `Pillow` to §3 (approved) | DB driver for migrations/RLS tests and later the checkpointer; Pillow for scanned-look docs |
| 2026-11-14 | Invoice lines have an optional `sku` | §6.2 matching is "by SKU, else fuzzy description", which needs SKUs on some invoices |
| 2026-11-14 | Two model families: `records.py` (plain, saved/ground truth) and `extracted.py` (`Field`-wrapped, floats) with `to_record()` | Keeps confidence/provenance out of the DB and ground truth; floats are what LLMs emit |
| 2026-11-14 | Added `docagent_app` write role under RLS; `session_id` on `trace_events`, `review_actions`, `llm_ledger` | Writes are isolated too, not just reads; enables per-session budget queries |
| 2026-11-14 | Migration runner + session helper live in a new workspace member `packages/db` (`docagent_db`) | Shared by api, sqlstore and tests; role passwords set from DATABASE_*_URL, never in SQL |
| 2026-11-14 | Local DSNs use `127.0.0.1`, not `localhost` | On Windows `localhost` tries `::1` first and stalls until timeout |
| 2026-11-14 | Generator also injects single-document arithmetic defects (2 invoices, 1 PO) | Phase 4 needs a document that "pauses for review"; PO-mismatch docs alone don't trigger validate_record |
| 2026-11-14 | Mismatching invoices always target a PO no other invoice touches | Expected match outcomes don't depend on how earlier mismatching invoices count against the PO balance |
| 2026-11-14 | Defined `matched` vs `partial` (§5.2): partial = all checks pass, quantity still open | The spec listed the statuses without defining them; ground truth needs a precise rule |
| 2026-11-14 | Generated PDFs are committed (3.2 MB), not LFS | Small enough for git; samples must ship with the app; CI regenerates and checks ground truth matches |
| 2026-11-14 | `evals` is importable from the repo root (pytest `pythonpath = ["."]`), not a workspace package | Keeps the §4 layout (`evals/generator/`, `evals/runner.py`) |
| 2026-11-14 | Sample PO is numbered PO-1042 | The example question in §2 ("uninvoiced on PO-1042") works on the demo data |
| 2026-11-14 | Migrations run with `uv run python -m docagent_db` | |
| 2026-11-14 | Model IDs + prices checked on aistudio.google.com/docs/models and /docs/pricing (2026-11-14): default `gemini-3.6-flash` ($0.75/$3.75 per 1M in/out until 2026-12-31, then $1.50/$7.50); strong `gemini-4-argon` ($2/$10 until 2026-11-30, then $4/$20) | Gemini for both tiers so one key suffices. No stable "Pro"-named model exists (2.5 Pro deprecated, 3.1 Pro preview-only); Gemini 4 Argon is the efficient frontier model, same price as Carbon with fewer tokens per turn |
| 2026-11-14 | Default tier uses `gemini-3.6-flash`, not `gemini-3.8-flash` | 3.8 Flash (same price) returned 503 "high demand" on every call for ~15 min while 3.6 answered; one-line switch back in models.yaml |
| 2026-11-14 | Prices are dated periods in models.yaml | Both models have introductory prices ending within weeks; the ledger must cost each call at the price on its UTC date |
| 2026-11-14 | `complete()` takes `session_id` | Needed to enforce the per-session cap |
| 2026-11-14 | Gateway pins `LITELLM_MODE=PRODUCTION` before importing LiteLLM | LiteLLM otherwise runs `load_dotenv()` on import and silently pulls `.env` (API keys) into the process |
| 2026-11-14 | Real-API tests are `@pytest.mark.integration`, skipped without `GEMINI_API_KEY`, deselected in CI | Unit tests never hit the network; CI never spends money |
| 2026-11-14 | Added PyYAML to §3 (approved) | Parse config/*.yaml; already a LiteLLM dependency |
| 2026-11-14 | Page rendering + extraction prompt live in `servers/extraction` already | Phase 2's end-to-end check needs them; Phase 3 wraps them as MCP tools |
| 2026-11-14 | MCP SDK 2.3: `MCPServer` (FastMCP was renamed in v2), stateless Streamable HTTP with JSON responses, tool errors as `ToolError` with a JSON `{code, message}` body | Checked against the installed SDK's source and the v2 docs; stateless suits internal request/response tools and horizontal scaling |
| 2026-11-14 | Shared secret travels in an `x-mcp-secret` header, checked by a pure-ASGI middleware with a constant-time compare; DNS-rebinding allowlist = URL host + localhost | §8.2 asks for a shared secret header; the middleware wraps the SDK app so lifespan still works |
| 2026-11-14 | Servers take port/path from their `MCP_*_URL` and bind `MCP_BIND_HOST` (default 127.0.0.1) | One source of truth for where a server lives; never bind publicly by default |
| 2026-11-14 | Trace events are logged by the agent around each call; LLM tools return a `usage` block | §6 asked to pick one approach; keeps servers free of trace-store dependencies |
| 2026-11-14 | Extraction tools take optional `session_id`/`run_id` | Per-session budget and ledger attribution happen in the gateway, inside the server |
| 2026-11-14 | `match_invoice_to_po` takes `prior_invoices` and returns `line_matches`; validation severities are error/review/warning | Remaining qty/value needs earlier invoices; line pairing drives the UI diff; review = human, warning = informational |
| 2026-11-14 | Line pairing: SKU, then rapidfuzz token-set description match at 85, then a fallback pass at 60 (`po_matching.description_match_fallback_score`) | Vendor wording differs a lot ("Colour toner set C/M/Y" vs "Toner cartridge set, cyan/magenta/yellow"); verified 107/107 pairings correct on the dataset |
| 2026-11-14 | New workspace package `packages/mcp_common` | Auth middleware, serving, errors and limits loading shared by all four servers |
| 2026-11-14 | `python -m servers.<name>` works from the repo root via a small `__main__.py` shim per server folder | Matches the documented run command; canonical module is `docagent_mcp_<name>` |
| 2026-11-14 | `get_purchase_order` returns `{found, po_id, purchase_order, prior_invoices[]}` and takes `exclude_invoice_id` | The matcher needs whole earlier invoices (it pairs their lines itself); the invoice being matched is already saved by then and must not count against its own PO |
| 2026-11-14 | `run_select` returns the executed SQL; numerics/dates come back as exact strings | The UI shows the SQL that actually ran (with LIMIT); money must not pass through floats |
| 2026-11-14 | sqlstore tests act as `docagent_reader`/`docagent_app` via `SET ROLE` on an admin connection; `set_role_passwords` takes a `roles` map | A Phase 1 test reset the real `docagent_reader` password (roles are cluster-wide), breaking the local dev login; tests now use a throwaway role |
| 2026-11-14 | DB connection failures surface as `database_unavailable` with no details | Postgres's connect error names host, port and role; the model shouldn't see that |
| 2026-11-14 | Reference data: monthly USD-based FX table for 2026 (9 currencies), the 20 fictional vendors from the generator (`V-001`…), standard/reduced tax rates for 9 countries; all labelled illustrative | §6.4: static bundled data, no external dependency; tests pin vendors and tax rates to what the synthetic documents use |
| 2026-11-14 | `lookup_vendor` returns `found` + `matched_on` + `tax_id_matches` instead of record-or-null | §7.1 turns "unknown vendor or tax_id mismatch" into review issues; the agent needs to tell those apart |
