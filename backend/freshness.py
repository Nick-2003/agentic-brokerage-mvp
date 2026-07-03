"""Shared data-freshness note (061).

Proposal 052 introduced the one-line "figures are end-of-day / generated at" note
for the WhatsApp/email brief, living privately in `briefing.py`. 061 surfaces the
SAME note in the in-app `morning_brief` widget (the chat product shows the same
IBKR Flex book, so it needs the same T+1 disclosure). To keep one source of the
wording, the note logic moves here; `briefing.py` and `tools/portfolio.py` both
call it.

IBKR Flex is end-of-day (T+1): a statement reflects the CLOSE of the `as_of` US
session, not live/intraday prices. The note discloses that session date plus the
generation instant in BOTH the configured local timezone (`BRIEFING_TZ`) and GMT,
so a reader (e.g. in GMT+8) can judge recency without mental arithmetic.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def briefing_tz_name() -> str:
    """Configured display timezone for the freshness note (052). Defaults to the
    operator's locale; read per-call so env changes/tests take effect without reimport."""
    return os.getenv("BRIEFING_TZ", "Asia/Hong_Kong")


def tz_or_utc(tz_name: str):
    """ZoneInfo for `tz_name`, falling back to UTC if the zone/tzdata is unavailable
    (never crash on a bad BRIEFING_TZ)."""
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — bad zone name / missing tzdata
        return timezone.utc


def freshness_note(as_of: str | None, now: datetime, tz_name: str | None = None) -> str | None:
    """One-line data-freshness disclosure (052/061). IBKR Flex is end-of-day (T+1):
    the statement reflects the close of the `as_of` US session, not live/intraday.

    Shows BOTH the configured local timezone AND GMT:
      • the session date is the US trading day (`as_of`), labelled plainly;
      • the generation instant (`now`) is rendered in the local tz and in GMT.
    `tz_name` overrides the env (for tests). None as_of → None.
    """
    if not as_of:
        return None
    tz_name = tz_name or briefing_tz_name()
    try:
        sess_str = datetime.strptime(as_of[:10], "%Y-%m-%d").strftime("%a %d %b %Y")
    except (TypeError, ValueError):
        sess_str = as_of
    local = now.astimezone(tz_or_utc(tz_name))
    local_abbr = local.tzname() or tz_name
    gen_local = local.strftime("%d %b %H:%M")
    gen_gmt = now.astimezone(timezone.utc).strftime("%d %b %H:%M")
    return (
        f"Figures are end-of-day — the close of the US session on {sess_str} "
        f"(IBKR statement data, not live/intraday). "
        f"Generated {gen_local} {local_abbr} / {gen_gmt} GMT."
    )
