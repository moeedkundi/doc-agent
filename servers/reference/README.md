# docagent-reference (MCP server)

Reference lookups the agent needs while validating documents and answering questions:
currency conversion, the vendor master, and VAT/sales-tax rates. Everything comes from
**static JSON bundled in the package** (`src/docagent_mcp_reference/data/`). There are no
external APIs, no database and no LLM, so the demo has no live dependency here.

> **The data is illustrative.** FX rates aren't market data, tax rates aren't tax advice, and
> every vendor is a fictional company: the same 20 that appear on the synthetic documents.

## Run

```bash
uv run --env-file .env python -m servers.reference        # or: python -m docagent_mcp_reference
```

| Env var | Purpose |
|---|---|
| `MCP_REFERENCE_URL` | e.g. `http://reference:8104/mcp`. The server takes its **port and path** from this |
| `MCP_BIND_HOST` | bind address, default `127.0.0.1` |
| `MCP_SHARED_SECRET` | required, ≥ 16 chars. Every request must send it as the `x-mcp-secret` header, or gets `401` |

## Tools

| Tool | Input | Output |
|---|---|---|
| `convert_currency` | `amount`, `from_currency`, `to_currency` (ISO 4217), `date` | `{amount, from_currency, to_currency, rate, rate_date, source}`. Uses the rate in force on `date`: monthly table, USD-based cross rates, rounded to cents |
| `lookup_vendor` | `name?`, `tax_id?` (at least one) | `{found, vendor?: {vendor_id, legal_name, tax_id, country, default_currency, payment_terms}, matched_on: tax_id\|name, name_score, tax_id_matches}` |
| `lookup_tax_rate` | `country` (ISO 3166 alpha-2), `category` (`standard`\|`reduced`) | `{country, category, rate, effective_date, source}` |

How `lookup_vendor` matches:
- **By tax ID** first: an exact match after removing spaces and punctuation.
- **By name** otherwise: fuzzy (rapidfuzz, score ≥ 85), ignoring legal forms like Ltd, Inc
  and GmbH.
- **With both given**, `tax_id_matches: false` means the name found a vendor but the tax ID
  disagrees. The ingest graph turns that into a review issue.

Errors (`isError`, `{code, message}`): `rate_not_available` (date before the table),
`unknown_currency`, `missing_query`, `unknown_tax_rate`.

## Data

| File | Contents |
|---|---|
| `fx_rates.json` | units per 1 USD on the 1st of each month in 2026, for USD, EUR, GBP, CHF, CAD, AUD, JPY, SEK and PKR |
| `vendors.json` | 20 fictional vendors (`V-001`…`V-020`). Tests check they match the generator's catalog |
| `tax_rates.json` | standard (and some reduced) rates for US, GB, DE, NL, FR, IE, BE, ES and IT. Tests check they match what the synthetic documents charge |

## Example

```bash
npx @modelcontextprotocol/inspector@latest --cli http://127.0.0.1:8104/mcp --transport http \
  --header "x-mcp-secret: $MCP_SHARED_SECRET" --method tools/call --tool-name lookup_vendor \
  --tool-arg "name=BRIGHTLEDGER STATIONERS LIMITED" --tool-arg "tax_id=GB 284 719 306"
```

```json
{"found": true, "matched_on": "tax_id", "name_score": 100.0, "tax_id_matches": true,
 "vendor": {"vendor_id": "V-009", "legal_name": "Brightledger Stationers Ltd",
            "tax_id": "GB284719306", "country": "GB", "default_currency": "GBP",
            "payment_terms": "Net 30"}}
```

## Tests

`uv run pytest servers/reference`. These cover lookups, conversions, error codes, the MCP
surface and auth. They also check that the package imports no network or database client.
