"""Offline test for W1 — IBKR Flex connector. Fully offline (stubs `_http_get`).

    backend/.venv/bin/python proposed_changes/W1-ibkr-flex-connector/scripts/test_W1_ibkr_flex.py
    # (or, once applied:  backend/.venv/bin/python scripts/test_W1_ibkr_flex.py)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# Put the backend dir that holds ibkr_flex.py on sys.path (proposal folder pre-apply,
# repo/backend post-apply). Walk up to find it.
for _up in _HERE.parents:
    _cand = _up / "backend"
    if (_cand / "ibkr_flex.py").exists():
        sys.path.insert(0, str(_cand))
        break

import ibkr_flex as ib  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}")


# --- canned control responses --------------------------------------------------

SEND_SUCCESS = (
    '<FlexStatementResponse timestamp="x"><Status>Success</Status>'
    "<ReferenceCode>9999</ReferenceCode>"
    "<Url>https://example.test/GetStatement</Url></FlexStatementResponse>"
)
SEND_FAIL_TOKEN = (
    "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1003</ErrorCode>"
    "<ErrorMessage>Token is invalid</ErrorMessage></FlexStatementResponse>"
)
GET_IN_PROGRESS = (
    "<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>"
    "<ErrorMessage>Statement generation in progress</ErrorMessage></FlexStatementResponse>"
)
FIXTURE_XML = ib._FIXTURE.read_text()


def _install_http(script: dict[str, list[str]]) -> None:
    """Stub ib._http_get to pop canned bodies per endpoint ('send' | 'get')."""
    seq = {k: list(v) for k, v in script.items()}

    async def fake(url: str, params: dict) -> str:  # noqa: ANN001
        key = "send" if url == ib.FLEX_SENDREQUEST_URL else "get"
        return seq[key].pop(0)

    ib._http_get = fake  # type: ignore[assignment]


def _env(mock=None, tok=None, qid=None) -> None:
    for var, val in [("USE_MOCK_IBKR", mock), ("IBKR_FLEX_TOKEN", tok), ("IBKR_FLEX_QUERY_ID", qid)]:
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val


async def main() -> None:
    # ---- parse_flex_statement(fixture) — reconciled to real Flex shapes ----
    snap = ib.parse_flex_statement(FIXTURE_XML)
    check("parse: account_id", snap["account_id"] == "U0000001")
    check("parse: as_of (toDate)", snap["as_of"] == "2026-06-05")
    check("parse: base_currency HKD (not position USD)", snap["base_currency"] == "HKD")
    check("parse: 2 holdings only (cash/Total excluded)", len(snap["positions"]) == 2)
    check("parse: no cash/total leaked", {p["symbol"] for p in snap["positions"]} == {"AAPL", "NVDA"})
    check("parse: is_mock False", snap["is_mock"] is False)
    aapl = next((p for p in snap["positions"] if p["symbol"] == "AAPL"), {})
    check("parse: AAPL day_pnl 705.0 (from MTM `total`)", aapl.get("day_pnl") == 705.0)
    check("parse: AAPL description", aapl.get("description") == "APPLE INC")
    check("parse: AAPL quantity 300", aapl.get("quantity") == 300.0)
    check("parse: AAPL unrealized_pnl 6975.0", aapl.get("unrealized_pnl") == 6975.0)
    check("parse: AAPL pct_of_nav 24.3 (LOT row didn't blank it)", aapl.get("pct_of_nav") == 24.3)
    check("parse: AAPL position_value_base present", aapl.get("position_value_base") == 473459.61)
    check("parse: AAPL fx_rate_to_base 7.8342", aapl.get("fx_rate_to_base") == 7.8342)
    check("parse: NAV total = today's row (06-05)", snap["nav"].get("total") == 248750.40)
    check("parse: NAV prev_total from ChangeInNAV", snap["nav"].get("prev_total") == 246980.12)
    check("parse: change_in_nav.mtm", snap["change_in_nav"].get("mtm") == 1770.28)
    check("parse: performance mtd (Total row)", snap["performance"].get("mtd") == 1770.28)
    check("parse: performance ytd (Total row)", snap["performance"].get("ytd") == 41210.55)
    check("parse: performance unrealized (FIFO Total)", snap["performance"].get("unrealized") == 40539.00)
    check("parse: currency_rates USD→base", snap["currency_rates"].get("USD") == 7.8342)

    # ---- get_portfolio_snapshot mock-first ----
    _env(mock="1")
    msnap = await ib.get_portfolio_snapshot()
    check("snapshot: USE_MOCK_IBKR=1 → is_mock True", msnap["is_mock"] is True)
    check("snapshot: mock has positions", len(msnap["positions"]) == 2)

    # ---- ibkr_flex_enabled truth table ----
    _env(mock="1", tok="realtoken", qid="123")
    check("enabled: USE_MOCK_IBKR=1 → False", ib.ibkr_flex_enabled() is False)
    _env(tok="realtoken", qid="123")
    check("enabled: creds present → True", ib.ibkr_flex_enabled() is True)
    _env(tok="REPLACE", qid="123")
    check("enabled: token REPLACE → False", ib.ibkr_flex_enabled() is False)
    _env(tok="realtoken")  # no query id
    check("enabled: missing query id → False", ib.ibkr_flex_enabled() is False)
    _env()  # nothing
    check("enabled: no creds → False", ib.ibkr_flex_enabled() is False)

    # ---- 2-step happy path with a 1019 poll (stubbed transport, no real waits) ----
    ib._POLL_BACKOFF_S = [0.0, 0.0]
    _install_http({"send": [SEND_SUCCESS], "get": [GET_IN_PROGRESS, FIXTURE_XML]})
    fetched = await ib.fetch_flex_statement("t", "q")
    check("fetch: polls 1019 then parses", len(fetched["positions"]) == 2 and fetched["account_id"] == "U0000001")

    # ---- GetStatement host fallback: returned <Url> host unreachable → canonical host ----
    async def fake_hostfb(url: str, params: dict) -> str:  # noqa: ANN001
        if url == "https://gdcdyn.example.test/GetStatement":
            raise ib.IBKRFlexError(f"connect ({url})", code="ibkr_flex_connect_error")
        return FIXTURE_XML

    ib._http_get = fake_hostfb  # type: ignore[assignment]
    raw = await ib._get_statement("https://gdcdyn.example.test/GetStatement", "t", "ref")
    check("get_statement: unreachable Url host → falls back to canonical host", "<FlexStatement " in raw)

    # ---- SendRequest fail → invalid_token code ----
    _install_http({"send": [SEND_FAIL_TOKEN], "get": []})
    try:
        await ib.fetch_flex_statement("t", "q")
        check("error: SendRequest fail raises", False)
    except ib.IBKRFlexError as e:
        check("error: SendRequest 1003 → ibkr_flex_invalid_token", e.code == "ibkr_flex_invalid_token")

    # ---- GetStatement 1019 forever → timeout code ----
    ib._POLL_BACKOFF_S = [0.0]
    _install_http({"send": [SEND_SUCCESS], "get": [GET_IN_PROGRESS, GET_IN_PROGRESS]})
    try:
        await ib.fetch_flex_statement("t", "q")
        check("error: 1019 forever raises", False)
    except ib.IBKRFlexError as e:
        check("error: 1019 forever → ibkr_flex_statement_timeout", e.code == "ibkr_flex_statement_timeout")

    # ---- non-XML body → parse_failed ----
    try:
        ib.parse_flex_statement("not xml at all")
        check("error: non-XML raises", False)
    except ib.IBKRFlexError as e:
        check("error: non-XML → ibkr_flex_parse_failed", e.code == "ibkr_flex_parse_failed")


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
