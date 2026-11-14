"""Request guards: upload validation and per-IP rate limiting."""

import hashlib
import io
import math
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pypdf import PdfReader
from pypdf.errors import PdfReadError

_MAGIC = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
)


class ApiError(Exception):
    """An error with an HTTP status and a stable machine-readable code."""

    def __init__(self, status: int, code: str, message: str, **extra: object) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


@dataclass(frozen=True)
class CheckedUpload:
    mime: str
    ext: str
    pages: int


def check_upload(data: bytes, filename: str, *, max_bytes: int, max_pages: int) -> CheckedUpload:
    """Type by magic bytes (the extension is ignored), size, then page count."""
    if len(data) == 0:
        raise ApiError(400, "empty_file", "The file is empty.")
    if len(data) > max_bytes:
        raise ApiError(
            413,
            "file_too_large",
            f"The file is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{max_bytes / 1_048_576:.0f} MB.",
        )
    kind = next(((mime, ext) for magic, mime, ext in _MAGIC if data.startswith(magic)), None)
    if kind is None:
        raise ApiError(
            415,
            "unsupported_file_type",
            f"{filename!r} is not a PDF, PNG or JPEG (checked by file content, not by its name).",
        )
    mime, ext = kind
    pages = 1
    if mime == "application/pdf":
        try:
            pages = len(PdfReader(io.BytesIO(data)).pages)
        except (PdfReadError, ValueError, KeyError) as exc:
            raise ApiError(422, "unreadable_pdf", f"The PDF could not be read ({exc}).") from None
        if pages > max_pages:
            raise ApiError(
                422, "too_many_pages", f"The PDF has {pages} pages; the limit is {max_pages}."
            )
    return CheckedUpload(mime, ext, pages)


# -- rate limiting ------------------------------------------------------------------------

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_rate(rule: str) -> tuple[int, float]:
    """'5/10minutes' -> (5, 600.0). Same syntax as limits.yaml / slowapi."""
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d*)\s*(second|minute|hour|day)s?\s*", rule)
    if not m:
        raise ValueError(f"bad rate limit {rule!r}")
    count, n, unit = m.groups()
    return int(count), float(int(n or 1) * _UNITS[unit])


def hash_ip(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


@dataclass
class RateLimiter:
    """Sliding-window limits per (rule, hashed IP), in process memory.

    One API instance is the deployment target; with several, swap for a Redis-backed
    limiter (SPEC §3). Requests count when they arrive, valid or not.
    """

    rules: dict[str, tuple[int, float]]
    clock: Callable[[], float] = time.monotonic
    _hits: dict[tuple[str, str], deque[float]] = field(default_factory=lambda: {})

    def hit(self, rule: str, key: str) -> int | None:
        """Record a request; return None if allowed, else seconds until the next slot."""
        limit, window = self.rules[rule]
        now = self.clock()
        hits = self._hits.setdefault((rule, key), deque())
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, math.ceil(hits[0] + window - now))
        hits.append(now)
        return None
