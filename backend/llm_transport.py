"""Shared HTTP transport for the OpenAI-format LLM rails (083).

Why this exists — the bug it was written for:

    Error: KimiError: kimi request failed:

That message reached a live user with **nothing after the colon**. The cause is a
one-line httpx footgun: a read timeout is raised as `httpx.ReadTimeout` mapped
from an httpcore timeout that carries **no message**, so `str(e)` is the empty
string and `{e}` interpolates to nothing. Every OpenAI-format client here had the
same `f"… request failed: {e}"` line, so the single most likely production
failure was also the one we could learn the least from.

Three things are fixed together, because fixing only the message would leave the
turn dying anyway:

1. **An error is never empty.** The exception CLASS is always named, and a
   message-less transport error gets a synthesised description ("no response
   within the 120s budget"). The response body, when there is one, is preserved
   — 065's verbose-error path and the failover classifier both read it.
2. **A bounded, deadline-aware retry.** A transient connect/5xx failure gets one
   more attempt, but **all attempts share one wall-clock budget**
   (`<RAIL>_TIMEOUT_S`), so retrying can never multiply the time a user waits.
   A full read timeout consumes the budget and therefore does not retry — the
   deadline enforces that, not a special case.
3. **A machine-readable `reason`.** `timeout | network | rate_limit |
   overloaded | None`, carried on the raised error so `agent._failover_reason`
   can route a transport failure to the DeepSeek fallback instead of ending the
   turn on a red bubble. Guessing at provider prose (080's `_OPENAI_BILLING_MARKERS`
   caveat) is unavoidable for *quota* wording; it is NOT necessary for transport
   failures, which we can classify from the exception type with certainty.

Why a new module rather than `openai_compat`: that module's contract is "pure
functions only: no I/O, no env reads", and this is entirely I/O and env. Same
motivation though — 071 collapsed two wire-translation copies into one so the
rails could not drift; three copies of the retry/timeout/error policy would drift
in exactly the same way, and the fallback rail sharing the primary's blind spot
is how a failover turns one bad turn into two.

House style: no new dependency (httpx is already the transport for all three
rails), mock-first is unaffected (clients still short-circuit before calling in).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

# One model call's total wall-clock budget, shared across attempts.
# 60s (the pre-083 default) was measured to be right at the edge: live Kimi
# iterations were completing in ~55s, so a normal agentic turn was one slow
# tool payload away from a timeout that read as a hard failure.
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_CONNECT_TIMEOUT_S = 10.0
DEFAULT_MAX_ATTEMPTS = 2

# Don't start another attempt unless this much of the budget is left — a retry
# that is certain to time out is worse than the error it is trying to avoid.
MIN_RETRY_BUDGET_S = 15.0
RETRY_BACKOFF_S = 1.0

DETAIL_MAX = 300  # cap a provider body so it can't dump a huge blob into a log

# Retried in-place. `timeout` is deliberately absent: a read timeout has already
# spent the budget, so the deadline check below refuses the retry anyway, and
# re-sending a prompt the model is evidently slow on just doubles the wait.
# `rate_limit` is absent too — the right response is the fallback rail, not a
# tighter retry loop against a provider that just said "slow down".
RETRYABLE_REASONS = ("network", "overloaded")

# Server-side statuses worth another attempt (or a fallback rail).
_OVERLOADED_STATUSES = (408, 425, 500, 502, 503, 504, 529)


class LLMTransportError(Exception):
    """A rail-neutral transport failure. Each client re-raises it as its own
    error type (`KimiError`, `DeepSeekError`, `OpenAIError`) so existing
    `except` clauses and tests keep working, carrying `reason` through."""

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        # Read by `agent._failover_reason` — a provider-declared hint beats
        # substring-guessing at prose.
        self.failover_reason = reason


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        log.warning("invalid %s — using %.1f", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        log.warning("invalid %s — using %d", name, default)
        return default


def budget_seconds(prefix: str) -> float:
    """`<PREFIX>_TIMEOUT_S` — the TOTAL budget for one model call, retries
    included. Same env var name each rail already used, so an operator's
    existing value keeps working (it just means more than it did)."""
    return max(1.0, _env_float(f"{prefix}_TIMEOUT_S", DEFAULT_TIMEOUT_S))


def connect_timeout(prefix: str) -> float:
    """A separate, short connect timeout. Reaching the host should take a second;
    only *generation* is slow. Without the split, a dead host held the full budget."""
    return max(1.0, _env_float(f"{prefix}_CONNECT_TIMEOUT_S", DEFAULT_CONNECT_TIMEOUT_S))


def max_attempts(prefix: str) -> int:
    """`<PREFIX>_MAX_ATTEMPTS` — total attempts, not extra retries. 1 disables."""
    return max(1, _env_int(f"{prefix}_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))


def classify_transport_error(e: BaseException) -> str | None:
    """`timeout | network | rate_limit | overloaded`, or None when the failure is
    NOT transient (auth, bad request, an unexpected 4xx).

    Type-based, not text-based: unlike quota wording, an httpx exception class is
    a fact, so this classification can't be wrong the way 080's defensive
    billing-marker guesses can.
    """
    # TimeoutException is a subclass of TransportError — check it first.
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, httpx.TransportError):
        return "network"
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status == 429:
        return "rate_limit"
    if status in _OVERLOADED_STATUSES:
        return "overloaded"
    return None


def _body_detail(e: BaseException) -> str:
    r = getattr(e, "response", None)
    if r is None:
        return ""
    try:
        return f" — {r.text[:DETAIL_MAX]}"
    except Exception:  # noqa: BLE001 — detail is best-effort only
        return ""


def describe_failure(
    e: BaseException,
    *,
    provider: str,
    attempts: int,
    budget_s: float,
    reason: str | None,
    host: str,
    model: str,
) -> str:
    """The message that replaces `f"… request failed: {e}"`.

    THE point of this function: `str(e)` is empty for a mapped httpx timeout, so
    the class name and a synthesised description carry the meaning instead. It
    still contains the provider's own body when there is one, so the billing /
    rate-limit markers the failover classifier scans for are unaffected.
    """
    name = type(e).__name__
    msg = str(e).strip()
    if not msg:
        if reason == "timeout":
            msg = f"no response within the {budget_s:g}s budget"
        elif reason == "network":
            msg = "the connection failed before a response arrived"
        else:
            msg = "the transport reported no detail"
    plural = "" if attempts == 1 else "s"
    return (
        f"{provider} request failed after {attempts} attempt{plural} "
        f"[{reason or 'non-transient'}]: {name}: {msg}{_body_detail(e)} "
        f"(host {host}, model {model})"
    )


async def post_chat_completion(
    *,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    provider: str,
    prefix: str,
    model: str,
) -> dict[str, Any]:
    """POST one chat-completion and return the decoded JSON body.

    Raises `LLMTransportError` (with `.reason`) on any transport, HTTP-status or
    body-decode failure. Never raises a message-less error.

    The budget is a DEADLINE, not a per-attempt timeout: each attempt is given
    only the time still left, so N attempts can't take N × the configured wait.
    """
    budget = budget_seconds(prefix)
    attempts_allowed = max_attempts(prefix)
    connect_s = connect_timeout(prefix)
    deadline = time.monotonic() + budget
    host = urlparse(url).netloc or url
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    attempt = 0
    while True:
        attempt += 1
        remaining = max(0.001, deadline - time.monotonic())
        timeout = httpx.Timeout(remaining, connect=min(connect_s, remaining))
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            reason = classify_transport_error(e)
            left = deadline - time.monotonic()
            if (
                reason in RETRYABLE_REASONS
                and attempt < attempts_allowed
                and left >= MIN_RETRY_BUDGET_S
            ):
                log.warning(
                    "%s: %s on attempt %d/%d after %.1fs (%.1fs of budget left) — retrying: %s",
                    provider, reason, attempt, attempts_allowed,
                    time.monotonic() - started, left, type(e).__name__,
                )
                await asyncio.sleep(min(RETRY_BACKOFF_S * attempt, max(0.0, left - MIN_RETRY_BUDGET_S)))
                continue
            raise LLMTransportError(
                describe_failure(
                    e, provider=provider, attempts=attempt, budget_s=budget,
                    reason=reason, host=host, model=model,
                ),
                reason=reason,
            ) from e

        try:
            return resp.json()
        except ValueError as e:
            # A 200 with an undecodable body. Not transient — don't retry, but do
            # show what actually came back instead of a bare "invalid JSON".
            raise LLMTransportError(
                f"{provider} returned a non-JSON body (HTTP {resp.status_code}): "
                f"{resp.text[:DETAIL_MAX]!r} (host {host}, model {model})",
                reason=None,
            ) from e
