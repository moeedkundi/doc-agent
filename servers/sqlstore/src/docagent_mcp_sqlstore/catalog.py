"""Human descriptions of the reader-visible tables, returned by get_schema to help the model
write correct SQL. Column names/types come from the live database; these add meaning."""

TABLES: dict[str, str] = {
    "documents": "Uploaded files. One row per document (invoice or purchase order).",
    "invoices": "One row per saved invoice (header fields).",
    "invoice_lines": "Line items of saved invoices; join invoice_lines.invoice_id = invoices.id.",
    "purchase_orders": "One row per saved purchase order (header fields).",
    "po_lines": "Line items of saved POs; join on po_lines.po_id = purchase_orders.id.",
    "invoice_po_matches": "Result of matching an invoice to its PO (status + issues).",
}

COLUMNS: dict[str, dict[str, str]] = {
    "documents": {"doc_type": "invoice | purchase_order | unknown", "pages": "page count"},
    "invoices": {
        "currency": "ISO 4217 code; amounts are in this currency",
        "po_number": "PO the invoice references (may be null); join to purchase_orders.po_number",
        "seller_name": "vendor that issued the invoice",
        "total": "invoice total including tax",
        "subtotal": "total before tax",
    },
    "purchase_orders": {
        "currency": "ISO 4217 code; amounts are in this currency",
        "vendor_name": "supplier the PO was issued to",
        "total": "PO total including tax",
    },
    "invoice_lines": {"line_no": "1-based position on the invoice", "sku": "may be null"},
    "po_lines": {"line_no": "1-based position on the PO"},
    "invoice_po_matches": {
        "status": "matched | partial | mismatch | po_not_found. matched and partial both "
        "mean the invoice AGREES with its PO (partial = the PO still has open quantity); "
        "mismatch = it disagrees (see issues_json); po_not_found = its PO is not on file",
        "issues_json": "list of {rule, message, invoice_line, po_line}",
        "po_id": "null when status = po_not_found",
    },
}

HIDDEN_COLUMNS: dict[str, set[str]] = {
    "documents": {"storage_path", "session_id"},
    "invoices": {"session_id"},
    "invoice_lines": {"session_id"},
    "purchase_orders": {"session_id"},
    "po_lines": {"session_id"},
    "invoice_po_matches": {"session_id"},
}
"""Not advertised to the model (RLS already scopes rows; paths are internal)."""
