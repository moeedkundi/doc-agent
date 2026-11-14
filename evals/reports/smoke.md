# DocAgent eval report: smoke (6 documents)

Run 2026-11-14T11:18:33Z · models: default `gemini-3.5-flash-lite`, strong `gemini-4-hydrogen` · session `9950177f-5e5f-4c12-9097-57255be3fc51`

| Metric | Value |
|---|---|
| Field accuracy | 98.1% |
| Classification accuracy | 100.0% |
| F1, invoice | 0.971 (P 0.971, R 0.971) |
| F1, purchase_order | 0.988 (P 0.988, R 0.988) |
| Accuracy, clean documents | 99.3% (5) |
| Accuracy, scanned documents | 88.9% (1) |
| Same content, clean vs scanned (0 pairs) | – vs – |
| Match-status accuracy | 66.7% |
| Mismatch recall | 100.0% |
| Escalation rate | 0.0% |
| Review rate | 16.7% |
| Cost per document | avg $0.0050, max $0.0063 (total $0.0302) |
| Latency per document | p50 8.8s, p95 9.6s |
| Failed runs | 0 |

## Mismatch recall by injected kind

| Kind | Recall |
|---|---|
| price_drift | 100.0% |
| over_billed_qty | – |
| wrong_vendor | – |

## By model

| Model | Calls | Tokens in/out | Cost |
|---|---|---|---|
| gemini-3.5-flash-lite | 12 | 20457/9621 | $0.0302 |

## Per-field accuracy

| Field | Accuracy |
|---|---|
| `invoice.buyer.address` | 100.0% |
| `invoice.buyer.name` | 100.0% |
| `invoice.currency` | 100.0% |
| `invoice.due_date` | 100.0% |
| `invoice.invoice_date` | 100.0% |
| `invoice.invoice_number` | 66.7% |
| `invoice.line_items[].description` | 100.0% |
| `invoice.line_items[].line_total` | 100.0% |
| `invoice.line_items[].quantity` | 100.0% |
| `invoice.line_items[].sku` | 100.0% |
| `invoice.line_items[].unit_price` | 100.0% |
| `invoice.po_number` | 100.0% |
| `invoice.seller.address` | 100.0% |
| `invoice.seller.name` | 100.0% |
| `invoice.seller.tax_id` | 66.7% |
| `invoice.subtotal` | 100.0% |
| `invoice.tax_amount` | 100.0% |
| `invoice.total` | 100.0% |
| `purchase_order.buyer.address` | 100.0% |
| `purchase_order.buyer.name` | 100.0% |
| `purchase_order.currency` | 100.0% |
| `purchase_order.line_items[].description` | 100.0% |
| `purchase_order.line_items[].line_total` | 100.0% |
| `purchase_order.line_items[].quantity` | 100.0% |
| `purchase_order.line_items[].sku` | 100.0% |
| `purchase_order.line_items[].unit_price` | 100.0% |
| `purchase_order.payment_terms` | 100.0% |
| `purchase_order.po_date` | 100.0% |
| `purchase_order.po_number` | 100.0% |
| `purchase_order.requested_delivery_date` | 100.0% |
| `purchase_order.ship_to` | 66.7% |
| `purchase_order.subtotal` | 100.0% |
| `purchase_order.tax_amount` | 100.0% |
| `purchase_order.total` | 100.0% |
| `purchase_order.vendor.address` | 100.0% |
| `purchase_order.vendor.name` | 100.0% |
| `purchase_order.vendor.tax_id` | 100.0% |

## Documents with errors

- **po_003**: `ship_to` 'Birchfield Campus, Mill Road, Reading RG1 8HX, United Kingdom' → 'Silverbirch Academy Trust, Birchfield Campus, Mill Road, Reading RG1 8HX, United Kingdom'
- **inv_002** (scanned): `invoice_number` 'INV004677' → 'INVQ04677'; `seller.tax_id` '39-2058816' → '39-2058616' match mismatch (expected partial)
