# Synthetic dataset

60 generated business documents (30 purchase orders + 30 invoices) with ground truth.
**Everything here is synthetic. Every company, address, tax ID and person is fictional**, and
the tax rates and prices are illustrative. Regenerate with:

```
uv run python -m evals.generator            # seed 20261114 -> this folder
```

The generator is deterministic: the same seed gives byte-identical files, and CI checks that
the committed ground truth matches.

| Folder | Contents |
|---|---|
| `samples/` | 3 POs + the 3 invoices against them (matched, partial on PO-1042, price-drift mismatch); the public demo samples |
| `eval/` | 27 POs + 27 invoices: the evaluation set |
| `manifest.json` | every document in ingest order (POs first), with template, scanned flag, pages, injected defects and expected match status |

Each `*.pdf` has a `*.json` sidecar (`docagent_schemas.GroundTruth`). Its `record` is exactly
what the document prints, and is the extraction target. `injected` lists deliberate problems,
and `expected_match` is the outcome `match_invoice_to_po` should produce when the documents
are ingested in manifest order.

Injected problems:
- **PO mismatches:** `price_drift` (unit price +3–8% against 2% tolerance), `over_billed_qty`,
  `wrong_vendor`, `missing_po` (cites a PO number that doesn't exist).
- **Single-document arithmetic defects** (for validation and review): `arithmetic_total`
  (the printed total ≠ subtotal + tax) and `arithmetic_line` (line total ≠ qty × price).

Layouts: `classic`, `modern` and `compact`, 10 of each per document type. 12 documents are
"scanned" (image-only: skewed, grainy, no text layer). Three run to two pages.
