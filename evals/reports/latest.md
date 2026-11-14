# DocAgent eval report: full (65 documents)

Run 2026-11-14T11:34:33Z · models: default `gemini-3.5-flash-lite`, strong `gemini-4-hydrogen` · session `7d37bee3-aaed-4e5e-974e-7787b2493bca`

| Metric | Value |
|---|---|
| Field accuracy | 99.3% |
| Classification accuracy | 100.0% |
| F1, invoice | 1.000 (P 1.000, R 1.000) |
| F1, purchase_order | 0.988 (P 0.988, R 0.988) |
| Accuracy, clean documents | 99.2% (54) |
| Accuracy, scanned documents | 100.0% (11) |
| Same content, clean vs scanned (11 pairs) | 100.0% vs 100.0% |
| Match-status accuracy | 100.0% |
| Mismatch recall | 100.0% |
| Escalation rate | 4.6% |
| Review rate | 4.6% |
| Cost per document | avg $0.0069, max $0.0218 (total $0.4475) |
| Latency per document | p50 9.9s, p95 21.9s |
| Failed runs | 0 |

## Mismatch recall by injected kind

| Kind | Recall |
|---|---|
| price_drift | 100.0% |
| over_billed_qty | 100.0% |
| wrong_vendor | 100.0% |

## By model

| Model | Calls | Tokens in/out | Cost |
|---|---|---|---|
| gemini-3.5-flash-lite | 130 | 232891/141833 | $0.4245 |
| gemini-4-hydrogen | 3 | 7202/1633 | $0.0231 |

## Per-field accuracy

| Field | Accuracy |
|---|---|
| `invoice.buyer.address` | 100.0% |
| `invoice.buyer.name` | 100.0% |
| `invoice.currency` | 100.0% |
| `invoice.due_date` | 100.0% |
| `invoice.invoice_date` | 100.0% |
| `invoice.invoice_number` | 100.0% |
| `invoice.line_items[].description` | 100.0% |
| `invoice.line_items[].line_total` | 100.0% |
| `invoice.line_items[].quantity` | 100.0% |
| `invoice.line_items[].sku` | 100.0% |
| `invoice.line_items[].unit_price` | 100.0% |
| `invoice.po_number` | 100.0% |
| `invoice.seller.address` | 100.0% |
| `invoice.seller.name` | 100.0% |
| `invoice.seller.tax_id` | 100.0% |
| `invoice.subtotal` | 100.0% |
| `invoice.tax_amount` | 100.0% |
| `invoice.total` | 100.0% |
| `purchase_order.buyer.address` | 100.0% |
| `purchase_order.buyer.name` | 78.1% |
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
| `purchase_order.ship_to` | 71.9% |
| `purchase_order.subtotal` | 100.0% |
| `purchase_order.tax_amount` | 100.0% |
| `purchase_order.total` | 100.0% |
| `purchase_order.vendor.address` | 100.0% |
| `purchase_order.vendor.name` | 100.0% |
| `purchase_order.vendor.tax_id` | 100.0% |

## Documents with errors

- **po_004**: `ship_to` '9 Chandlers Wharf, Southampton SO14 3QB, United Kingdom' → 'Tidewater Architects LLP, 9 Chandlers Wharf, Southampton SO14 3QB, United Kingdom'
- **po_010**: `buyer.name` 'Meadowbrook Dental Group PC' → 'MICHAEL DORSEY'; `ship_to` '140 Sycamore Plaza, Asheville, NC 28801, USA' → 'Meadowbrook Dental Group PC, 140 Sycamore Plaza, Asheville, NC 28801, USA'
- **po_012**: `buyer.name` 'Ostwind Maschinenbau GmbH' → 'SILVESTER LINKE'; `ship_to` 'Industriestraße 72, 70565 Stuttgart, Germany' → 'Ostwind Maschinenbau GmbH, Industriestraße 72, 70565 Stuttgart, Germany'
- **po_016**: `ship_to` 'Birchfield Campus, Mill Road, Reading RG1 8HX, United Kingdom' → 'Silverbirch Academy Trust, Birchfield Campus, Mill Road, Reading RG1 8HX, United Kingdom'
- **po_020**: `buyer.name` 'Redfern Analytics Inc.' → 'KIMBERLY STAFFORD'; `ship_to` '88 Granary Square, Floor 6, Minneapolis, MN 55401, USA' → 'Redfern Analytics Inc., 88 Granary Square, Floor 6, Minneapolis, MN 55401, USA'
- **po_022**: `buyer.name` 'Ostwind Maschinenbau GmbH' → 'SIEGLINDE GRÖTTNER'; `ship_to` 'Industriestraße 72, 70565 Stuttgart, Germany' → 'Ostwind Maschinenbau GmbH, Industriestraße 72, 70565 Stuttgart, Germany'
- **po_024**: `buyer.name` 'Meadowbrook Dental Group PC' → 'MARY FERGUSON'; `ship_to` '140 Sycamore Plaza, Asheville, NC 28801, USA' → 'Meadowbrook Dental Group PC, 140 Sycamore Plaza, Asheville, NC 28801, USA'
- **po_025**: `buyer.name` 'Ostwind Maschinenbau GmbH' → 'YVETTE NERGER-HORNICH'; `ship_to` 'Industriestraße 72, 70565 Stuttgart, Germany' → 'Ostwind Maschinenbau GmbH, Industriestraße 72, 70565 Stuttgart, Germany'
- **po_027**: `buyer.name` 'Ostwind Maschinenbau GmbH' → 'MAREK GORLITZ'; `ship_to` 'Industriestraße 72, 70565 Stuttgart, Germany' → 'Ostwind Maschinenbau GmbH, Industriestraße 72, 70565 Stuttgart, Germany'
