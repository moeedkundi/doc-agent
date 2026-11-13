"""Small shared types."""

from typing import Literal

DocumentType = Literal["invoice", "purchase_order", "unknown"]
"""What `classify_document` can return."""

RecordDocType = Literal["invoice", "purchase_order"]
"""Document types that produce a saved record."""
