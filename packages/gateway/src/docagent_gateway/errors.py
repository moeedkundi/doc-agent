"""Gateway exceptions. Provider errors are re-raised as `ProviderError` with secrets redacted
and the original exception detached, so no API key can leak through messages or tracebacks."""

import re
from collections.abc import Iterable
from decimal import Decimal
from typing import Literal

# Shapes an API key can take inside provider error text, beyond the literal key value.
_KEY_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google API keys
    re.compile(r"(?i)(key|api[_-]?key|x-goog-api-key)([\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = _KEY_PATTERNS[0].sub("***", text)
    text = _KEY_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    return _KEY_PATTERNS[2].sub(lambda m: f"{m.group(1)}***", text)


class GatewayError(Exception):
    """Base class for everything the gateway raises."""


class BudgetExceeded(GatewayError):
    def __init__(self, scope: Literal["daily", "session"], spent: Decimal, cap: Decimal) -> None:
        self.scope = scope
        self.spent = spent
        self.cap = cap
        super().__init__(
            f"{scope} LLM budget exhausted: spent ${spent} of ${cap} "
            "(each call needs a reserve left)"
        )


class ProviderError(GatewayError):
    """The provider failed (after retries and fallbacks). Message is already redacted."""

    def __init__(self, message: str, *, kind: str, transient: bool) -> None:
        self.kind = kind
        self.transient = transient
        super().__init__(message)


class OutputParseError(GatewayError):
    """The model's output did not match the requested schema, even after one repair attempt."""
