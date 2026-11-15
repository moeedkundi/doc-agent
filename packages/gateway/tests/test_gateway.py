"""Gateway behaviour with a fake provider: routing, structured output, repair, retries,
fallback, ledger, budgets and secret hygiene."""

import logging
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from litellm.exceptions import (
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServiceUnavailableError,
)
from pydantic import BaseModel

from docagent_gateway import (
    BudgetExceeded,
    ImageInput,
    LedgerEntry,
    MemoryLedger,
    OutputParseError,
    ProviderError,
)
from docagent_gateway.gateway import retry_hint_seconds
from docagent_gateway.testing import FAKE_KEY, NOW, Harness, Slow, make_config, response

MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "extract"}]


class Thing(BaseModel):
    name: str
    qty: int


def _rate_limited() -> RateLimitError:
    return RateLimitError("slow down", llm_provider="gemini", model="m")


async def test_routes_tier_to_configured_model_with_limits(
    harness: Callable[..., Harness],
) -> None:
    h = harness(flash_model=[response("hello")])
    out = await h.gateway.complete(MSGS, tier="default")
    call = h.provider.calls[0]
    assert call["model"] == "gemini/flash-model"
    assert call["api_key"] == FAKE_KEY
    assert call["timeout"] == 30
    assert call["num_retries"] == 0  # retries are ours, not LiteLLM's
    assert call["max_tokens"] == 4096
    assert call["reasoning_effort"] == "low"
    assert "response_format" not in call
    assert (out.text, out.tier, out.model, out.parsed) == ("hello", "default", "flash-model", None)


async def test_cost_is_tokens_times_price_and_ledgered(harness: Callable[..., Harness]) -> None:
    h = harness(flash_model=[response("x", tokens_in=1834, tokens_out=412)])
    sid, rid = uuid4(), uuid4()
    out = await h.gateway.complete(MSGS, tier="default", run_id=rid, session_id=sid)
    # 1834 * 0.75/1M + 412 * 3.75/1M = 0.0013755 + 0.001545 = 0.0029205
    assert out.cost_usd == Decimal("0.002921")
    assert (out.tokens_in, out.tokens_out, out.calls) == (1834, 412, 1)
    [entry] = h.ledger.entries
    assert entry == LedgerEntry(
        ts=NOW,
        model="flash-model",
        tokens_in=1834,
        tokens_out=412,
        cost_usd=Decimal("0.002921"),
        run_id=rid,
        session_id=sid,
    )


async def test_structured_output_parsed_with_json_schema(harness: Callable[..., Harness]) -> None:
    h = harness(flash_model=[response('{"name": "toner", "qty": 3}')])
    out = await h.gateway.complete(MSGS, tier="default", response_schema=Thing)
    assert out.parsed == Thing(name="toner", qty=3)
    fmt = h.provider.calls[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] == Thing.model_json_schema()


async def test_images_are_attached_to_last_user_message(harness: Callable[..., Harness]) -> None:
    h = harness(flash_model=[response("ok")])
    await h.gateway.complete(MSGS, tier="default", images=[ImageInput(b"\x89PNG", "image/png")])
    sent = h.provider.calls[0]["messages"]
    assert sent[0] == MSGS[0]
    parts = sent[1]["content"]
    assert parts[0] == {"type": "text", "text": "extract"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,iVBORw=="
    assert MSGS[1]["content"] == "extract"  # caller's messages untouched


async def test_invalid_output_is_repaired_once(harness: Callable[..., Harness]) -> None:
    h = harness(
        flash_model=[response('{"name": "toner"}'), response('{"name": "toner", "qty": 2}')]
    )
    out = await h.gateway.complete(MSGS, tier="default", response_schema=Thing)
    assert out.parsed == Thing(name="toner", qty=2)
    assert out.calls == 2 and len(h.ledger.entries) == 2
    repair = h.provider.calls[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": '{"name": "toner"}'}
    assert "qty" in repair[-1]["content"] and "ONLY" in repair[-1]["content"]


async def test_second_invalid_output_raises_but_both_calls_are_costed(
    harness: Callable[..., Harness],
) -> None:
    h = harness(flash_model=[response("not json"), response("{}")])
    with pytest.raises(OutputParseError):
        await h.gateway.complete(MSGS, tier="default", response_schema=Thing)
    assert len(h.ledger.entries) == 2


async def test_transient_errors_retry_with_backoff(harness: Callable[..., Harness]) -> None:
    h = harness(flash_model=[_rate_limited(), _rate_limited(), response("ok")])
    out = await h.gateway.complete(MSGS, tier="default")
    assert out.text == "ok"
    assert len(h.sleeps) == 2
    assert 0.5 <= h.sleeps[0] <= 1.5 and 1.0 <= h.sleeps[1] <= 3.0  # 1s, 2s with jitter
    assert len(h.ledger.entries) == 1  # failed attempts cost nothing


async def test_exhausted_tier_falls_back_to_next(harness: Callable[..., Harness]) -> None:
    unavailable = ServiceUnavailableError("overloaded", llm_provider="gemini", model="m")
    h = harness(flash_model=[unavailable] * 3, strong_model=[response("from strong")])
    out = await h.gateway.complete(MSGS, tier="default")
    assert (out.text, out.tier, out.model) == ("from strong", "strong", "strong-model")


async def test_quota_with_long_retry_hint_skips_retries_but_falls_back(
    harness: Callable[..., Harness],
) -> None:
    quota = RateLimitError(
        'RESOURCE_EXHAUSTED ... Please retry in 10h39m26.6s. "retryDelay": "38366s"',
        llm_provider="gemini",
        model="m",
    )
    h = harness(flash_model=[quota], strong_model=[response("ok")])
    out = await h.gateway.complete(MSGS, tier="default")
    assert out.tier == "strong" and h.sleeps == []


def test_retry_hint_parsing() -> None:
    assert retry_hint_seconds('"retryDelay": "38366s"') == 38366
    assert retry_hint_seconds("Please retry in 10h39m26.5s.") == 38366.5
    assert retry_hint_seconds("Please retry in 4.2s") == 4.2
    assert retry_hint_seconds("server overloaded") is None


async def test_strong_tier_has_nothing_to_fall_back_to(harness: Callable[..., Harness]) -> None:
    h = harness(strong_model=[_rate_limited()] * 3)
    with pytest.raises(ProviderError) as exc:
        await h.gateway.complete(MSGS, tier="strong")
    assert exc.value.transient and exc.value.kind == "RateLimitError"


async def test_non_transient_error_fails_fast(harness: Callable[..., Harness]) -> None:
    bad = BadRequestError("schema rejected", model="m", llm_provider="gemini")
    h = harness(flash_model=[bad])
    with pytest.raises(ProviderError) as exc:
        await h.gateway.complete(MSGS, tier="default")
    assert not exc.value.transient
    assert h.sleeps == [] and len(h.provider.calls) == 1


async def test_api_key_never_leaks(
    harness: Callable[..., Harness], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    leaky = AuthenticationError(
        f"invalid key {FAKE_KEY} (x-goog-api-key: {FAKE_KEY})", llm_provider="gemini", model="m"
    )
    retry_leak = RateLimitError(f"quota for key={FAKE_KEY}", llm_provider="g", model="m")
    h = harness(flash_model=[retry_leak, leaky])
    with pytest.raises(ProviderError) as exc:
        await h.gateway.complete(MSGS, tier="default")
    err = exc.value
    assert FAKE_KEY not in str(err) and FAKE_KEY not in repr(err)
    assert err.__cause__ is None and err.__suppress_context__
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in repr(h.gateway.config) and FAKE_KEY not in str(h.gateway.config)


async def test_unknown_tier_is_rejected(harness: Callable[..., Harness]) -> None:
    with pytest.raises(ValueError, match="unknown tier"):
        await harness().gateway.complete(MSGS, tier="turbo")


# -- budgets -----------------------------------------------------------------------


def _spent(ledger: MemoryLedger, usd: str, *, session: object = None, days_ago: int = 0) -> None:
    ledger.record(
        LedgerEntry(
            ts=NOW - timedelta(days=days_ago),
            model="m",
            tokens_in=0,
            tokens_out=0,
            cost_usd=Decimal(usd),
            session_id=session,  # type: ignore[arg-type]
        )
    )


async def test_daily_budget_exceeded_blocks_before_calling_provider(
    harness: Callable[..., Harness],
) -> None:
    ledger = MemoryLedger()
    _spent(ledger, "0.995")  # cap 1.00, reserve 0.01 -> 0.995 + 0.01 > 1.00
    h = harness(ledger=ledger, flash_model=[response("never")])
    with pytest.raises(BudgetExceeded) as exc:
        await h.gateway.complete(MSGS, tier="default")
    assert (exc.value.scope, exc.value.spent, exc.value.cap) == (
        "daily",
        Decimal("0.995"),
        Decimal("1.00"),
    )
    assert h.provider.calls == []


async def test_yesterdays_spend_does_not_count(harness: Callable[..., Harness]) -> None:
    ledger = MemoryLedger()
    _spent(ledger, "5.00", days_ago=1)
    h = harness(ledger=ledger, flash_model=[response("ok")])
    assert (await h.gateway.complete(MSGS, tier="default")).text == "ok"


async def test_session_budget_is_per_session(harness: Callable[..., Harness]) -> None:
    ledger = MemoryLedger()
    heavy, light = uuid4(), uuid4()
    _spent(ledger, "0.095", session=heavy)  # cap 0.10, reserve 0.01
    h = harness(ledger=ledger, flash_model=[response("ok")])
    with pytest.raises(BudgetExceeded) as exc:
        await h.gateway.complete(MSGS, tier="default", session_id=heavy)
    assert exc.value.scope == "session"
    assert (await h.gateway.complete(MSGS, tier="default", session_id=light)).text == "ok"


async def test_budget_runs_out_mid_session(harness: Callable[..., Harness]) -> None:
    # Each call costs 4000*0.75/1M + 1000*3.75/1M = 0.00675; session cap 0.02, reserve 0.005.
    # Calls are allowed while spent + reserve <= cap: 0, 0.00675, 0.0135 pass; 0.02025 fails.
    config = make_config(session="0.02", reserve="0.005")
    h = harness(config, flash_model=[response("ok", 4000, 1000)] * 5)
    sid = uuid4()
    for _ in range(3):
        await h.gateway.complete(MSGS, tier="default", session_id=sid)
    with pytest.raises(BudgetExceeded) as exc:
        await h.gateway.complete(MSGS, tier="default", session_id=sid)
    assert exc.value.spent == Decimal("0.020250")
    # Overshoot past the cap is bounded by one call's cost minus the reserve.
    assert len(h.provider.calls) == 3


async def test_retries_recheck_the_budget(harness: Callable[..., Harness]) -> None:
    """A retry is a new provider call and must not slip past a cap reached meanwhile."""
    ledger = MemoryLedger()
    h = harness(ledger=ledger, flash_model=[_rate_limited(), response("ok")])
    original_sleep = h.gateway._sleep  # pyright: ignore[reportPrivateUsage]

    async def sleep_and_overspend(s: float) -> None:
        _spent(ledger, "2.00")
        await original_sleep(s)

    h.gateway._sleep = sleep_and_overspend  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(BudgetExceeded):
        await h.gateway.complete(MSGS, tier="default")


async def test_eval_sessions_use_their_own_budget(harness: Callable[..., Harness]) -> None:
    ledger = MemoryLedger()
    evaluator = uuid4()
    ledger.eval_sessions.add(evaluator)
    _spent(ledger, "0.995")  # the demo's daily budget is all but gone
    config = make_config()
    config = config.model_copy(
        update={"budgets": config.budgets.model_copy(update={"eval_usd": Decimal("3.00")})}
    )
    h = harness(config, ledger, flash_model=[response("ok", 4000, 1000)] * 2)
    out = await h.gateway.complete(MSGS, tier="default", session_id=evaluator)
    assert out.text == "ok"
    assert h.ledger.entries[-1].pool == "eval"
    assert ledger.spent_since(NOW - timedelta(hours=1), "demo") == Decimal("0.995")  # untouched
    with pytest.raises(BudgetExceeded) as exc:  # a visitor still hits the demo cap
        await h.gateway.complete(MSGS, tier="default", session_id=uuid4())
    assert exc.value.scope == "daily"


async def test_eval_budget_has_its_own_cap(harness: Callable[..., Harness]) -> None:
    ledger = MemoryLedger()
    evaluator = uuid4()
    ledger.eval_sessions.add(evaluator)
    ledger.record(
        LedgerEntry(
            ts=NOW,
            model="m",
            tokens_in=0,
            tokens_out=0,
            cost_usd=Decimal("2.995"),
            session_id=evaluator,
            pool="eval",
        )
    )
    config = make_config()
    config = config.model_copy(
        update={"budgets": config.budgets.model_copy(update={"eval_usd": Decimal("3.00")})}
    )
    h = harness(config, ledger, flash_model=[response("never")])
    with pytest.raises(BudgetExceeded) as exc:
        await h.gateway.complete(MSGS, tier="default", session_id=evaluator)
    assert exc.value.scope == "eval" and h.provider.calls == []


# -- deadline ---------------------------------------------------------------------------------


async def test_deadline_shrinks_the_timeout_and_stops_retrying(
    harness: Callable[..., Harness],
) -> None:
    # A slow strong tier: first attempt hits the (shrunken) timeout, and there's no time left
    # to retry within the 25 s deadline, so the call fails fast instead of taking minutes.
    h = harness(strong_model=[Slow(90, response("late")), response("never asked")])
    with pytest.raises(ProviderError) as err:
        await h.gateway.complete(MSGS, tier="strong", deadline_s=25)
    assert err.value.kind == "DeadlineExceeded" and not err.value.transient
    assert h.provider.calls[0]["timeout"] == 25  # min(request timeout 30, 25 left)
    assert len(h.provider.calls) == 1 and h.ledger.entries == []
    assert h.provider.clock[0] <= 25


async def test_deadline_allows_retries_that_fit(harness: Callable[..., Harness]) -> None:
    unavailable = ServiceUnavailableError("overloaded", llm_provider="gemini", model="m")
    h = harness(strong_model=[unavailable, Slow(5, response("ok"))])
    out = await h.gateway.complete(MSGS, tier="strong", deadline_s=25)
    assert out.text == "ok" and len(h.sleeps) == 1
    assert h.provider.calls[1]["timeout"] < 25  # what was left after the first try + backoff


async def test_no_deadline_keeps_the_request_timeout(harness: Callable[..., Harness]) -> None:
    h = harness(strong_model=[Slow(20, response("ok"))])
    out = await h.gateway.complete(MSGS, tier="strong")
    assert out.text == "ok" and h.provider.calls[0]["timeout"] == 30
