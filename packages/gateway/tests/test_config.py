from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from docagent_gateway import compute_cost, load_config
from docagent_gateway.config import PricePeriod, TierConfig

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
ENV = {"DAILY_BUDGET_USD": "1.00", "SESSION_BUDGET_USD": "0.10", "GEMINI_API_KEY": "AIzaSyX-k"}


def test_repo_config_loads_both_gemini_tiers() -> None:
    cfg = load_config(CONFIG_DIR, ENV)
    assert set(cfg.models.tiers) == {"default", "strong"}
    assert cfg.models.fallback_order == ["default", "strong"]
    for tier in cfg.models.tiers.values():
        assert tier.provider == "gemini" and tier.api_key_env == "GEMINI_API_KEY"
        assert not tier.model.startswith("TODO")
    assert cfg.budgets.daily_usd == Decimal("1.00")
    assert cfg.limits.max_retries >= 1


def test_budget_env_vars_are_mandatory() -> None:
    with pytest.raises(LookupError, match="DAILY_BUDGET_USD"):
        load_config(CONFIG_DIR, {"SESSION_BUDGET_USD": "0.1"})


def test_missing_api_key_is_reported_by_name_only() -> None:
    cfg = load_config(CONFIG_DIR, {k: v for k, v in ENV.items() if k != "GEMINI_API_KEY"})
    with pytest.raises(LookupError, match="GEMINI_API_KEY is not set"):
        cfg.api_key_for(cfg.models.tiers["default"])


def test_secret_is_masked_in_repr() -> None:
    cfg = load_config(CONFIG_DIR, ENV)
    assert "AIzaSyX-k" not in repr(cfg) and "AIzaSyX-k" not in str(cfg)
    assert cfg.api_key_for(cfg.models.tiers["default"]).get_secret_value() == "AIzaSyX-k"


def _tier() -> TierConfig:
    return TierConfig(
        model="m",
        provider="gemini",
        api_key_env="K",
        prices=[
            PricePeriod(
                until=date(2026, 11, 30), input_per_mtok=Decimal(2), output_per_mtok=Decimal(10)
            ),
            PricePeriod(input_per_mtok=Decimal(4), output_per_mtok=Decimal(20)),
        ],
    )


def test_price_period_switches_after_until_date() -> None:
    t = _tier()
    assert t.price_on(date(2026, 11, 30)).input_per_mtok == 2  # inclusive
    assert t.price_on(date(2026, 12, 1)).input_per_mtok == 4
    nov = compute_cost(t, 1_000_000, 100_000, datetime(2026, 11, 30, 23, 59, tzinfo=UTC))
    dec = compute_cost(t, 1_000_000, 100_000, datetime(2026, 12, 1, 0, 0, tzinfo=UTC))
    assert (nov, dec) == (Decimal("3.000000"), Decimal("6.000000"))


def test_only_last_price_period_may_be_open_ended() -> None:
    with pytest.raises(ValidationError):
        TierConfig(
            model="m",
            provider="gemini",
            api_key_env="K",
            prices=[
                PricePeriod(input_per_mtok=Decimal(1), output_per_mtok=Decimal(1)),
                PricePeriod(
                    until=date(2027, 1, 1), input_per_mtok=Decimal(1), output_per_mtok=Decimal(1)
                ),
            ],
        )
