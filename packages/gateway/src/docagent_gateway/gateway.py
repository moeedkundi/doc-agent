"""The single entry point for every LLM call in DocAgent (SPEC §8.1).

`Gateway.complete()` resolves a tier to a model, enforces the daily and per-session budget
before each provider call, retries transient failures with backoff, falls back to the next
tier in `fallback_order`, records every call in the cost ledger, and (optionally) parses the
output into a Pydantic model with one repair attempt.
"""

import asyncio
import base64
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast, overload
from uuid import UUID

import litellm
from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from pydantic import BaseModel, ValidationError

from docagent_gateway.config import GatewayConfig, TierConfig
from docagent_gateway.errors import BudgetExceeded, OutputParseError, ProviderError, redact
from docagent_gateway.ledger import Ledger, LedgerEntry, Pool, start_of_utc_day

log = logging.getLogger("docagent.gateway")

# Keep LiteLLM quiet: no debug banners, nothing that could echo request headers.
litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

CompletionFn = Callable[..., Awaitable[Any]]
Message = dict[str, Any]

_MICRO = Decimal("0.000001")
_TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504}
_REPAIR_ERROR_CHARS = 2000


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime_type: str = "image/png"

    def as_content_part(self) -> dict[str, Any]:
        b64 = base64.b64encode(self.data).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{self.mime_type};base64,{b64}"}}


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    text: str
    """Raw text of the final (successful) response."""
    parsed: T | None
    """The validated object when `response_schema` was given."""
    tier: str
    """Tier that produced the answer (differs from the requested one after a fallback)."""
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int
    calls: int
    """Provider calls made, including retries that returned and the repair attempt."""


@dataclass(frozen=True)
class _CallResult:
    text: str
    finish_reason: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int


def compute_cost(tier: TierConfig, tokens_in: int, tokens_out: int, day: datetime) -> Decimal:
    price = tier.price_on(day.astimezone(UTC).date())
    cost = (
        Decimal(tokens_in) * price.input_per_mtok + Decimal(tokens_out) * price.output_per_mtok
    ) / Decimal(1_000_000)
    return cost.quantize(_MICRO, rounding=ROUND_HALF_UP)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute-or-key access (LiteLLM responses support both, test doubles may not)."""
    if isinstance(obj, dict):
        return cast(Any, obj.get(name, default))  # pyright: ignore[reportUnknownMemberType]
    return getattr(obj, name, default)


_RETRY_HINTS = (
    re.compile(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"'),
    re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s", re.IGNORECASE),
)


def retry_hint_seconds(text: str) -> float | None:
    """The provider's own 'retry after' hint, if the error carries one."""
    m = _RETRY_HINTS[0].search(text)
    if m:
        return float(m.group(1))
    m = _RETRY_HINTS[1].search(text)
    if m:
        h, mins, secs = m.groups()
        return int(h or 0) * 3600 + int(mins or 0) * 60 + float(secs)
    return None


def _is_transient(exc: Exception) -> bool:
    if isinstance(
        exc,
        Timeout
        | APIConnectionError
        | RateLimitError
        | ServiceUnavailableError
        | InternalServerError
        | asyncio.TimeoutError,
    ):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in _TRANSIENT_STATUS


@dataclass(frozen=True)
class _Deadline:
    at: float
    """time.monotonic() value by which the whole call must finish"""
    seconds: float

    def exceeded(self, model: str, attempts: int) -> ProviderError:
        return ProviderError(
            f"{model}: no answer within the {self.seconds:g}s deadline "
            f"({attempts} attempt(s) made)",
            kind="DeadlineExceeded",
            transient=False,
        )


class Gateway:
    def __init__(
        self,
        config: GatewayConfig,
        ledger: Ledger,
        *,
        completion_fn: CompletionFn | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._ledger = ledger
        self._completion_fn: CompletionFn = completion_fn or litellm.acompletion
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto
        self._monotonic = monotonic

    @property
    def config(self) -> GatewayConfig:
        return self._config

    # -- public API -------------------------------------------------------------
    @overload
    async def complete[T: BaseModel](
        self,
        messages: Sequence[Message],
        *,
        tier: str,
        response_schema: type[T],
        images: Sequence[ImageInput] | None = None,
        run_id: UUID | None = None,
        session_id: UUID | None = None,
        deadline_s: float | None = None,
    ) -> Completion[T]: ...

    @overload
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: str,
        response_schema: None = None,
        images: Sequence[ImageInput] | None = None,
        run_id: UUID | None = None,
        session_id: UUID | None = None,
        deadline_s: float | None = None,
    ) -> Completion[BaseModel]: ...

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tier: str,
        response_schema: type[BaseModel] | None = None,
        images: Sequence[ImageInput] | None = None,
        run_id: UUID | None = None,
        session_id: UUID | None = None,
        deadline_s: float | None = None,
    ) -> Completion[Any]:
        """`deadline_s` caps the whole call (attempts, backoff, repair, fallback tiers): each
        attempt's timeout shrinks to the time left, and once it's used up the call fails with
        a non-transient ProviderError(kind="DeadlineExceeded") instead of retrying."""
        if tier not in self._config.models.tiers:
            raise ValueError(f"unknown tier {tier!r}")
        msgs = _attach_images(list(messages), images or ())
        deadline = (
            None if deadline_s is None else _Deadline(self._monotonic() + deadline_s, deadline_s)
        )

        last_error: ProviderError | None = None
        for candidate in self._tier_chain(tier):
            try:
                return await self._complete_on(
                    candidate, msgs, response_schema, run_id, session_id, deadline
                )
            except ProviderError as exc:
                if not exc.transient:
                    raise
                last_error = exc
                log.warning("tier %s exhausted (%s); trying next tier", candidate, exc.kind)
        if last_error is None:  # unreachable: the tier chain is never empty
            raise RuntimeError("no tiers to try")
        raise last_error

    # -- internals ----------------------------------------------------------------
    def _tier_chain(self, tier: str) -> list[str]:
        order = self._config.models.fallback_order
        if tier not in order:
            return [tier]
        return order[order.index(tier) :]

    async def _complete_on(
        self,
        tier_name: str,
        messages: list[Message],
        schema: type[BaseModel] | None,
        run_id: UUID | None,
        session_id: UUID | None,
        deadline: _Deadline | None = None,
    ) -> Completion[Any]:
        tier = self._config.models.tiers[tier_name]
        response_format = _response_format(schema) if schema is not None else None

        results: list[_CallResult] = []
        first = await self._call_with_retries(
            tier, messages, response_format, run_id, session_id, deadline
        )
        results.append(first)
        parsed: BaseModel | None = None
        final = first
        if schema is not None:
            try:
                parsed = schema.model_validate_json(first.text)
            except ValidationError as err:
                log.info("structured output failed validation on %s; repairing once", tier.model)
                repair_msgs = [
                    *messages,
                    {"role": "assistant", "content": first.text},
                    {"role": "user", "content": _repair_prompt(err, first.finish_reason)},
                ]
                final = await self._call_with_retries(
                    tier, repair_msgs, response_format, run_id, session_id, deadline
                )
                results.append(final)
                try:
                    parsed = schema.model_validate_json(final.text)
                except ValidationError as err2:
                    raise OutputParseError(
                        f"{tier.model} output did not match {schema.__name__} after one repair "
                        f"attempt ({err2.error_count()} errors)"
                    ) from None

        return Completion(
            text=final.text,
            parsed=parsed,
            tier=tier_name,
            model=tier.model,
            tokens_in=sum(r.tokens_in for r in results),
            tokens_out=sum(r.tokens_out for r in results),
            cost_usd=sum((r.cost_usd for r in results), Decimal(0)),
            latency_ms=sum(r.latency_ms for r in results),
            calls=len(results),
        )

    async def check_budget(self, session_id: UUID | None = None) -> None:
        """Raise BudgetExceeded if a call now would break the daily or session cap."""
        await self._check_budget(session_id)

    async def spent_today(self) -> Decimal:
        return await asyncio.to_thread(self._ledger.spent_since, start_of_utc_day(self._now()))

    async def _check_budget(self, session_id: UUID | None) -> Pool:
        """Raise BudgetExceeded if the next call would break a cap; return the pool to charge.

        Eval sessions are capped only by the eval budget (no per-session cap); everything
        else by the demo's daily cap and the per-session cap.
        """
        reserve = self._config.limits.call_reserve_usd
        budgets = self._config.budgets
        midnight = start_of_utc_day(self._now())
        pool = await asyncio.to_thread(self._ledger.pool_of, session_id)
        if pool == "eval":
            spent_eval = await asyncio.to_thread(self._ledger.spent_since, midnight, "eval")
            if spent_eval + reserve > budgets.eval_usd:
                raise BudgetExceeded("eval", spent_eval, budgets.eval_usd)
            return pool
        spent_today = await asyncio.to_thread(self._ledger.spent_since, midnight, "demo")
        if spent_today + reserve > budgets.daily_usd:
            raise BudgetExceeded("daily", spent_today, budgets.daily_usd)
        if session_id is not None:
            spent_session = await asyncio.to_thread(self._ledger.spent_by_session, session_id)
            if spent_session + reserve > budgets.session_usd:
                raise BudgetExceeded("session", spent_session, budgets.session_usd)
        return pool

    async def _call_with_retries(
        self,
        tier: TierConfig,
        messages: list[Message],
        response_format: dict[str, Any] | None,
        run_id: UUID | None,
        session_id: UUID | None,
        deadline: _Deadline | None = None,
    ) -> _CallResult:
        limits = self._config.limits
        key = self._config.api_key_for(tier)
        kwargs: dict[str, Any] = {
            "model": tier.litellm_model,
            "messages": messages,
            "api_key": key.get_secret_value(),
            "timeout": limits.request_timeout_s,
            "num_retries": 0,
            "max_tokens": limits.max_output_tokens,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tier.reasoning_effort is not None:
            kwargs["reasoning_effort"] = tier.reasoning_effort

        attempt = 0
        while True:
            pool = await self._check_budget(session_id)
            if deadline is not None:
                left = deadline.at - self._monotonic()
                if left <= 0:
                    raise deadline.exceeded(tier.model, attempt)
                kwargs["timeout"] = min(limits.request_timeout_s, left)
            started = time.perf_counter()
            try:
                response = await self._completion_fn(**kwargs)
            except Exception as exc:
                transient = _is_transient(exc)
                message = redact(f"{type(exc).__name__}: {exc}", [key.get_secret_value()])
                hint = retry_hint_seconds(str(exc))
                # A quota that resets in hours won't recover within our backoff: stop retrying
                # this tier (a fallback tier may still help).
                worth_retrying = hint is None or hint <= limits.backoff_max_s
                if transient and worth_retrying and attempt < limits.max_retries:
                    delay = min(limits.backoff_max_s, limits.backoff_base_s * 2**attempt)
                    delay *= 0.5 + self._rng.random()  # jitter 0.5x..1.5x
                    if deadline is not None and self._monotonic() + delay >= deadline.at:
                        raise deadline.exceeded(tier.model, attempt + 1) from None
                    log.warning(
                        "%s attempt %d failed (%s); retrying in %.1fs",
                        tier.model,
                        attempt + 1,
                        type(exc).__name__,
                        delay,
                    )
                    attempt += 1
                    await self._sleep(delay)
                    continue
                # `from None` + a fresh message: the original exception (which may carry
                # request details) is not chained into tracebacks.
                raise ProviderError(
                    f"{tier.model} call failed after {attempt + 1} attempt(s): {message}",
                    kind=type(exc).__name__,
                    transient=transient,
                ) from None

            latency_ms = int((time.perf_counter() - started) * 1000)
            result = self._to_result(tier, response, latency_ms)
            entry = LedgerEntry(
                ts=self._now(),
                model=tier.model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cost_usd=result.cost_usd,
                run_id=run_id,
                session_id=session_id,
                pool=pool,
            )
            await asyncio.to_thread(self._ledger.record, entry)
            log.info(
                "llm call tier_model=%s tokens_in=%d tokens_out=%d cost_usd=%s latency_ms=%d",
                tier.model,
                result.tokens_in,
                result.tokens_out,
                result.cost_usd,
                latency_ms,
            )
            return result

    def _to_result(self, tier: TierConfig, response: Any, latency_ms: int) -> _CallResult:
        choices: list[Any] = _get(response, "choices") or []
        choice: Any = choices[0] if choices else None
        message = _get(choice, "message") if choice is not None else None
        text = _get(message, "content") or ""
        usage = _get(response, "usage")
        tokens_in = int(_get(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(_get(usage, "completion_tokens", 0) or 0)  # includes thinking
        return _CallResult(
            text=str(text),
            finish_reason=_get(choice, "finish_reason") if choice is not None else None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=compute_cost(tier, tokens_in, tokens_out, self._now()),
            latency_ms=latency_ms,
        )


def _attach_images(messages: list[Message], images: Sequence[ImageInput]) -> list[Message]:
    """Append images to the last user message as OpenAI-style content parts."""
    if not images:
        return messages
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            content = messages[i].get("content")
            parts: list[Any] = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else list(content or [])
            )
            parts.extend(img.as_content_part() for img in images)
            messages = [*messages[:i], {**messages[i], "content": parts}, *messages[i + 1 :]]
            return messages
    raise ValueError("images given but there is no user message to attach them to")


def _response_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
            "strict": True,
        },
    }


def _repair_prompt(err: ValidationError, finish_reason: str | None) -> str:
    details = json.dumps(
        [{"loc": list(e["loc"]), "msg": e["msg"]} for e in err.errors()[:20]], default=str
    )[:_REPAIR_ERROR_CHARS]
    truncated = " Your output was cut off; be more concise." if finish_reason == "length" else ""
    return (
        "Your previous reply did not validate against the required JSON schema."
        f"{truncated} Validation errors: {details}. "
        "Reply again with ONLY the complete, corrected JSON object."
    )
