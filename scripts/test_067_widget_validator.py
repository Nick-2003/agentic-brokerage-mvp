#!/usr/bin/env python3
"""Offline guard for Proposal 067 — widget numeric-provenance validator.

Trust #1/#3: every HARD market/account number in a widget (price, fill, P&L,
analyst target) must trace back to a tool result from that turn. Derived numbers
(sizing, %-of-NAV, risk_score, proposed stops) are warn-tier and never block;
prose numbers are not checked at all.

Covers:
  A. collect_tool_numbers — numeric leaves + numbers inside strings, mapped to the
     tool_use id that produced them; nested dicts/lists.
  B. tolerance — 945.00≈945.0, 8673≈8672.61 (0.1%); an invented number is rejected.
  C. validate_widget:
       · sourced research_card / live_trade → ok + provenance names the tool id
       · fabricated current_price (research_card, and nested tracker.trade) → violation
       · NO tools at all + a priced widget → violation (pure fabrication)
       · absent optional enforced field → not counted, still ok
       · order_ticket sizing / portfolio_risk.risk_score / morning_brief prose
         → ok (warn-tier only), i.e. NO false positives on derived values
  D. validator_mode() env parsing.
  E. wiring — agent.py imports validation, records raw tool_facts, fails closed.

Runs against the LIVE backend (067 is applied). It used to temp-apply from
`.proposed_changes/067-…/`, which broke the moment that staging dir was deleted
post-apply; now it imports the live `validation` / `tools.portfolio` and reads the
live prompts, so it keeps working as a permanent regression guard. Read-only —
touches no file. Anchored on backend/auth.py.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_067_widget_validator.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"could not locate repo root (backend/auth.py) above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


# A realistic turn: a quote tool and a consensus tool.
QUOTE = {
    "id": "tc_quote",
    "name": "get_quote",
    "result": {"quotes": [{"ticker": "NVDA", "price": 942.5, "change_pct": 1.98}]},
}
CONSENSUS = {
    "id": "tc_fmp",
    "name": "get_consensus_targets",
    "result": {"ticker": "NVDA", "median_target": 1100, "n_analysts": 38},
}
POSITION = {
    "id": "tc_pos",
    "name": "get_open_position",
    "result": {
        "ticker": "NVDA", "shares": 10, "fill_price": 945.0,
        "current_price": 947.19, "unrealized_pnl": 21.9, "unrealized_pnl_pct": 0.23,
    },
}
NEWS = {
    "id": "tc_news",
    "name": "get_company_news",
    "result": {"news": [{"headline": "Goldman raises NVDA target to $1,200"}]},
}


def run() -> None:
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    import validation as V  # noqa: E402 — after temp-apply

    print("\n=== A. collect_tool_numbers ===")
    pool = V.collect_tool_numbers([QUOTE, CONSENSUS, NEWS])
    check("numeric leaf harvested (942.5)", 942.5 in pool and pool[942.5] == "tc_quote")
    check("nested list/dict walked (1.98)", 1.98 in pool)
    check("number inside a string harvested (1,200 → 1200)", 1200.0 in pool and pool[1200.0] == "tc_news")
    check("maps back to the tool_use id", pool.get(1100.0) == "tc_fmp", str(pool.get(1100.0)))

    print("\n=== B. tolerance: rounding ok, invention rejected ===")
    check("945.00 matches 945.0", V._match(945.00, {945.0: "t"}) == "t")
    check("8673 matches 8672.61 (0.1%)", V._match(8673.0, {8672.61: "t"}) == "t")
    check("invented 1234.56 rejected", V._match(1234.56, {942.5: "t", 1100.0: "t"}) is None)

    print("\n=== C. validate_widget ===")
    # sourced research_card
    w = {"type": "research_card", "data": {"current_price": 942.50, "target_price": 1100, "horizon_months": 12}}
    r = V.validate_widget(w, [QUOTE, CONSENSUS])
    check("sourced research_card → ok", r.ok and r.checked == 2, f"checked={r.checked} v={r.violations}")
    check("provenance names the tool ids", r.provenance.get("current_price") == "tc_quote" and r.provenance.get("target_price") == "tc_fmp", str(r.provenance))

    # fabricated price
    bad = {"type": "research_card", "data": {"current_price": 1234.56, "target_price": 1100}}
    rb = V.validate_widget(bad, [QUOTE, CONSENSUS])
    check("fabricated current_price → violation", (not rb.ok) and rb.violations[0].path == "current_price", str(rb.violations))

    # nested tracker path
    trk = {"type": "tracker", "data": {"trade": {"shares": 10, "fill_price": 945, "current_price": 8888.0, "unrealized_pnl": 21.9, "unrealized_pnl_pct": 0.23}}}
    rt = V.validate_widget(trk, [POSITION])
    check("nested tracker.trade.current_price violation", (not rt.ok) and any(v.path == "trade.current_price" for v in rt.violations), str(rt.violations))

    # live_trade fully sourced
    lt = {"type": "live_trade", "data": {"shares": 10, "fill_price": 945.00, "current_price": 947.19, "unrealized_pnl": 21.90, "unrealized_pnl_pct": 0.23, "tp_armed_at": 1100, "sl_armed_at": 880}}
    rl = V.validate_widget(lt, [POSITION])
    check("sourced live_trade → ok (tp/sl are proposals, not enforced)", rl.ok, str(rl.violations))

    # NO tools at all → a priced widget is pure fabrication
    rn = V.validate_widget(w, [])
    check("priced widget with zero tool calls → violation", not rn.ok, str(rn.violations))

    # absent optional enforced field
    part = {"type": "research_card", "data": {"current_price": 942.5}}
    rp = V.validate_widget(part, [QUOTE])
    check("absent target_price not counted, still ok", rp.ok and rp.checked == 1, f"checked={rp.checked}")

    print("\n=== C2. NO false positives on derived/judgment values ===")
    ot = {"type": "order_ticket", "data": {"shares": 7, "notional": 6597.5, "limit_price": 943, "tp_price": 1100, "sl_price": 880, "rr_ratio": 2.2, "portfolio_pct": 9.5}}
    ro = V.validate_widget(ot, [QUOTE])
    check("order_ticket sizing never blocks", ro.ok and ro.checked == 0)
    check("but derived values are reported as warn-tier", len(ro.warn_unverified) > 0, str(ro.warn_unverified[:3]))

    pr = {"type": "portfolio_risk", "data": {"risk_score": 7.2, "sector_exposure": [{"label": "Tech", "pct": 82}]}}
    check("portfolio_risk risk_score/pct never block", V.validate_widget(pr, [QUOTE]).ok)

    mb = {"type": "morning_brief", "data": {"headline": "Up 1.46% overnight", "paragraphs": ["NVDA +1.98% and the 10-K lands in 2028"]}}
    check("morning_brief prose never blocks", V.validate_widget(mb, [QUOTE]).ok)

    print("\n=== D. validator_mode() ===")
    saved = os.environ.get("WIDGET_VALIDATOR_MODE")
    try:
        os.environ.pop("WIDGET_VALIDATOR_MODE", None)
        check("default is warn", V.validator_mode() == V.MODE_WARN)
        os.environ["WIDGET_VALIDATOR_MODE"] = "enforce"
        check("enforce parsed", V.validator_mode() == V.MODE_ENFORCE)
        os.environ["WIDGET_VALIDATOR_MODE"] = "OFF"
        check("case-insensitive off", V.validator_mode() == V.MODE_OFF)
        os.environ["WIDGET_VALIDATOR_MODE"] = "nonsense"
        check("invalid falls back to warn", V.validator_mode() == V.MODE_WARN)
    finally:
        os.environ.pop("WIDGET_VALIDATOR_MODE", None)
        if saved is not None:
            os.environ["WIDGET_VALIDATOR_MODE"] = saved

    print("\n=== E. agent.py wiring ===")
    import agent  # noqa: E402 — proves `import validation` resolves inside agent
    check("agent imports cleanly with validation", hasattr(agent, "run_agent"))
    src = open(os.path.join(BACKEND, "agent.py"), encoding="utf-8").read()
    check("agent imports validation", "import validation" in src)
    check("agent records RAW tool results", "tool_facts.append(" in src)
    check("agent validates before emitting the widget", "validation.validate_widget(widget, tool_facts)" in src)
    check("agent fails closed in enforce mode", "widget_unverified" in src and "MODE_ENFORCE" in src)


def main() -> int:
    # Runs against the live backend — nothing to apply or restore.
    run()

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
