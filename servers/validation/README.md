# docagent-validation (MCP server)

Deterministic checks for extracted invoices and purchase orders, plus the PO ↔ invoice
two-way match. It's pure Python: **no LLM**, no database, no network. The same input
always gives the same answer, so its decisions can be tested and audited.

## Run

```bash
uv run --env-file .env python -m servers.validation        # or: python -m docagent_mcp_validation
```

| Env var | Purpose |
|---|---|
| `MCP_VALIDATION_URL` | e.g. `http://validation:8102/mcp`. The server takes its **port and path** from this |
| `MCP_BIND_HOST` | bind address, default `127.0.0.1` |
| `MCP_SHARED_SECRET` | required, ≥ 16 chars. Every request must send it as the `x-mcp-secret` header, or gets `401` |

Thresholds come from `config/limits.yaml`: `validation.review_confidence_threshold`,
`validation.amount_tolerance`, `validation.po_number_pattern`,
`po_matching.unit_price_tolerance_pct` and `po_matching.description_match_*_score`.

## Tools

| Tool | Input | Output |
|---|---|---|
| `validate_record` | `doc_type`, `record`: extraction output (`Invoice`/`PurchaseOrder`, with confidences) **or** a plain record (`InvoiceRecord`/`PurchaseOrderRecord`, e.g. after human review) | `ValidationResult {valid, issues[]: {field_path, severity, rule, message}}` |
| `list_rules` | `doc_type` | `[{rule, doc_types, severity, description}]` |
| `match_invoice_to_po` | `invoice: InvoiceRecord`, `purchase_order: PurchaseOrderRecord`, `prior_invoices?: InvoiceRecord[]` (already saved against this PO, oldest first), `tolerances?` | `MatchResult {status, issues[]: {rule, message, invoice_line, po_line}, line_matches[]: {invoice_line, po_line, method, score}}` |

`field_path` uses the same syntax `reextract_fields` accepts (`line_items[1].line_total`),
so the agent can hand failing paths straight to re-extraction. `valid` is false when any
issue is `error` or `review`. `warning` never blocks.

### Rules

| Rule | Applies to | Severity | Checks |
|---|---|---|---|
| `required_field` | both | error | number, date, currency, party name, total present |
| `line_items_present` | both | error | at least one line |
| `currency_iso4217` | both | error | valid ISO 4217 code |
| `line_amount` | both | error | qty × unit price = line total (± tolerance) |
| `lines_sum_to_subtotal` | both | error | Σ line totals = subtotal |
| `subtotal_plus_tax_equals_total` | both | error | subtotal + tax = total |
| `due_date_not_before_invoice_date` | invoice | error | due ≥ invoice date |
| `invoice_po_reference_format` | invoice | warning | PO reference looks like a PO number |
| `delivery_date_not_before_po_date` | PO | error | requested delivery ≥ PO date |
| `po_number_format` | PO | warning | looks like a document number (`po_number_pattern`) |
| `payment_terms_parseable` | PO | review | Net N, N days, 2/10 Net 30, due on receipt… |
| `no_duplicate_skus` | PO | error | each SKU on one line only |
| `low_confidence` | both | review | every extracted field ≥ review threshold (extraction output only) |

### Matching

1. **Vendor:** tax IDs must agree when both sides have one. Otherwise the names must agree
   (fuzzy, ignoring legal forms like Ltd/Inc/GmbH). Failure → `vendor_mismatch`.
2. **Currency:** must agree. Failure → `currency_mismatch`.
3. **Line pairing**, one to one: same SKU first, then fuzzy description (rapidfuzz). There's a
   confident pass at score 85, then a fallback pass at 60 for leftovers. Unpaired invoice
   lines → `line_not_on_po`.
4. **Quantity:** at most the PO quantity minus what earlier invoices billed. Over →
   `qty_exceeds_remaining`.
5. **Unit price:** within ±2%. Outside → `unit_price_out_of_tolerance`.
6. **Total:** invoice total ≤ PO total minus earlier invoice totals (+ amount tolerance).
   Over → `total_exceeds_remaining`.

Status is `mismatch` if there's any issue. Otherwise it's `matched` when the PO has no open
quantity left, or `partial` when it does. `po_not_found` is decided by the caller's PO lookup
(sqlstore), not by this tool.

## Example

```bash
npx @modelcontextprotocol/inspector@latest --cli http://127.0.0.1:8102/mcp --transport http \
  --header "x-mcp-secret: $MCP_SHARED_SECRET" \
  --method tools/call --tool-name match_invoice_to_po \
  --tool-arg "invoice=$(jq -c .record evals/dataset/samples/inv_003.json)" \
  --tool-arg "purchase_order=$(jq -c .record evals/dataset/samples/po_003.json)"
```

```json
{
  "status": "mismatch",
  "issues": [
    {"rule": "unit_price_out_of_tolerance", "invoice_line": 1, "po_line": 1,
     "message": "invoice line 2 unit price 98.08 vs PO 93.41 (+5.0%, tolerance +/-2.0%)"},
    {"rule": "total_exceeds_remaining", "invoice_line": null, "po_line": null,
     "message": "invoice total 5890.66 exceeds the PO's remaining value 5666.50 (...)"}
  ],
  "line_matches": [
    {"invoice_line": 0, "po_line": 0, "method": "description", "score": 80.0},
    {"invoice_line": 1, "po_line": 1, "method": "description", "score": 93.8}
  ]
}
```

## Tests

`uv run pytest servers/validation`. These cover every rule against the public sample pair,
every match rule, and the whole synthetic dataset:
- All 26 invoices that have a PO produce their expected match outcome.
- All 107 invoice lines pair with the right PO line.
- Only the injected arithmetic defects fail validation.
