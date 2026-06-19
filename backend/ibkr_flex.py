"""IBKR Flex Web Service client (W1) — read-only holdings/NAV for the waitlist briefing.

Read-only (no trading). The Flex Web Service returns an XML *report* via a 2-step flow:
  1. SendRequest(token, queryId) -> {ReferenceCode, Url}        (Status=Fail -> raise)
  2. GetStatement(Url, token, ReferenceCode) -> FlexQueryResponse XML
     (Status=Warn ErrorCode=1019 "generation in progress" -> poll w/ backoff)

Mock-first (same discipline as fmp_client.py): USE_MOCK_IBKR=1 or no creds -> parse the
bundled backend/data/mock_flex_statement.xml. On real-path failure -> raise IBKRFlexError
(never a silent fall-through to mock).

Credentials are PARAMS on the core fns (forward-compatible with the per-user token store
in W4); the env vars + ibkr_flex_enabled() are the dev/probe default. NOT an agent tool —
the briefing is a system job (SECURITY threat 1: no credential/outbound tools in the
agent registry).
"""
from __future__ import annotations

import asyncio
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

FLEX_SENDREQUEST_URL = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
)
_GETSTATEMENT_FALLBACK = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
)
_VERSION = "3"
_TIMEOUT = float(os.getenv("IBKR_FLEX_TIMEOUT_S", "30"))
# GetStatement can return 1019 "in progress"; poll with backoff (1 try + 5 retries ≈ 31s).
_POLL_BACKOFF_S = [2, 3, 5, 8, 13]
_STMT_IN_PROGRESS = "1019"
_INVALID_TOKEN_CODES = {"1003", "1015", "1016", "1017"}  # token bad/expired/inactive

_FIXTURE = Path(__file__).parent / "data" / "mock_flex_statement.xml"


class IBKRFlexError(Exception):
    """Any Flex fetch/parse failure. Surfaced to callers via `code`."""

    def __init__(self, message: str, code: str = "ibkr_flex_fetch_failed") -> None:
        self.code = code
        super().__init__(message)


def ibkr_flex_enabled() -> bool:
    """True iff the real Flex path should be used (env creds present, mock not forced).

    Dev/probe gate; the per-user product path (W4) passes creds explicitly instead.
    """
    if os.getenv("USE_MOCK_IBKR") == "1":
        return False
    return _creds_from(None, None) is not None


def _creds_from(token: str | None, query_id: str | None) -> tuple[str, str] | None:
    tok = (token or os.getenv("IBKR_FLEX_TOKEN", "")).strip()
    qid = (query_id or os.getenv("IBKR_FLEX_QUERY_ID", "")).strip()
    if not tok or not qid or tok.endswith("REPLACE") or qid.endswith("REPLACE"):
        return None
    return tok, qid


# --- XML helpers (defensive: IBKR tag/attr spellings vary by version) ----------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # strip any namespace


def _findall(root: ET.Element, tag: str) -> list[ET.Element]:
    return [e for e in root.iter() if _local(e.tag) == tag]


def _find(root: ET.Element, tag: str) -> ET.Element | None:
    got = _findall(root, tag)
    return got[0] if got else None


def _child_text(el: ET.Element, tag: str) -> str | None:
    c = _find(el, tag)
    return (c.text or "").strip() if c is not None and c.text else None


def _attr(el: ET.Element, *names: str) -> str | None:
    for n in names:
        v = el.get(n)
        if v not in (None, ""):
            return v
    return None


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _anum(el: ET.Element, *names: str) -> float | None:
    return _num(_attr(el, *names))


# --- HTTP (2-step) -------------------------------------------------------------

async def _http_get(url: str, params: dict[str, str]) -> str:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, params=params)
    except httpx.ConnectError as e:
        # DNS / TCP failure (e.g. the returned <Url> host doesn't resolve) —
        # tag it so the caller can retry on a different host.
        raise IBKRFlexError(f"Flex connect error ({url}): {e}",
                            code="ibkr_flex_connect_error") from e
    except httpx.HTTPError as e:
        raise IBKRFlexError(f"Flex request error: {e}") from e
    if resp.status_code != 200:
        raise IBKRFlexError(f"Flex HTTP {resp.status_code}: {resp.text[:160]}")
    return resp.text


def _candidate_get_urls(returned_url: str) -> list[str]:
    """GetStatement URLs to try, in order. IBKR's SendRequest sometimes returns a
    <Url> on a host that doesn't resolve (observed: `gdcdyn` returned while the
    `ndcdyn` SendRequest host works), so we fall back to GetStatement on the SAME
    host/service as SendRequest (confirmed reachable)."""
    out: list[str] = []
    for u in (returned_url, _GETSTATEMENT_FALLBACK):
        if u and u not in out:
            out.append(u)
    return out


def _control(xml_text: str) -> ET.Element | None:
    """Return the <FlexStatementResponse> control elem (warn/fail/success-pointer),
    or None when the body is the actual <FlexQueryResponse> statement."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise IBKRFlexError(f"Flex response not XML: {e}", code="ibkr_flex_parse_failed") from e
    return root if _local(root.tag) == "FlexStatementResponse" else None


async def _send_request(token: str, query_id: str) -> tuple[str, str]:
    ctrl = _control(
        await _http_get(FLEX_SENDREQUEST_URL, {"t": token, "q": query_id, "v": _VERSION})
    )
    if ctrl is None:
        raise IBKRFlexError("SendRequest returned a statement instead of a reference code")
    if (_child_text(ctrl, "Status") or "").lower() == "success":
        ref = _child_text(ctrl, "ReferenceCode")
        if not ref:
            raise IBKRFlexError("SendRequest success but no ReferenceCode")
        return ref, (_child_text(ctrl, "Url") or _GETSTATEMENT_FALLBACK)
    code = _child_text(ctrl, "ErrorCode") or ""
    msg = _child_text(ctrl, "ErrorMessage") or "unknown error"
    err = "ibkr_flex_invalid_token" if code in _INVALID_TOKEN_CODES else "ibkr_flex_fetch_failed"
    raise IBKRFlexError(f"SendRequest failed [{code}]: {msg}", code=err)


async def _get_statement(get_url: str, token: str, reference_code: str) -> str:
    candidates = _candidate_get_urls(get_url)
    url, host_idx, polls = candidates[0], 0, 0
    params = {"t": token, "q": reference_code, "v": _VERSION}
    while True:
        try:
            xml_text = await _http_get(url, params)
        except IBKRFlexError as e:
            # Returned host unreachable (DNS/connect) → try the next candidate
            # host once (doesn't consume the poll budget).
            if e.code == "ibkr_flex_connect_error" and host_idx + 1 < len(candidates):
                host_idx += 1
                log.info("GetStatement host unreachable (%s); falling back to %s",
                         e, candidates[host_idx])
                url = candidates[host_idx]
                continue
            raise
        ctrl = _control(xml_text)
        if ctrl is None:
            return xml_text  # the FlexQueryResponse statement
        code = _child_text(ctrl, "ErrorCode") or ""
        msg = _child_text(ctrl, "ErrorMessage") or ""
        if code == _STMT_IN_PROGRESS:
            if polls >= len(_POLL_BACKOFF_S):
                raise IBKRFlexError("Flex statement not ready after polling",
                                    code="ibkr_flex_statement_timeout")
            delay = _POLL_BACKOFF_S[polls]
            log.info("Flex statement %s in progress; retry in %ss (%d/%d)",
                     reference_code, delay, polls + 1, len(_POLL_BACKOFF_S))
            await asyncio.sleep(delay)
            polls += 1
            continue
        raise IBKRFlexError(f"GetStatement failed [{code}]: {msg}")


# --- Parse → normalized snapshot (the W2 contract) -----------------------------

def parse_flex_statement(xml_text: str) -> dict:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise IBKRFlexError(f"statement not XML: {e}", code="ibkr_flex_parse_failed") from e
    stmt = _find(root, "FlexStatement")
    if stmt is None:
        raise IBKRFlexError("no <FlexStatement> in response", code="ibkr_flex_parse_failed")

    as_of = _attr(stmt, "toDate", "reportDate", "fromDate")

    # Open Positions = the user's current holdings. Each symbol appears as a
    # `levelOfDetail="SUMMARY"` row AND one or more `"LOT"` rows; the LOT rows
    # duplicate the values but blank out percentOfNAV — so keep SUMMARY only.
    positions: dict[str, dict] = {}
    for op in _findall(stmt, "OpenPosition"):
        if _attr(op, "levelOfDetail") == "LOT":
            continue
        sym = _attr(op, "symbol")
        if not sym:
            continue
        positions[sym] = {
            "symbol": sym,
            "description": _attr(op, "description"),
            "currency": _attr(op, "currency"),
            "asset_class": _attr(op, "assetCategory"),
            "quantity": _anum(op, "position"),
            "mark_price": _anum(op, "markPrice"),
            "position_value": _anum(op, "positionValue"),            # native ccy
            "position_value_base": _anum(op, "positionValueInBase"),  # base ccy (NAV ccy)
            "fx_rate_to_base": _anum(op, "fxRateToBase"),
            "cost_basis_price": _anum(op, "costBasisPrice"),
            "unrealized_pnl": _anum(op, "fifoPnlUnrealized", "unrealizedPnl"),
            "pct_of_nav": _anum(op, "percentOfNAV"),
        }

    # MTM Performance Summary — per-position daily P&L (the "what moved"), in
    # base ccy. The real daily-MTM attr is `total` (transactionMtm + priorOpenMtm);
    # `mtmPnl` is a fallback for other Flex versions. ENRICH existing holdings
    # only — the MTM section also lists symbols sold today + cash rows (not holdings).
    for m in _findall(stmt, "MTMPerformanceSummaryUnderlying"):
        sym = _attr(m, "symbol")
        if not sym or sym not in positions:
            continue
        positions[sym]["day_pnl"] = _anum(m, "total", "totalWithAccruals", "mtmPnl")
        positions[sym]["prev_price"] = _anum(m, "prevClosePrice", "priorClosePrice")
        positions[sym]["close_price"] = _anum(m, "closePrice")
    # Financial Instrument Information — fill any missing name / asset class.
    for s in _findall(stmt, "SecurityInfo"):
        sym = _attr(s, "symbol")
        if sym in positions:
            if not positions[sym].get("description"):
                positions[sym]["description"] = _attr(s, "description")
            if not positions[sym].get("asset_class"):
                positions[sym]["asset_class"] = _attr(s, "assetCategory")

    # NAV (Net Asset Value in Base). There can be TWO EquitySummary rows (prior
    # + current report date) — pick the one matching the statement's toDate
    # (as_of), else the last (latest). (NB: a childless ET element is falsy, so
    # select with `is None`/explicit matching, never `a or b`.)
    eq_rows = _findall(stmt, "EquitySummaryByReportDateInBase")
    eq = None
    if eq_rows:
        eq = next((r for r in eq_rows if _attr(r, "reportDate") == as_of), None) or eq_rows[-1]
    elif _find(stmt, "EquitySummaryInBase") is not None:
        eq = _find(stmt, "EquitySummaryInBase")
    nav: dict[str, Any] = {}
    if eq is not None:
        nav = {
            "total": _anum(eq, "total"),
            "cash": _anum(eq, "cash"),
            "stock": _anum(eq, "stock"),
            "bonds": _anum(eq, "bonds"),
        }
    # Change in NAV — authoritative for the overnight delta (its endingValue is
    # today's total, startingValue is the prior close).
    change: dict[str, Any] = {}
    cin = _find(stmt, "ChangeInNAV")
    if cin is not None:
        change = {
            "starting": _anum(cin, "startingValue", "fromValue"),
            "ending": _anum(cin, "endingValue", "toValue"),
            "mtm": _anum(cin, "mtm"),
            "realized": _anum(cin, "realized"),
            "dividends": _anum(cin, "dividends"),
        }
        if change.get("ending") is not None:
            nav["total"] = change["ending"]
        nav["prev_total"] = change.get("starting")

    # Base currency = the currency of the NAV/ChangeInNAV section (e.g. HKD),
    # NOT a position's native currency (positions can be in USD while the
    # account base is HKD).
    base_ccy = (
        (_attr(eq, "currency") if eq is not None else None)
        or (_attr(cin, "currency") if cin is not None else None)
        or _attr(stmt, "currency")
        or next((p.get("currency") for p in positions.values() if p.get("currency")), None)
        or "USD"
    )

    # Performance — read the account TOTAL rows (the underlying with an empty
    # `symbol`), not a per-symbol row. MTD/YTD live in MTDYTDPerformanceSummary;
    # realized/unrealized totals in FIFOPerformanceSummary.
    perf: dict[str, Any] = {}
    mtot = next((r for r in _findall(stmt, "MTDYTDPerformanceSummaryUnderlying")
                 if not _attr(r, "symbol")), None)
    if mtot is not None:
        perf["mtd"] = _anum(mtot, "mtmMTD", "mtm", "mtd")
        perf["ytd"] = _anum(mtot, "mtmYTD", "ytd")
        perf["realized_mtd"] = _anum(mtot, "realizedPnlMTD")
        perf["realized_ytd"] = _anum(mtot, "realizedPnlYTD")
    ftot = next((r for r in _findall(stmt, "FIFOPerformanceSummaryUnderlying")
                 if not _attr(r, "symbol")), None)
    if ftot is not None:
        perf["realized"] = _anum(ftot, "totalRealizedPnl", "totalRealized", "realized")
        perf["unrealized"] = _anum(ftot, "totalUnrealizedPnl", "totalUnrealized", "unrealized")

    # Currency rates (Include Currency Rates = Yes)
    rates: dict[str, float] = {}
    for cr in _findall(stmt, "ConversionRate"):
        ccy, rate = _attr(cr, "fromCurrency"), _anum(cr, "rate")
        if ccy and rate is not None:
            rates[ccy] = rate

    # Executed trades (proposal 050) — "did my orders go through". Present only
    # when the Flex Query includes the Trades section (operator-configured); the
    # list is simply empty otherwise (so this is harmless until they enable it).
    # Skip lot-level rows (CLOSED_LOT/LOT duplicate a fill into tax lots); keep
    # one row per execution/order. `buySell` is BUY/SELL; price is in the trade's
    # native currency. The Flex period (LastBusinessDay) already scopes these to
    # the statement day, so we don't date-filter here.
    trades: list[dict[str, Any]] = []
    for t in _findall(stmt, "Trade"):
        lod = (_attr(t, "levelOfDetail") or "").upper()
        if lod in {"CLOSED_LOT", "LOT"}:
            continue
        sym = _attr(t, "symbol")
        if not sym:
            continue
        trades.append({
            "symbol": sym,
            "description": _attr(t, "description"),
            "side": (_attr(t, "buySell", "buy_sell") or "").upper(),  # BUY / SELL
            "quantity": _anum(t, "quantity"),
            "price": _anum(t, "tradePrice", "price"),
            "currency": _attr(t, "currency"),
            "trade_date": _attr(t, "tradeDate", "reportDate", "dateTime"),
            "proceeds": _anum(t, "proceeds"),
            "realized_pnl": _anum(t, "fifoPnlRealized", "realizedPnl"),
            "asset_class": _attr(t, "assetCategory"),
        })

    # Make silent shape-mismatches audible (the 022 lesson — guides the real-probe fixups).
    if not positions:
        log.info("Flex parse: no <OpenPosition> found — check the Open Positions section/tag")
    elif not any("day_pnl" in p for p in positions.values()):
        log.info("Flex parse: no MTM day P&L mapped — check MTMPerformanceSummaryUnderlying")
    if not nav:
        log.info("Flex parse: no NAV section — check EquitySummary*/ChangeInNAV tags")
    if not trades:
        log.info("Flex parse: no <Trade> rows — Trades section may be off in the Flex Query (050)")

    return {
        "account_id": _attr(stmt, "accountId"),
        "as_of": as_of,
        "base_currency": base_ccy,
        "is_mock": False,
        "nav": nav,
        "change_in_nav": change,
        "positions": list(positions.values()),
        "performance": perf,
        "currency_rates": rates,
        "trades": trades,
    }


# --- Public entry points -------------------------------------------------------

async def fetch_flex_statement(token: str, query_id: str) -> dict:
    """Real 2-step fetch + parse. Raises IBKRFlexError on any failure."""
    ref, url = await _send_request(token, query_id)
    return parse_flex_statement(await _get_statement(url, token, ref))


async def get_portfolio_snapshot(token: str | None = None, query_id: str | None = None) -> dict:
    """Mock-first entry point for W2/W5. USE_MOCK_IBKR=1 or missing creds -> fixture."""
    creds = None if os.getenv("USE_MOCK_IBKR") == "1" else _creds_from(token, query_id)
    if creds is None:
        snap = parse_flex_statement(_FIXTURE.read_text())
        snap["is_mock"] = True
        return snap
    return await fetch_flex_statement(*creds)
