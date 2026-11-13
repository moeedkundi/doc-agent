"""Where documents live: `UPLOAD_DIR/<document_id>.<pdf|png|jpg>` (the API writes them there).

Ids must be UUIDs, so a tool argument can never name a path outside the upload dir. File
types are checked by magic bytes, not by extension.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

Mime = Literal["application/pdf", "image/png", "image/jpeg"]

_MAGIC: tuple[tuple[bytes, Mime], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")


class DocumentNotFound(LookupError):
    pass


class UnsupportedDocument(ValueError):
    pass


@dataclass(frozen=True)
class StoredDocument:
    document_id: UUID
    data: bytes
    mime: Mime


def sniff(data: bytes) -> Mime:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    raise UnsupportedDocument("not a PDF, PNG or JPEG file")


@dataclass(frozen=True)
class DocumentStore:
    root: Path

    def load(self, document_id: UUID) -> StoredDocument:
        for ext in _EXTENSIONS:
            path = self.root / f"{document_id}{ext}"
            if path.is_file():
                data = path.read_bytes()
                return StoredDocument(document_id, data, sniff(data))
        raise DocumentNotFound(f"document {document_id} not found")
