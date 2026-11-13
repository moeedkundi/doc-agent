"""Gateway configuration: model tiers and prices (config/models.yaml), call limits
(config/limits.yaml `llm:`), and budgets + API keys from environment variables."""

import os
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

ReasoningEffort = Literal["minimal", "low", "medium", "high"]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PricePeriod(_Strict):
    until: date | None = None
    """Last UTC day this price applies (inclusive); None means open-ended."""
    input_per_mtok: Decimal = Field(ge=0)
    output_per_mtok: Decimal = Field(ge=0)


class TierConfig(_Strict):
    model: str
    provider: Literal["gemini"]
    api_key_env: str
    reasoning_effort: ReasoningEffort | None = None
    prices: list[PricePeriod] = Field(min_length=1)

    @model_validator(mode="after")
    def _prices_ordered(self) -> Self:
        dated = [p.until for p in self.prices if p.until is not None]
        if dated != sorted(dated):
            raise ValueError("price periods must be in chronological order")
        if any(p.until is None for p in self.prices[:-1]):
            raise ValueError("only the last price period may be open-ended")
        return self

    @property
    def litellm_model(self) -> str:
        return f"{self.provider}/{self.model}"

    def price_on(self, day: date) -> PricePeriod:
        for period in self.prices:
            if period.until is None or day <= period.until:
                return period
        raise LookupError(f"no price configured for {self.model} on {day}; update models.yaml")


class ModelsConfig(_Strict):
    tiers: dict[str, TierConfig]
    fallback_order: list[str]

    @model_validator(mode="after")
    def _fallbacks_exist(self) -> Self:
        unknown = set(self.fallback_order) - set(self.tiers)
        if unknown:
            raise ValueError(f"fallback_order names unknown tiers: {sorted(unknown)}")
        return self


class LlmLimits(_Strict):
    request_timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    backoff_base_s: float = Field(ge=0)
    backoff_max_s: float = Field(ge=0)
    max_output_tokens: int = Field(gt=0)
    call_reserve_usd: Decimal = Field(ge=0)


class Budgets(_Strict):
    daily_usd: Decimal = Field(ge=0)
    session_usd: Decimal = Field(ge=0)


class GatewayConfig(_Strict):
    models: ModelsConfig
    limits: LlmLimits
    budgets: Budgets
    api_keys: dict[str, SecretStr]
    """Env var name -> key. SecretStr keeps keys out of repr/str/logs."""

    def api_key_for(self, tier: TierConfig) -> SecretStr:
        key = self.api_keys.get(tier.api_key_env)
        if key is None or not key.get_secret_value():
            raise LookupError(f"{tier.api_key_env} is not set")
        return key


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data: object = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return dict(data)  # pyright: ignore[reportUnknownArgumentType]


def _required_decimal(env: Mapping[str, str], name: str) -> Decimal:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        raise LookupError(f"{name} must be set (budget caps are mandatory)")
    return Decimal(raw)


def load_config(
    config_dir: Path | None = None, env: Mapping[str, str] | None = None
) -> GatewayConfig:
    """Load from `config_dir` (default: $DOCAGENT_CONFIG_DIR or ./config) and the environment."""
    env = os.environ if env is None else env
    config_dir = config_dir or Path(env.get("DOCAGENT_CONFIG_DIR", "config"))
    models = ModelsConfig.model_validate(_load_yaml(config_dir / "models.yaml"))
    limits = LlmLimits.model_validate(_load_yaml(config_dir / "limits.yaml")["llm"])
    budgets = Budgets(
        daily_usd=_required_decimal(env, "DAILY_BUDGET_USD"),
        session_usd=_required_decimal(env, "SESSION_BUDGET_USD"),
    )
    key_names = {t.api_key_env for t in models.tiers.values()}
    api_keys = {name: SecretStr(env[name]) for name in key_names if env.get(name)}
    return GatewayConfig(models=models, limits=limits, budgets=budgets, api_keys=api_keys)
