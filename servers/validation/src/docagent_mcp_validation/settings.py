"""Validation settings from config/limits.yaml (`validation:` and `po_matching:`)."""

import re
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from docagent_mcp_common import load_limits
from docagent_schemas import MatchTolerances


class ValidationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    review_confidence_threshold: float = Field(default=0.80, ge=0, le=1)
    amount_tolerance: Decimal = Decimal("0.01")
    po_number_pattern: str = r"^[A-Za-z0-9][A-Za-z0-9 ./_#-]{1,39}$"
    match: MatchTolerances = MatchTolerances()

    @property
    def po_number_re(self) -> re.Pattern[str]:
        return re.compile(self.po_number_pattern)


def load_settings(config_dir: Path | None = None) -> ValidationSettings:
    limits = load_limits(config_dir)
    v = limits["validation"]
    m = limits["po_matching"]
    return ValidationSettings(
        review_confidence_threshold=v["review_confidence_threshold"],
        amount_tolerance=Decimal(str(v["amount_tolerance"])),
        po_number_pattern=v["po_number_pattern"],
        match=MatchTolerances(
            unit_price_pct=Decimal(str(m["unit_price_tolerance_pct"])),
            amount=Decimal(str(v["amount_tolerance"])),
            description_min_score=m["description_match_min_score"],
            description_fallback_score=m["description_match_fallback_score"],
        ),
    )
