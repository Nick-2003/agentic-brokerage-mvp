"""Offline test for W2 — briefing generator. Fully offline (no network, no LLM).

Covers: facts computation (base-ccy P&L, mover ordering, %s), the mock template
render, the mock-gate truth table, the real-LLM plumbing with a stubbed client,
and build_briefing() end-to-end over the W1 mock fixture.

    backend/.venv/bin/python proposed_changes/W2-briefing-generator/scripts/test_W2_briefing.py
    # (or, once applied:  backend/.venv/bin/python scripts/test_W2_briefing.py)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# Put both backends on sys.path: the one CO-LOCATED with this test (its `briefing.py`
# — the proposal copy pre-apply, the repo copy post-apply) MUST win for `import
# briefing`, and the repo backend (has `ibkr_flex.py` + `tools/`) supplies the rest.
# Insert the repo backend first, then the co-located one (last insert(0) wins) so a
# stale APPLIED briefing.py never shadows the version this test ships with.
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))       # ibkr_flex + tools
sys.path.insert(0, str(_COLOCATED_BACKEND))      # briefing.py — wins over any applied copy

os.environ["USE_MOCK_IBKR"] = "1"      # parse the bundled fixture
os.environ["USE_MOCK_MARKET"] = "1"    # deterministic macro + news
os.environ["USE_MOCK_BRIEFING"] = "1"  # template render (no LLM) by default

import briefing as br  # noqa: E402
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


async def main() -> None:
    snap = ib.parse_flex_statement(ib._FIXTURE.read_text())

    # ---- compute_brief_facts (no context) ----
    f = br.compute_brief_facts(snap)
    check("facts: base_currency HKD", f["base_currency"] == "HKD")
    check("facts: nav_total verbatim", f["nav_total"] == 248750.40)
    check("facts: nav display has HK$ symbol", f["nav_total_display"] == "HK$248,750.40")
    check("facts: day_change = ending-starting", f["day_change"] == 1770.28)
    check("facts: day_change_pct ~0.72", f["day_change_pct"] == 0.72)
    check("facts: day_change_display signed", f["day_change_display"] == "+HK$1,770.28")
    check("facts: 2 holdings", f["holdings_count"] == 2)
    check("facts: NVDA is top mover (bigger |day_pnl|)", f["movers"][0]["symbol"] == "NVDA")
    check("facts: NVDA day_pnl base-ccy", f["movers"][0]["day_pnl"] == 1065.60)
    check("facts: NVDA day_pnl_display", f["movers"][0]["day_pnl_display"] == "+HK$1,065.60")
    check("facts: NVDA change_pct 0.76", f["movers"][0]["change_pct"] == 0.76)
    check("facts: AAPL change_pct 1.18", f["movers"][1]["change_pct"] == 1.18)
    check("facts: mtd verbatim", f["mtd"] == 1770.28)
    check("facts: ytd display", f["ytd_display"] == "+HK$41,210.55")
    check("facts: max_chars present", isinstance(f["max_chars"], int))

    # ---- gather_market_context: LIVE → real news (stubbed), mock macro suppressed ----
    # `snap` is the raw parsed fixture (is_mock False) → a live snapshot. Macro is
    # mock-only so it's dropped; news comes from fetch_recent_news — stub it (no network).
    async def _fake_real_news(tickers, limit=2, since=None):  # noqa: ANN001
        return {
            "news_by_ticker": {
                "NVDA": [{"headline": "Real NVDA headline", "source": "Reuters",
                          "ts": "2026-06-05T10:00:00Z", "url": None}]
            },
            "is_mock": False,
        }

    _orig_news = br.fetch_recent_news
    br.fetch_recent_news = _fake_real_news  # type: ignore[assignment]
    ctx_live = await br.gather_market_context(snap)
    br.fetch_recent_news = _orig_news  # type: ignore[assignment]
    check("context(live): mock macro suppressed", ctx_live["macro"] == {})
    check("context(live): real news used (not mock)", "NVDA" in ctx_live["news_by_ticker"])

    # A mock-demo snapshot (is_mock True) → mock context allowed (it's a labelled demo).
    msnap = await ib.get_portfolio_snapshot()  # USE_MOCK_IBKR=1 → is_mock True
    ctx = await br.gather_market_context(msnap)
    check("context(mock): macro present", bool(ctx["macro"]))
    check("context(mock): mock news for NVDA mover", "NVDA" in ctx["news_by_ticker"])
    f2 = br.compute_brief_facts(msnap, ctx)
    check("context(mock): headlines attached to top mover", len(f2["movers"][0]["headlines"]) >= 1)

    # ---- _parse_yf_news_item: tolerate both yfinance shapes ----
    from news_context import _parse_yf_news_item as _pyf  # noqa: E402
    nested = _pyf({"id": "x", "content": {
        "title": "T1", "pubDate": "2026-06-05T10:00:00Z",
        "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "http://e/1"}}})
    check("yf parse(nested): headline", nested["headline"] == "T1")
    check("yf parse(nested): source from provider", nested["source"] == "Reuters")
    check("yf parse(nested): ts iso passthrough", nested["ts"] == "2026-06-05T10:00:00Z")
    check("yf parse(nested): url from canonicalUrl", nested["url"] == "http://e/1")
    flat = _pyf({"title": "T2", "publisher": "WSJ",
                 "providerPublishTime": 1717579800, "link": "http://e/2"})
    check("yf parse(flat): headline", flat["headline"] == "T2")
    check("yf parse(flat): source from publisher", flat["source"] == "WSJ")
    check("yf parse(flat): epoch → iso ts", bool(flat["ts"]) and "T" in flat["ts"])
    check("yf parse(no title) → None", _pyf({"content": {"summary": "no title"}}) is None)

    # ---- mock render ----
    text = br._render_mock_briefing(f2)
    check("mock render: non-empty", bool(text.strip()))
    check("mock render: quotes NAV display", "HK$248,750.40" in text)
    check("mock render: quotes NVDA day P&L", "+HK$1,065.60" in text)
    check("mock render: within max_chars", len(text) <= f2["max_chars"])
    check("mock render: no HTML tags", "<strong>" not in text and "<em>" not in text)

    # ---- generate_briefing (mock path) ----
    out = await br.generate_briefing(msnap, ctx)
    check("generate(mock): is_mock True (snapshot mock)", out["is_mock"] is True)
    check("generate(mock): model 'mock'", out["model"] == "mock")
    check("generate(mock): base_currency carried", out["base_currency"] == "HKD")
    check("generate(mock): text present", bool(out["text"]))
    check("generate(mock): generated_at iso", "T" in out["generated_at"])
    check("generate(mock): permalink None for now", out["permalink"] is None)

    # ---- build_briefing end-to-end (mock-first snapshot) ----
    built = await br.build_briefing()
    check("build: end-to-end text", bool(built["text"]))
    check("build: is_mock True", built["is_mock"] is True)

    # ---- mock-gate truth table ----
    os.environ["USE_MOCK_BRIEFING"] = "1"
    check("gate: USE_MOCK_BRIEFING=1 → True", br.briefing_mock_enabled() is True)
    os.environ.pop("USE_MOCK_BRIEFING", None)
    _key = os.environ.pop("ANTHROPIC_API_KEY", None)
    check("gate: no key → mock True", br.briefing_mock_enabled() is True)
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    check("gate: key present, no force → real (False)", br.briefing_mock_enabled() is False)

    # ---- real-LLM plumbing with a stubbed Anthropic client ----
    class _Block:
        type = "text"
        text = "📈 *Your IBKR book* is up overnight. *NVDA* +HK$1,065.60 led."

    class _Resp:
        content = [_Block()]

    class _Msgs:
        async def create(self, **kwargs):  # noqa: ANN003
            _Msgs.last_kwargs = kwargs
            return _Resp()

    class _FakeClient:
        messages = _Msgs()

    br._client = _FakeClient()  # type: ignore[assignment]
    real = await br.generate_briefing(snap, ctx_live)  # live snapshot + suppressed ctx
    check("real: used LLM text", real["text"].startswith("📈 *Your IBKR book*"))
    check("real: model is not 'mock'", real["model"] != "mock")
    # `snap` is the raw parsed fixture (is_mock False); gen path is real → is_mock False.
    check("real: is_mock False (raw snapshot + real LLM)", real["is_mock"] is False)
    check("real: system prompt sent", "system" in _Msgs.last_kwargs)
    check("real: facts in user message", "<facts>" in _Msgs.last_kwargs["messages"][0]["content"])

    # ---- real-LLM empty response → BriefingError ----
    class _EmptyResp:
        content = []

    class _EmptyMsgs:
        async def create(self, **kwargs):  # noqa: ANN003
            return _EmptyResp()

    class _EmptyClient:
        messages = _EmptyMsgs()

    br._client = _EmptyClient()  # type: ignore[assignment]
    try:
        await br.generate_briefing(snap, ctx_live)
        check("real: empty response raises", False)
    except br.BriefingError as e:
        check("real: empty → briefing_empty code", e.code == "briefing_empty")

    # restore env
    if _key is not None:
        os.environ["ANTHROPIC_API_KEY"] = _key
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
