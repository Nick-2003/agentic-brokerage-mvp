"""W2 — Briefing generator (waitlist pivot).

Turns a W1 IBKR Flex snapshot + market context into a **WhatsApp-ready narrative
morning briefing** ("what moved / why / what it means"), written by Claude.

This is a SYSTEM-SIDE generator, NOT an agent tool. The scheduler (W5) calls it
per connected user and hands the text to the WhatsApp sender (W3). The LLM only
*writes* the brief; it never sends it (SECURITY threat 1 — no outbound-comms /
credential tools in the agent registry). So this module talks to Anthropic with
a single, tool-less `messages.create` call — deliberately NOT the `run_agent`
tool loop.

It reuses the chat product's `morning_brief` trust rules (numbers come from tool
data, P&L in the account's base currency, no hallucinated causes) but emits prose
instead of widget JSON — see `prompts/briefing_system.md`.

Mock-first, same discipline as `ibkr_flex.py` / `fmp_client.py`:
  • `USE_MOCK_BRIEFING=1` or no `ANTHROPIC_API_KEY` → deterministic template prose
    (offline, no API spend; powers the offline test + a keyless probe run).
  • real path failure → raise `BriefingError` (never a silent fall-through to mock).

The *holdings* snapshot has its own mock switch (`USE_MOCK_IBKR`, W1); the two are
independent, so you can generate a real LLM brief over the mock fixture, or a mock
brief over a live statement.

Numbers are computed HERE in Python (exact), then handed to the LLM as finished
facts to copy — the same "raw facts in, prose out" split that keeps `get_full_research`
honest. The LLM composes sentences; it never does arithmetic on the figures.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

import ibkr_flex
from news_context import fetch_macro_context, fetch_recent_news
from tools.market import get_company_news, get_macro_snapshot

log = logging.getLogger(__name__)

_PROMPT = Path(__file__).parent / "prompts" / "briefing_system.md"

# How many top movers (by |day P&L|) to spotlight + fetch news for.
_TOP_MOVERS = int(os.getenv("BRIEFING_TOP_MOVERS", "4"))
# WhatsApp soft length budget (Twilio caps a single message at 1600 chars).
_MAX_CHARS = int(os.getenv("BRIEFING_MAX_CHARS", "1500"))
# Headline freshness window (days), anchored to the statement's as_of date — only
# news at/after (as_of − this) is offered to the brief, so a stale headline can't
# be cited as a cause for today's move. See _news_since().
_NEWS_MAX_AGE_DAYS = int(os.getenv("BRIEFING_NEWS_MAX_AGE_DAYS", "2"))


class BriefingError(Exception):
    """Any real-path briefing-generation failure (LLM unreachable / empty)."""

    def __init__(self, message: str, code: str = "briefing_generation_failed") -> None:
        self.code = code
        super().__init__(message)


# --- mock gate / client --------------------------------------------------------

def briefing_mock_enabled() -> bool:
    """True iff the deterministic template path should be used instead of Claude.

    Forced by `USE_MOCK_BRIEFING=1`, or implied when there's no Anthropic key
    (so offline dev / a keyless probe still produces a brief).
    """
    if os.getenv("USE_MOCK_BRIEFING") == "1":
        return True
    return not (os.getenv("ANTHROPIC_API_KEY") or "").strip()


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


def _model() -> str:
    # Reuse the chat model by default; allow a cheaper narrative-only override.
    return os.getenv("BRIEFING_MODEL") or os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5")


# --- money formatting ----------------------------------------------------------

# Currency symbols for the few we expect; fall back to "<CCY> " prefix otherwise.
_CCY_SYMBOL = {"USD": "$", "HKD": "HK$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNH": "¥", "CNY": "¥"}


def _sym(ccy: str | None) -> str:
    if not ccy:
        return ""
    return _CCY_SYMBOL.get(ccy.upper(), f"{ccy.upper()} ")


def _money(v: float | None, ccy: str | None, *, signed: bool = False) -> str | None:
    """Human money string, e.g. `HK$248,750.40` or `+HK$1,770.28`. None passes through."""
    if v is None:
        return None
    sign = "+" if (signed and v >= 0) else ("-" if signed and v < 0 else "")
    return f"{sign}{_sym(ccy)}{abs(v):,.2f}"


def _pct(v: float | None, *, signed: bool = True) -> str | None:
    if v is None:
        return None
    sign = "+" if (signed and v >= 0) else ""
    return f"{sign}{v:.2f}%"


def _macro_indicators(macro: dict | None) -> list[dict]:
    """Normalise the macro context to a uniform `[{label, display}, ...]` list,
    whatever the source — so the prompt + mock render see ONE shape.

    - Real `fetch_macro_context` → already an `indicators` list (passed through).
    - Old `get_macro_snapshot` mock dict → a few `{label, display}` lines built
      from its scalar keys (so the deterministic demo still shows macro).
    - Empty / missing → `[]` (a live brief with no macro just omits the line).
    """
    if not macro:
        return []
    inds = macro.get("indicators")
    if isinstance(inds, list):
        return inds
    out: list[dict] = []

    def _push(label: str, val: Any, kind: str) -> None:
        if val is None:
            return
        if kind == "move":
            out.append({"label": label, "display": f"{label} {float(val):+.2f}%"})
        elif kind == "pct_level":
            out.append({"label": label, "display": f"{label} {float(val):.2f}%"})
        else:
            out.append({"label": label, "display": f"{label} {val}"})

    _push("S&P 500 futures", macro.get("sp_futures_pct"), "move")
    _push("Nasdaq futures", macro.get("nasdaq_futures_pct"), "move")
    _push("VIX", macro.get("vix"), "level")
    _push("US 10Y yield", macro.get("treasury_10y_yield_pct"), "pct_level")
    return out


# --- facts computation (exact numbers; LLM only writes prose around these) ------

def compute_brief_facts(snapshot: dict, market_context: dict | None = None) -> dict:
    """Reduce a W1 snapshot (+ market context) to a flat, finished facts block.

    All P&L / NAV figures are in the snapshot's `base_currency`. Each numeric has
    a `*_display` sibling pre-formatted with the right symbol + sign, so the LLM
    copies a string and can't mangle the currency.
    """
    ctx = market_context or {}
    base = snapshot.get("base_currency") or "USD"
    nav = snapshot.get("nav") or {}
    change = snapshot.get("change_in_nav") or {}
    perf = snapshot.get("performance") or {}

    nav_total = nav.get("total")
    nav_prev = change.get("starting")
    if nav_prev is None:
        nav_prev = nav.get("prev_total")
    # Overnight delta: prefer the authoritative ChangeInNAV ending-starting.
    # Round to cents — float subtraction of two large base-ccy figures otherwise
    # leaves a 1e-10 tail that would print as a bogus extra digit.
    if change.get("ending") is not None and change.get("starting") is not None:
        day_change = round(change["ending"] - change["starting"], 2)
    elif nav_total is not None and nav_prev is not None:
        day_change = round(nav_total - nav_prev, 2)
    else:
        day_change = None
    day_change_pct = (
        (day_change / nav_prev * 100) if (day_change is not None and nav_prev) else None
    )

    # Movers — base-ccy day P&L per holding, biggest absolute move first.
    movers: list[dict] = []
    for p in snapshot.get("positions") or []:
        dp = p.get("day_pnl")
        if dp is None:
            continue
        prev, close = p.get("prev_price"), p.get("close_price")
        chg_pct = ((close - prev) / prev * 100) if (prev and close is not None) else None
        movers.append(
            {
                "symbol": p.get("symbol"),
                "name": p.get("description") or p.get("symbol"),
                "day_pnl": dp,
                "day_pnl_display": _money(dp, base, signed=True),
                "change_pct": round(chg_pct, 2) if chg_pct is not None else None,
                "change_pct_display": _pct(chg_pct) if chg_pct is not None else None,
                "pct_of_nav": p.get("pct_of_nav"),
                "native_currency": p.get("currency"),
                "mark_price": p.get("mark_price"),
            }
        )
    movers.sort(key=lambda m: abs(m["day_pnl"]), reverse=True)
    top = movers[:_TOP_MOVERS]

    news = (ctx.get("news_by_ticker") or {})
    for m in top:  # attach this name's headlines (may be empty)
        m["headlines"] = news.get((m["symbol"] or "").upper(), [])

    return {
        "as_of": snapshot.get("as_of"),
        "account_id": snapshot.get("account_id"),
        "base_currency": base,
        "max_chars": _MAX_CHARS,
        "nav_total": nav_total,
        "nav_total_display": _money(nav_total, base),
        "day_change": day_change,
        "day_change_display": _money(day_change, base, signed=True),
        "day_change_pct": round(day_change_pct, 2) if day_change_pct is not None else None,
        "day_change_pct_display": _pct(day_change_pct) if day_change_pct is not None else None,
        "holdings_count": len(snapshot.get("positions") or []),
        "movers": top,
        "mtd": perf.get("mtd"),
        "mtd_display": _money(perf.get("mtd"), base, signed=True),
        "ytd": perf.get("ytd"),
        "ytd_display": _money(perf.get("ytd"), base, signed=True),
        "macro": _macro_indicators(ctx.get("macro")),
        "permalink": ctx.get("permalink"),  # W4 fills this; None for now
    }


# --- market context ------------------------------------------------------------

def _news_since(as_of: str | None) -> str:
    """ISO date cutoff for the headline recency cap: `(as_of − _NEWS_MAX_AGE_DAYS)`.

    Anchored to the statement's `as_of` (the day the brief is *about*), NOT
    wall-clock now — so replaying a brief days later doesn't drop the news that
    actually explains its moves, and tests are deterministic. Falls back to
    `now − N days` if `as_of` is missing/unparseable (never crash on a bad date).
    Returned as `YYYY-MM-DD`; the date-only string compares correctly against the
    ISO-8601 `ts` values in `fetch_recent_news`'s lexicographic `since` filter.
    """
    anchor: datetime | None = None
    if as_of:
        try:
            anchor = datetime.strptime(as_of[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            anchor = None
    if anchor is None:
        anchor = datetime.now(timezone.utc)
    return (anchor - timedelta(days=_NEWS_MAX_AGE_DAYS)).strftime("%Y-%m-%d")


async def gather_market_context(snapshot: dict) -> dict:
    """The "why it moved / what it means" layer: macro snapshot + headlines for
    the snapshot's biggest movers. Reuses the chat product's market tools.
    Best-effort — a failure here degrades the brief (no news / no macro) but
    never aborts it.

    TRUST GUARD: a *live* brief must never present mock context as real (trust #1
    "no number without a source" / #5 "no hallucinated data").
      • News (the "why it moved" layer) — for a LIVE snapshot we fetch REAL
        per-ticker headlines via `fetch_recent_news` (yfinance), so Claude grounds
        causes in actual reporting instead of inventing them. For a labelled mock
        DEMO (`snapshot.is_mock`) we use the deterministic `get_company_news` mock.
      • Macro (the "what it means" layer) — for a LIVE snapshot we fetch REAL
        indicators (index futures / VIX / 10Y / commodities) via
        `fetch_macro_context`; for a DEMO we use the `get_macro_snapshot` mock.
    """
    ctx: dict[str, Any] = {"macro": {}, "news_by_ticker": {}}
    allow_mock = bool(snapshot.get("is_mock"))

    def _real_enough(result: dict | None) -> bool:
        # Include the layer only if it's real, or if the whole brief is a demo.
        return bool(result) and (allow_mock or not result.get("is_mock"))

    # Macro: live → real indicators; demo → mock snapshot.
    try:
        macro = (
            await get_macro_snapshot({}, "system")
            if allow_mock
            else await fetch_macro_context()
        )
        if _real_enough(macro):
            ctx["macro"] = macro
    except Exception as e:  # noqa: BLE001 — context is best-effort
        log.info("briefing: macro snapshot unavailable: %s", e)

    movers = sorted(
        (p for p in (snapshot.get("positions") or []) if p.get("day_pnl") is not None),
        key=lambda p: abs(p["day_pnl"]),
        reverse=True,
    )
    tickers = [p["symbol"] for p in movers[:_TOP_MOVERS] if p.get("symbol")]
    if tickers:
        try:
            # Live → real yfinance headlines, capped to fresh news (anchored to
            # as_of) so a stale headline can't be cited as today's cause; demo →
            # deterministic mock tool (timestamps fresh-by-construction → no cap).
            res = (
                await get_company_news({"tickers": tickers, "limit": 2}, "system")
                if allow_mock
                else await fetch_recent_news(
                    tickers, limit=2, since=_news_since(snapshot.get("as_of"))
                )
            )
            if _real_enough(res):
                ctx["news_by_ticker"] = res.get("news_by_ticker", {})
        except Exception as e:  # noqa: BLE001
            log.info("briefing: news unavailable: %s", e)
    return ctx


# --- mock (template) rendering -------------------------------------------------

def _render_mock_briefing(facts: dict) -> str:
    """Deterministic prose brief from facts — no LLM. Powers offline tests and a
    keyless probe. Same numbers + currency the real path would copy."""
    arrow = "➖"
    if isinstance(facts.get("day_change"), (int, float)):
        arrow = "📈" if facts["day_change"] >= 0 else "📉"
    nav = facts.get("nav_total_display") or "n/a"
    lines = [
        f"{arrow} *Your IBKR book* is at *{nav}*"
        + (
            f", *{facts['day_change_display']} ({facts['day_change_pct_display']})* overnight."
            if facts.get("day_change_display")
            else " this morning."
        )
    ]
    movers = facts.get("movers") or []
    if movers:
        parts = []
        for m in movers[:3]:
            seg = f"*{m['symbol']}* {m['day_pnl_display']}"
            if m.get("change_pct_display"):
                seg += f" ({m['change_pct_display']})"
            hl = (m.get("headlines") or [])
            if hl:
                seg += f" — {hl[0]['headline']} ({hl[0]['source']})"
            parts.append(seg)
        lines.append("Movers: " + "; ".join(parts) + ".")
    else:
        lines.append("No per-position moves in today's statement.")
    macro = facts.get("macro") or []
    if macro:
        lines.append("Market: " + "; ".join(m["display"] for m in macro[:3]) + ".")
    tail = []
    if facts.get("mtd_display"):
        tail.append(f"MTD *{facts['mtd_display']}*")
    if facts.get("ytd_display"):
        tail.append(f"YTD *{facts['ytd_display']}*")
    if tail:
        lines.append(", ".join(tail) + ".")
    text = "\n\n".join(lines)
    return text[: facts.get("max_chars", _MAX_CHARS)]


# --- the generator -------------------------------------------------------------

def _facts_user_message(facts: dict) -> str:
    return (
        "Write today's WhatsApp briefing from these facts. All P&L is in "
        f"{facts.get('base_currency')}. Copy every number verbatim; output only the "
        "message body.\n\n<facts>\n"
        + json.dumps(facts, ensure_ascii=False, indent=2)
        + "\n</facts>"
    )


async def generate_briefing(snapshot: dict, market_context: dict | None = None) -> dict:
    """Snapshot (+ context) → a finished briefing dict.

    Returns:
        {text, is_mock, model, as_of, account_id, base_currency, generated_at,
         permalink, facts}

    `is_mock` is True if EITHER the snapshot was mock OR the text was rendered by
    the template path (no LLM). Raises `BriefingError` on a real-LLM failure.
    """
    facts = compute_brief_facts(snapshot, market_context)
    snap_mock = bool(snapshot.get("is_mock"))
    gen_mock = briefing_mock_enabled()

    if gen_mock:
        text = _render_mock_briefing(facts)
        model = "mock"
    else:
        system = _PROMPT.read_text()
        try:
            resp = await _get_client().messages.create(
                model=_model(),
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": _facts_user_message(facts)}],
            )
        except Exception as e:  # noqa: BLE001
            raise BriefingError(f"Claude call failed: {e}") from e
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        if not text:
            raise BriefingError("Claude returned an empty briefing", code="briefing_empty")
        model = _model()

    return {
        "text": text,
        "is_mock": snap_mock or gen_mock,
        "model": model,
        "as_of": facts.get("as_of"),
        "account_id": facts.get("account_id"),
        "base_currency": facts.get("base_currency"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "permalink": facts.get("permalink"),
        "facts": facts,
    }


async def build_briefing(token: str | None = None, query_id: str | None = None) -> dict:
    """End-to-end entry point W5 calls per connected user: fetch the Flex snapshot
    (mock-first), gather market context, generate the briefing. Creds are params
    so W4's per-user encrypted token store plugs in with no refactor.
    """
    snapshot = await ibkr_flex.get_portfolio_snapshot(token, query_id)
    context = await gather_market_context(snapshot)
    return await generate_briefing(snapshot, context)
