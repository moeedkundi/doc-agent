"""Test doubles for the gateway (fake provider, config, harness fixture). No network calls."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import litellm
import pytest
from pydantic import SecretStr

from docagent_gateway.config import (
    Budgets,
    GatewayConfig,
    LlmLimits,
    ModelsConfig,
    PricePeriod,
    TierConfig,
)
from docagent_gateway.gateway import Gateway
from docagent_gateway.ledger import MemoryLedger

FAKE_KEY = "AIzaSyFAKE-unit-test-key-0123456789abcdef"
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def response(
    content: str, tokens_in: int = 1000, tokens_out: int = 200, finish_reason: str = "stop"
) -> litellm.ModelResponse:
    return litellm.ModelResponse(
        model="fake",
        choices=[
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
                "index": 0,
            }
        ],
        usage={
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    )


Script = list[litellm.ModelResponse | Exception]


@dataclass
class FakeProvider:
    """Stands in for litellm.acompletion: replays a script per model, records every call."""

    scripts: dict[str, Script] = field(default_factory=lambda: {})
    calls: list[dict[str, Any]] = field(default_factory=lambda: [])

    async def __call__(self, **kwargs: Any) -> litellm.ModelResponse:
        self.calls.append(kwargs)
        script = self.scripts[kwargs["model"]]
        if not script:
            raise AssertionError(f"unexpected extra call to {kwargs['model']}")
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_config(
    *, daily: str = "1.00", session: str = "0.10", reserve: str = "0.01", max_retries: int = 2
) -> GatewayConfig:
    return GatewayConfig(
        models=ModelsConfig(
            tiers={
                "default": TierConfig(
                    model="flash-model",
                    provider="gemini",
                    api_key_env="GEMINI_API_KEY",
                    reasoning_effort="low",
                    prices=[
                        PricePeriod(
                            until=date(2026, 12, 31),
                            input_per_mtok=Decimal("0.75"),
                            output_per_mtok=Decimal("3.75"),
                        ),
                        PricePeriod(
                            input_per_mtok=Decimal("1.50"), output_per_mtok=Decimal("7.50")
                        ),
                    ],
                ),
                "strong": TierConfig(
                    model="strong-model",
                    provider="gemini",
                    api_key_env="GEMINI_API_KEY",
                    prices=[
                        PricePeriod(
                            input_per_mtok=Decimal("2.00"), output_per_mtok=Decimal("10.00")
                        )
                    ],
                ),
            },
            fallback_order=["default", "strong"],
        ),
        limits=LlmLimits(
            request_timeout_s=30,
            max_retries=max_retries,
            backoff_base_s=1.0,
            backoff_max_s=8.0,
            max_output_tokens=4096,
            call_reserve_usd=Decimal(reserve),
        ),
        budgets=Budgets(daily_usd=Decimal(daily), session_usd=Decimal(session)),
        api_keys={"GEMINI_API_KEY": SecretStr(FAKE_KEY)},
    )


@dataclass
class Harness:
    gateway: Gateway
    provider: FakeProvider
    ledger: MemoryLedger
    sleeps: list[float]


@pytest.fixture
def harness() -> Callable[..., Harness]:
    def build(
        config: GatewayConfig | None = None, ledger: MemoryLedger | None = None, **scripts: Script
    ) -> Harness:
        provider = FakeProvider(
            scripts={f"gemini/{k.replace('_', '-')}": list(v) for k, v in scripts.items()}
        )
        sleeps: list[float] = []

        async def fake_sleep(s: float) -> None:
            sleeps.append(s)

        led = ledger if ledger is not None else MemoryLedger()
        gw = Gateway(
            config or make_config(),
            led,
            completion_fn=provider,
            now=lambda: NOW,
            sleep=fake_sleep,
        )
        return Harness(gw, provider, led, sleeps)

    return build
