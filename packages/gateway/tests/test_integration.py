"""Real Gemini calls. Opt-in only: skipped unless GEMINI_API_KEY is set, and CI deselects the
`integration` marker. Run locally with:

    uv run --env-file .env pytest -m integration
"""

import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from docagent_gateway import Gateway, MemoryLedger, load_config

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set"),
]

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


class Sum(BaseModel):
    a: int
    b: int
    total: int


@pytest.mark.parametrize("tier", ["default", "strong"])
async def test_tiny_structured_call(tier: str) -> None:
    env = {"DAILY_BUDGET_USD": "1.00", "SESSION_BUDGET_USD": "0.10", **os.environ}
    ledger = MemoryLedger()
    gw = Gateway(load_config(CONFIG_DIR, env), ledger)
    out = await gw.complete(
        [{"role": "user", "content": "a=2, b=3. Return a, b and their total."}],
        tier=tier,
        response_schema=Sum,
    )
    assert out.parsed == Sum(a=2, b=3, total=5)
    assert out.model == gw.config.models.tiers[tier].model
    assert out.tokens_in > 0 and out.tokens_out > 0 and out.cost_usd > 0
    assert len(ledger.entries) == out.calls
