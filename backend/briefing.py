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

import deepseek_client  # 070 — DeepSeek fallback when the primary is usage-limited
import llm_limits  # 073 — shared output caps (stdlib-only; keeps the cron light)
import kimi_client  # 080 — Kimi as a selectable primary for the brief too
import openai_client  # 071 — OpenAI as a selectable primary for the brief too
import freshness  # 061 — shared freshness-note logic (was inline here, 052)
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
# Widened 2 → 3 (proposal 049): the brief is T+1 (the as_of session is already a
# day old by send time) and yfinance headlines are sparse for some names, so a
# 2-day window left real movers unexplained. 3 days still keeps stale news out.
_NEWS_MAX_AGE_DAYS = int(os.getenv("BRIEFING_NEWS_MAX_AGE_DAYS", "3"))
# Headlines fetched per mover (049: 2 → 3 so a relevant one is more likely to land).
_NEWS_PER_TICKER = int(os.getenv("BRIEFING_NEWS_PER_TICKER", "3"))
# Max executed trades to surface in the brief (050).
_MAX_TRADES = int(os.getenv("BRIEFING_MAX_TRADES", "6"))


class BriefingError(Exception):
    """Any real-path briefing-generation failure (LLM unreachable / empty)."""

    def __init__(self, message: str, code: str = "briefing_generation_failed") -> None:
        self.code = code
        super().__init__(message)


# --- mock gate / client --------------------------------------------------------

def briefing_mock_enabled() -> bool:
    """True iff the deterministic template path should be used instead of an LLM.

    Forced by `USE_MOCK_BRIEFING=1`, or implied when the ACTIVE rail has no key
    (so offline dev / a keyless probe still produces a brief).

    074 — this MUST key off the active rail, not always Anthropic. Pre-074 it
    checked `ANTHROPIC_API_KEY` unconditionally: with `LLM_RAIL=deepseek` (or
    `openai`) and Anthropic deprovisioned, every brief would silently fall to the
    mock template even though a real LLM was configured. That was a latent trap
    the moment 071 made a non-Anthropic rail selectable; 074 closes it.
    """
    if os.getenv("USE_MOCK_BRIEFING") == "1":
        return True
    rail = _brief_rail()
    if rail == "deepseek":
        return not deepseek_client.deepseek_available()
    if rail == "openai":
        return not openai_client.openai_available()
    if rail == "kimi":  # 080
        return not kimi_client.kimi_available()
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


# --- 070: DeepSeek fallback for the daily brief --------------------------------
# The brief is a single TOOL-LESS `messages.create` (no agent loop, no widget JSON,
# no images), so failover here is far simpler than the chat rail (069): re-issue the
# same system+facts prompt to DeepSeek and use its prose.
#
# Gate: `BRIEFING_FALLBACK_ENABLED` — defaults to whatever `LLM_FALLBACK_ENABLED`
# is, so one flag normally arms both rails. Set it to 0 to keep the chat fallback
# on while refusing to SEND a DeepSeek-written brief to users (external comms are a
# higher bar than an in-app answer — see the README's trust note).

# Same markers as agent._classify_agent_error; duplicated deliberately so the cron
# service never has to import the whole agent module (tool registry + Anthropic
# client) just to classify one error. DRY follow-up noted in the README.
_USAGE_LIMIT_MARKERS = ("credit balance", "billing", "quota", "insufficient", "payment")


def _brief_fallback_enabled() -> bool:
    raw = os.getenv("BRIEFING_FALLBACK_ENABLED")
    if raw is None:
        raw = os.getenv("LLM_FALLBACK_ENABLED", "0")
    return raw == "1"


# 071 — the brief honours the SAME `LLM_RAIL` select as chat. 070's whole lesson
# was that wiring only the agent loop leaves the WhatsApp/email brief dead (it
# builds its own client and never touches run_chat), so the rail select has to
# land in both places or the cron stays broken. Duplicated rather than imported
# for the same reason `_USAGE_LIMIT_MARKERS` is: the cron service must never have
# to import the whole agent module (tool registry + Anthropic client) to send a brief.
_VALID_RAILS = ("anthropic", "openai", "deepseek", "kimi")  # 074 DeepSeek · 080 Kimi


def _brief_rail() -> str:
    raw = (os.getenv("LLM_RAIL", "anthropic") or "anthropic").strip().lower()
    if raw not in _VALID_RAILS:
        log.warning("unknown LLM_RAIL=%r — falling back to 'anthropic'", raw)
        return "anthropic"
    return raw


def _is_usage_limit_error(e: Exception) -> bool:
    """True for the failures worth retrying on another provider: billing/quota,
    rate limit, overload. Auth / bad-request are config bugs — they must stay loud
    and would fail on DeepSeek too."""
    text = str(e).lower()
    if any(m in text for m in _USAGE_LIMIT_MARKERS):
        return True
    if "rate limit" in text or "429" in text:
        return True
    status = getattr(e, "status_code", None)
    return status in (503, 529) or "overloaded" in text


# --- money formatting ----------------------------------------------------------

# Currency symbols for the few we expect; fall back to "<CCY> " prefix otherwise.
_CCY_SYMBOL = {"USD": "$", "HKD": "HK$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNH": "¥", "CNY": "¥"}


def _sym(ccy: str | None) -> str:
    if not ccy:
        return ""
    return _CCY_SYMBOL.get(ccy.upper(), f"{ccy.upper()} ")


def _yf_symbol(symbol: str | None, currency: str | None = None) -> str:
    """Best-effort map an IBKR Flex symbol to a yfinance ticker for news lookup
    (proposal 049). The briefing passes the Flex `symbol` straight to yfinance;
    US symbols match (AAPL→AAPL), but a Hong Kong numeric code under HKD does not
    (Flex `700`/`0700` → yfinance `0700.HK`), so those movers silently got no news.

    Heuristic, covering the common cases:
      • already-suffixed (contains '.') → unchanged (e.g. `1398.HK`).
      • HKD + all-digits → zero-pad to 4 + `.HK` (e.g. `700` → `0700.HK`).
      • otherwise pass through (US and anything we don't special-case).
    """
    s = (symbol or "").strip().upper()
    if not s or "." in s:
        return s
    if (currency or "").upper() == "HKD" and s.isdigit():
        return f"{int(s):04d}.HK"
    return s


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


# 061 — the freshness-note logic moved to the shared `freshness` module so the
# in-app morning_brief (tools/portfolio.py) shows the SAME note. These thin
# wrappers keep 052's call sites + tests (which reference `_freshness_note`,
# `_briefing_tz_name`) working unchanged.
def _briefing_tz_name() -> str:
    return freshness.briefing_tz_name()


def _tz_or_utc(tz_name: str):
    return freshness.tz_or_utc(tz_name)


def _freshness_note(as_of: str | None, now: datetime, tz_name: str | None = None) -> str | None:
    return freshness.freshness_note(as_of, now, tz_name)


def _qty_str(q: float | None) -> str:
    """Quantity without a trailing `.0` for whole shares (10, not 10.0); abs value
    (the side word carries direction)."""
    if q is None:
        return "?"
    q = abs(q)
    return str(int(q)) if float(q).is_integer() else f"{q:g}"


def _format_trades(snapshot: dict) -> list[dict]:
    """Pre-format executed trades (050) into copy-ready display strings, e.g.
    `Bought 20 NVDA @ $1,175.30`. Price is in the trade's NATIVE currency (the
    market it traded in); the side word ('Bought'/'Sold') carries direction, so
    quantities are shown absolute. Capped at `_MAX_TRADES`."""
    out: list[dict] = []
    for t in (snapshot.get("trades") or [])[:_MAX_TRADES]:
        side = (t.get("side") or "").upper()
        verb = "Bought" if side == "BUY" else ("Sold" if side == "SELL" else (side.title() or "Traded"))
        sym = t.get("symbol") or "?"
        price = _money(t.get("price"), t.get("currency"))
        disp = f"{verb} {_qty_str(t.get('quantity'))} {sym}"
        if price:
            disp += f" @ {price}"
        out.append({"symbol": sym, "side": side, "display": disp})
    return out


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

def compute_brief_facts(
    snapshot: dict, market_context: dict | None = None, now: datetime | None = None
) -> dict:
    """Reduce a W1 snapshot (+ market context) to a flat, finished facts block.

    All P&L / NAV figures are in the snapshot's `base_currency`. Each numeric has
    a `*_display` sibling pre-formatted with the right symbol + sign, so the LLM
    copies a string and can't mangle the currency.

    `now` (052) is the generation instant used for the freshness note's local/GMT
    times; defaults to now (UTC). Pass it for deterministic tests / to match the
    brief's `generated_at`.
    """
    if now is None:
        now = datetime.now(timezone.utc)
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
        "trades": _format_trades(snapshot),  # 050 — executed trades (empty unless Flex Trades on)
        "data_freshness_note": _freshness_note(snapshot.get("as_of"), now),  # 052 — T+1/EOD + local/GMT
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
    top = [p for p in movers[:_TOP_MOVERS] if p.get("symbol")]
    tickers = [p["symbol"] for p in top]
    if tickers:
        try:
            if allow_mock:
                # Demo → deterministic mock tool, keyed by the original symbols
                # (timestamps fresh-by-construction → no recency cap).
                res = await get_company_news(
                    {"tickers": tickers, "limit": _NEWS_PER_TICKER}, "system"
                )
                if _real_enough(res):
                    ctx["news_by_ticker"] = res.get("news_by_ticker", {})
            else:
                # Live → real yfinance headlines. Map each Flex symbol to its
                # yfinance ticker (049) so non-US/HK movers actually get news,
                # fetch by the yf symbol, then RE-KEY the result back to the
                # original Flex symbol so compute_brief_facts (which looks up by
                # the snapshot symbol) attaches headlines to the right mover.
                # Recency-capped (anchored to as_of) so stale news can't be cited.
                yf_to_orig = {
                    _yf_symbol(p["symbol"], p.get("currency")).upper(): p["symbol"].upper()
                    for p in top
                }
                res = await fetch_recent_news(
                    list(yf_to_orig.keys()),
                    limit=_NEWS_PER_TICKER,
                    since=_news_since(snapshot.get("as_of")),
                    # 060 — briefing is the ONLY caller that supplements yfinance
                    # with Alpha Vantage (best-effort; yfinance-only if AV off/capped).
                    use_av=True,
                )
                if _real_enough(res):
                    nb = res.get("news_by_ticker", {})
                    ctx["news_by_ticker"] = {
                        yf_to_orig.get(k.upper(), k): v for k, v in nb.items()
                    }
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
    trades = facts.get("trades") or []
    if trades:
        lines.append("Executed: " + "; ".join(t["display"] for t in trades) + ".")
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
    if facts.get("data_freshness_note"):  # 052 — T+1/EOD disclosure (always last)
        lines.append(f"_{facts['data_freshness_note']}_")
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


def _chart_data(snapshot: dict) -> dict | None:
    """Per-holding day P&L for the web brief's bar chart (051).

    Every holding with a day P&L (not just the top movers), each with the
    base-ccy numeric `day_pnl` (drives the bar width on the page) and a
    pre-formatted `day_pnl_display`. Sorted gainers-first. Returns None when no
    holding has a day P&L (the page then renders no chart). Stored with the
    published brief; the WhatsApp/email text is unaffected.
    """
    base = snapshot.get("base_currency") or "USD"
    bars: list[dict] = []
    for p in snapshot.get("positions") or []:
        dp = p.get("day_pnl")
        if dp is None:
            continue
        bars.append({
            "symbol": p.get("symbol"),
            "day_pnl": round(dp, 2),
            "day_pnl_display": _money(dp, base, signed=True),
        })
    if not bars:
        return None
    bars.sort(key=lambda b: b["day_pnl"], reverse=True)  # gainers first
    return {"kind": "day_pnl", "base_currency": base, "bars": bars}


async def generate_briefing(snapshot: dict, market_context: dict | None = None) -> dict:
    """Snapshot (+ context) → a finished briefing dict.

    Returns:
        {text, is_mock, model, as_of, account_id, base_currency, generated_at,
         permalink, facts}

    `is_mock` is True if EITHER the snapshot was mock OR the text was rendered by
    the template path (no LLM). Raises `BriefingError` on a real-LLM failure.
    """
    now = datetime.now(timezone.utc)  # 052 — one instant for the freshness note + generated_at
    facts = compute_brief_facts(snapshot, market_context, now=now)
    snap_mock = bool(snapshot.get("is_mock"))
    gen_mock = briefing_mock_enabled()
    fallback_used = False  # 070 — set when DeepSeek wrote this brief

    if gen_mock:
        text = _render_mock_briefing(facts)
        model = "mock"
    else:
        system = _PROMPT.read_text()
        user_msg = _facts_user_message(facts)
        rail = _brief_rail()  # 071 — same select as chat
        try:
            if rail == "openai":
                # 071 — tool-less single completion, exactly like the Anthropic
                # path; no widget JSON, no images, so nothing else changes.
                oa = await openai_client.complete(
                    system, [{"role": "user", "content": user_msg}],
                    max_tokens=llm_limits.brief_max_output_tokens("openai"),  # 073
                )
                text = (oa.get("text") or "").strip()
                model = openai_client.model()
            elif rail == "kimi":
                # 080 — Kimi as the chosen primary. Same tool-less shape. No
                # "written by" disclosure: like the 074 DeepSeek-primary branch,
                # a deliberately chosen model is NOT a degradation, so the
                # "the usual model was unavailable" wording would be false.
                km = await kimi_client.complete(
                    system, [{"role": "user", "content": user_msg}],
                    max_tokens=llm_limits.brief_max_output_tokens("kimi"),
                )
                text = (km.get("text") or "").strip()
                model = kimi_client.model()
            elif rail == "deepseek":
                # 074 — DeepSeek AS the chosen primary (not a fallback). Same
                # tool-less shape. A LIGHT attribution note is appended below;
                # the heavier "the usual model was unavailable" fallback
                # disclosure would be FALSE here — this is a deliberate choice.
                ds = await deepseek_client.complete(
                    system, [{"role": "user", "content": user_msg}],
                    max_tokens=llm_limits.brief_max_output_tokens("deepseek"),
                )
                text = (ds.get("text") or "").strip()
                model = deepseek_client.deepseek_model()
                if text:
                    text = f"{text}\n\n_Written by {model}._"
            else:
                resp = await _get_client().messages.create(
                    model=_model(),
                    max_tokens=llm_limits.brief_max_output_tokens("anthropic"),  # 073
                    system=system,
                    messages=[{"role": "user", "content": user_msg}],
                )
                text = "".join(
                    b.text for b in resp.content if getattr(b, "type", None) == "text"
                ).strip()
                model = _model()
        except Exception as e:  # noqa: BLE001
            # 070 — the primary is usage-limited: re-issue the SAME prompt to
            # DeepSeek rather than skipping the brief entirely. Any other error
            # (auth, bad request) still fails loudly, as before.
            # 074 — when DeepSeek IS the primary, there is nothing to fall back
            # to (it's the last resort); a failure raises directly rather than
            # pointlessly re-issuing the same doomed call to the same provider.
            if not (
                rail != "deepseek"
                and _brief_fallback_enabled()
                and deepseek_client.deepseek_available()
                and _is_usage_limit_error(e)
            ):
                raise BriefingError(f"{rail} call failed: {e}") from e
            log.warning("briefing failing over from %s to DeepSeek: %s", rail, e)
            try:
                ds = await deepseek_client.complete(
                    system, [{"role": "user", "content": user_msg}],
                    max_tokens=llm_limits.brief_max_output_tokens("deepseek"),  # 073
                )
            except Exception as de:  # noqa: BLE001 — fallback is last resort
                raise BriefingError(
                    f"{rail} call failed ({e}); DeepSeek fallback also failed ({de})"
                ) from de
            text = (ds.get("text") or "").strip()
            model = deepseek_client.deepseek_model()
            fallback_used = True
            if text:
                # Say so IN the delivered brief. The recipient gets this over
                # WhatsApp/email and cannot inspect logs — they deserve to know a
                # different model wrote it (same honesty rule as the "(mocked)"
                # source pills and the freshness note).
                text = (
                    f"{text}\n\n_Written by {model} — the usual model was "
                    "unavailable. Figures are unchanged; wording may differ._"
                )
        if not text:
            raise BriefingError("LLM returned an empty briefing", code="briefing_empty")

    return {
        "text": text,
        "is_mock": snap_mock or gen_mock,
        "model": model,
        # 070 — True when the DeepSeek fallback wrote this brief (the delivered
        # text says so too). Additive: existing consumers ignore it.
        "fallback": fallback_used,
        "as_of": facts.get("as_of"),
        "account_id": facts.get("account_id"),
        "base_currency": facts.get("base_currency"),
        "generated_at": now.isoformat(),
        "permalink": facts.get("permalink"),
        "facts": facts,
        # 051 — per-holding day P&L for the web brief's bar chart (stored with
        # the published brief; the WhatsApp/email text is unaffected). None when
        # no holding has a day P&L.
        "chart_data": _chart_data(snapshot),
    }


async def build_briefing(token: str | None = None, query_id: str | None = None) -> dict:
    """End-to-end entry point W5 calls per connected user: fetch the Flex snapshot
    (mock-first), gather market context, generate the briefing. Creds are params
    so W4's per-user encrypted token store plugs in with no refactor.
    """
    snapshot = await ibkr_flex.get_portfolio_snapshot(token, query_id)
    context = await gather_market_context(snapshot)
    return await generate_briefing(snapshot, context)
