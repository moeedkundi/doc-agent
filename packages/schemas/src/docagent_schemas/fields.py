"""The `Field` wrapper: every extracted value carries confidence and provenance."""

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydanticField

MAX_SOURCE_PAGE = 500
"""Sanity bound only (uploads are capped far lower); a model once returned 10**300 here."""


class Field[T](BaseModel):
    """One extracted value plus how sure the model is and where it came from."""

    model_config = ConfigDict(frozen=True)

    value: T | None
    confidence: float = PydanticField(ge=0.0, le=1.0)
    """0..1, model-reported, then calibrated."""
    source_page: int | None = PydanticField(default=None, ge=1, le=MAX_SOURCE_PAGE)
    """1-based page number the value was read from."""
    evidence: str | None = None
    """Short snippet of document text the value came from."""
