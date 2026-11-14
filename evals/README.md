# Evals

Measures how well the ingest pipeline reads invoices and purchase orders, by running it on
the synthetic dataset (`evals/dataset/`, see its README) and comparing every extracted field
with the ground truth.

## Running

The four MCP servers must be running and the database migrated (see the server READMEs).

```bash
uv run --env-file .env python -m evals.runner --suite smoke   # 6 public samples
uv run --env-file .env python -m evals.runner --suite full    # eval set: 65 documents
```

- **Reports:** a full run writes `evals/reports/latest.json` and `latest.md`, which the API
  serves at `GET /api/evals/latest` (`?format=md` for Markdown). A smoke run writes
  `smoke.json` and `smoke.md`.
- **Documents:** they run in manifest order, so every PO is ingested before its invoices,
  in a fresh session per run.
- **Reviews:** when the pipeline asks for human review, the runner approves the record as
  extracted and counts the review.
- **CI gate:** `--baseline FILE --max-drop 5` exits non-zero when field accuracy falls more
  than 5 points below the baseline.

### Budget

Eval sessions are marked `purpose='eval'`. Their LLM spend is charged to a separate pool
capped by `EVAL_BUDGET_USD` (per UTC day), so evals never use up the public demo's
`DAILY_BUDGET_USD`, and the demo's cap never counts them. The per-session cap
(`SESSION_BUDGET_USD`) does not apply to eval sessions. A full run costs about $0.4 with the
current models.

## Field match policy

Every field in a ground-truth record is compared with the extracted value:

| Fields | Rule |
|---|---|
| IDs: `invoice_number`, `po_number`, `sku`, `tax_id` | exact (surrounding whitespace ignored) |
| Dates: `invoice_date`, `due_date`, `po_date`, `requested_delivery_date` | exact (ISO dates) |
| `currency` | exact |
| Amounts and quantities: `subtotal`, `tax_amount`, `total`, `unit_price`, `line_total`, `quantity` | Decimal-equal (`12.50` = `12.5`) |
| Names, addresses, descriptions, `ship_to`, `payment_terms` | equal after normalising (case, punctuation, whitespace), or rapidfuzz `ratio` ≥ 90 on the normalised text |

- **Nulls:** a null on both sides is correct. A value on one side only is wrong.
- **Line items:** compared by position. A missing line makes all its fields wrong; an extra
  line adds false positives.
- **Fuzzy matching is forgiving.** "Finewick Data Accessories" scores as matching
  "Pinewick Data Accessories". That's intended for names and addresses (punctuation and
  abbreviations vary), but one-letter misreads in names won't show up as errors.

## Metrics (`metrics` in the report)

| Metric | Definition |
|---|---|
| `field_accuracy` | correct fields / all ground-truth fields (micro-average) |
| `per_field_accuracy` | the same, per field (`invoice.line_items[].unit_price`, …) |
| `by_doc_type` | per document type: field accuracy, plus precision/recall/F1 over non-null fields. TP = correct non-null; FP = wrong or extra predicted value; FN = missing or wrong expected value |
| `classification_accuracy` | predicted document type = true type |
| `clean_vs_scanned` | field accuracy on clean vs scanned documents, and on the same content: each scanned variant (`<id>_scan`) against its clean twin |
| `match_status_accuracy` | invoices whose PO-match status equals the expected one (`matched` / `partial` / `mismatch` / `po_not_found`, or no match for invoices without a PO number) |
| `mismatch_recall` | invoices with an injected PO mismatch that were flagged `mismatch` (also per kind: price drift, over-billed quantity, wrong vendor) |
| `mismatch_rules_exact` | of those caught, the share whose issue rules are exactly the expected ones |
| `escalation_rate` | runs that re-read fields with the strong tier |
| `review_rate` | runs that paused for human review |
| `cost_per_doc_usd` | run cost from the ledger (avg, max, total) |
| `latency_s` | wall time per document, p50 / p95 |
| `by_model` | calls, tokens and cost per model in the eval session |

## Scanned variants

Eleven eval documents also exist as scanned variants (`<id>_scan`, `scanned: true`,
`variant_of: <id>`). Each variant has the same record and expected outcome as its clean
twin, so the gap between them isolates the effect of scanning. A variant ingested after its
twin is a re-submission: the PO match counts it once, as it would a real duplicate upload.

## Caveats

- **Limited variety:** three templates per document type. One generator draws all the
  documents, so these numbers flatter real-world performance (see the TODO in SPEC.md).
- **Confidence is uncalibrated:** it's the model's self-report and mostly 1.0, so
  review-rate numbers mostly reflect deterministic validation, not confidence.
