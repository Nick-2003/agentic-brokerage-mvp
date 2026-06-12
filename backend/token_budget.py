"""P5 — per-user daily LLM token budget for /api/chat.

A soft *cost* guard that complements W6.6's per-IP request rate limit: that bounds
request *frequency*; this bounds a single user's *token spend* per UTC day. The
chat handler checks `over_budget(user_id)` BEFORE running the agent (so an
already-capped user doesn't spend more) and calls `record(...)` with the turn's
token usage AFTER (surfaced via the agent's `done` event).

**In-memory + single-replica** — same posture as the W6.6 rate limiter (Railway
runs one replica; counters live in this process). Resets on restart, which is
acceptable for a soft guard. Upgrade path if you scale out or need durability:
back `_usage` with Supabase (a `chat_token_usage(user_id, day, tokens)` table via
the service key) — the `over_budget`/`record` surface stays the same.

**Fail-open / opt-in:** disabled unless `CHAT_DAILY_TOKEN_BUDGET` is a positive
int; `over_budget` never blocks on an internal error. A budget check must never
take down a legit turn.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# user_id -> (utc_day_iso, tokens_used_today). Lazily reset when the day rolls.
_usage: dict[str, tuple[str, int]] = {}
# Keep the dict from growing unbounded; sweep stale (not-today) entries when it
# crosses this many keys (trivial at 5–10 users, a backstop at scale).
_SWEEP_AT = 5000


def _budget() -> int:
    """Today's per-user token cap, or 0 (disabled). Read live so it's togglable."""
    try:
        return int(os.getenv("CHAT_DAILY_TOKEN_BUDGET", "0"))
    except ValueError:
        return 0


def token_budget_enabled() -> bool:
    return _budget() > 0


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def over_budget(user_id: str) -> bool:
    """True iff this user has already reached today's token cap. Fail-OPEN: returns
    False when disabled, for an empty user, or on any unexpected error."""
    if not user_id or not token_budget_enabled():
        return False
    try:
        day, used = _usage.get(user_id, ("", 0))
        if day != _today():  # no record today (or a stale prior-day one) → not over
            return False
        return used >= _budget()
    except Exception as e:  # noqa: BLE001 — a guard must never break the turn
        log.warning("token-budget check failed for %s (allowing): %s", user_id, e)
        return False


def record(user_id: str, input_tokens: int, output_tokens: int) -> None:
    """Add this turn's tokens to the user's daily total. No-op when disabled / empty
    / zero-delta; best-effort (never raises)."""
    if not user_id or not token_budget_enabled():
        return
    try:
        delta = max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0))
        if delta <= 0:
            return
        today = _today()
        day, used = _usage.get(user_id, (today, 0))
        if day != today:  # new day → start fresh
            used = 0
        _usage[user_id] = (today, used + delta)
        if len(_usage) > _SWEEP_AT:
            _sweep(today)
    except Exception as e:  # noqa: BLE001
        log.warning("token-budget record failed for %s: %s", user_id, e)


def _sweep(today: str) -> None:
    """Drop entries from previous days (memory hygiene)."""
    for k in [k for k, (d, _) in _usage.items() if d != today]:
        _usage.pop(k, None)
