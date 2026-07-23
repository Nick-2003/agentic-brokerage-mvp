#!/usr/bin/env python3
"""Offline guard for Proposal 079 — options quote integrity (degenerate-field suppression).

Network-free. Temp-apply → assert → restore-in-`finally`, with the 078 LIVE-MODE
and non-destructive (`_created`) guards. Confirm with `git status` after running.

**The bug this locks down.** Outside market hours yfinance's delayed feed carries no
book: every bid/ask is 0.0, so its IV solver has nothing to solve from and returns
placeholder/bisection artifacts. Observed live on NVDA:

    bid=0.0 ask=0.0  IV=1.0000000000000003e-05   (solver floor)
    bid=0.0 ask=0.0  IV=0.007822421875           (~2^-7)
    bid=0.0 ask=0.0  IV=0.2500075                (~2^-2)

Passed through, those rendered as "0.8%" / "25.0%" implied vol for a name whose real
IV is ~40-60%. Open interest was 0 on EVERY strike while volume ran 27k-92k.
None of it would trip the 067 validator — the numbers *did* come from a tool.
Sourced is not the same as meaningful, so we suppress at the tool boundary.

Covers:
  A. no-NBBO snapshot (bid/ask 0, IV artifacts, OI 0) → bid/ask/IV/OI all None,
     quote_status "no_nbbo", iv_available False, note explains why;
  B. last price + volume SURVIVE (they're real — don't over-suppress);
  C. healthy snapshot (real bid/ask, IV 0.42) → values preserved, IV = 42.0,
     quote_status "live", iv_available True, note NOT extended;
  D. the 1e-05 solver floor is suppressed even when a book IS present (defensive);
  E. a genuine 0 open interest with a live book is preserved (not over-suppressed);
  F. mock/demo chain stays fully usable (live + iv_available);
  G. the system.md rule telling the model to say "unavailable" is present.

Run:
    backend/.venv/bin/python scripts/test_079_options_quote_integrity.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        p = os.path.dirname(d)
        if p == d:
            raise RuntimeError("repo root not found")
        d = p


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes", "079-options-quote-integrity", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["tools/options.py", "prompts/system.md"]
_created: list[str] = []
LIVE_MODE = not os.path.isdir(PROP)


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 079 is applied; asserting against the LIVE tree)")
        return
    for f in OVERWRITE:
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        shutil.copy2(os.path.join(BACKEND, f), bak)
        shutil.copy2(os.path.join(PROP, f), os.path.join(BACKEND, f))


def restore(backup_dir: str) -> None:
    if LIVE_MODE:
        return
    for f in OVERWRITE:
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        if os.path.isfile(bak):
            shutil.copy2(bak, os.path.join(BACKEND, f))
    for p in _created:
        if os.path.isfile(p):
            os.remove(p)


class _FakeDF:
    """Minimal DataFrame stand-in — only `.to_dict('records')` is used."""
    def __init__(self, records):
        self._records = records

    def to_dict(self, _how):
        return list(self._records)


def _install_fake_yf(rows):
    class _Chain:
        calls = _FakeDF(rows)
        puts = _FakeDF(rows)
        underlying = {"regularMarketPrice": 212.06}

    class _Ticker:
        options = ["2026-07-24", "2026-07-31"]
        fast_info = {"last_price": 212.06}

        def __init__(self, *a, **k):
            pass

        def option_chain(self, _exp):
            return _Chain()

    fake = types.ModuleType("yfinance")
    fake.Ticker = _Ticker
    sys.modules["yfinance"] = fake


def _row(strike, *, bid, ask, iv, oi, vol=50000.0, last=4.0):
    return {"strike": strike, "lastPrice": last, "bid": bid, "ask": ask,
            "volume": vol, "openInterest": oi, "impliedVolatility": iv,
            "inTheMoney": strike < 212.06}


def run() -> None:
    sys.path.insert(0, BACKEND)
    import tools.options as options

    real_yf = sys.modules.get("yfinance")
    os.environ["USE_MOCK_OPTIONS"] = "0"
    try:
        # ------------------------------------------------------------ A + B
        print("\nA. no-NBBO snapshot → degenerate fields suppressed")
        # The exact live shape: no book, solver-artifact IV, zero OI, real volume.
        _install_fake_yf([
            _row(205.0, bid=0.0, ask=0.0, iv=1.0000000000000003e-05, oi=0, vol=27142.0, last=7.8),
            _row(210.0, bid=0.0, ask=0.0, iv=0.007822421875, oi=0, vol=92363.0, last=4.0),
            _row(215.0, bid=0.0, ask=0.0, iv=0.2500075, oi=0, vol=128459.0, last=1.57),
        ])
        r = asyncio.run(options.get_option_chain({"ticker": "NVDA", "option_type": "calls"}, "u"))
        rows = r["calls"]
        check("quote_status is no_nbbo", r.get("quote_status") == "no_nbbo", str(r.get("quote_status")))
        check("iv_available is False", r.get("iv_available") is False)
        check("bid/ask are None (not 0.0)", all(x["bid"] is None and x["ask"] is None for x in rows))
        check("implied_vol_pct all None (no 0.8% / 25.0%)",
              all(x["implied_vol_pct"] is None for x in rows),
              str([x["implied_vol_pct"] for x in rows]))
        check("open_interest None (not a misleading 0)",
              all(x["open_interest"] is None for x in rows))
        check("note explains the outage",
              "market is likely closed" in r.get("note", ""), r.get("note", "")[:80])

        print("\nB. real fields survive (don't over-suppress)")
        check("last price preserved", [x["last"] for x in rows] == [7.8, 4.0, 1.57])
        check("volume preserved", [x["volume"] for x in rows] == [27142.0, 92363.0, 128459.0])
        check("strike preserved", [x["strike"] for x in rows] == [205.0, 210.0, 215.0])

        # ------------------------------------------------------------ C
        print("\nC. healthy snapshot → values pass through")
        _install_fake_yf([
            _row(210.0, bid=3.5, ask=3.95, iv=0.42, oi=5250.0),
            _row(215.0, bid=1.4, ask=1.7, iv=0.385, oi=1399.0),
        ])
        h = asyncio.run(options.get_option_chain({"ticker": "NVDA", "option_type": "calls"}, "u"))
        hr = h["calls"]
        check("quote_status live", h.get("quote_status") == "live")
        check("iv_available True", h.get("iv_available") is True)
        check("IV 0.42 → 42.0%", hr[0]["implied_vol_pct"] == 42.0, str(hr[0]["implied_vol_pct"]))
        check("bid/ask preserved", hr[0]["bid"] == 3.5 and hr[0]["ask"] == 3.95)
        check("open_interest preserved", hr[0]["open_interest"] == 5250.0)
        check("note NOT extended when healthy", "market is likely closed" not in h.get("note", ""))

        # ------------------------------------------------------------ D
        print("\nD. solver floor suppressed even with a live book (defensive)")
        _install_fake_yf([_row(210.0, bid=3.5, ask=3.95, iv=1.0000000000000003e-05, oi=100.0)])
        d = asyncio.run(options.get_option_chain({"ticker": "NVDA", "option_type": "calls"}, "u"))
        check("1e-05 IV → None despite a book", d["calls"][0]["implied_vol_pct"] is None)
        check("but the row is otherwise live", d.get("quote_status") == "live")
        check("iv_available False (no usable IV in any row)", d.get("iv_available") is False)

        # ------------------------------------------------------------ E
        print("\nE. a GENUINE zero OI with a live book is preserved")
        _install_fake_yf([_row(210.0, bid=3.5, ask=3.95, iv=0.42, oi=0.0)])
        e = asyncio.run(options.get_option_chain({"ticker": "NVDA", "option_type": "calls"}, "u"))
        check("open_interest 0.0 kept (real zero, not suppressed)",
              e["calls"][0]["open_interest"] == 0.0)
    finally:
        if real_yf is not None:
            sys.modules["yfinance"] = real_yf
        else:
            sys.modules.pop("yfinance", None)
        os.environ["USE_MOCK_OPTIONS"] = "1"

    # ---------------------------------------------------------------- F
    print("\nF. mock/demo chain stays fully usable")
    m = asyncio.run(options.get_option_chain({"ticker": "NVDA", "strikes": 2}, "u"))
    check("mock quote_status live", m.get("quote_status") == "live")
    check("mock iv_available True", m.get("iv_available") is True)
    check("mock rows keep bid/ask/IV",
          all(x["bid"] is not None and x["implied_vol_pct"] is not None for x in m["calls"]))

    # ---------------------------------------------------------------- G
    print("\nG. prompt rule present")
    sm = open(os.path.join(BACKEND, "prompts", "system.md")).read()
    check("system.md tells the model to say 'unavailable'",
          "quote_status" in sm and "iv_available" in sm)
    check("system.md forbids rendering a null as 0 / dash",
          "never print a blank" in sm.lower() or "do not render a null" in sm.lower())


def main() -> None:
    backup = tempfile.mkdtemp(prefix="079-backup-")
    try:
        apply_proposal(backup)
        run()
    finally:
        restore(backup)
        shutil.rmtree(backup, ignore_errors=True)

    total, ok = len(results), sum(results)
    print(f"\n{'=' * 62}\n  {ok}/{total} checks passed\n{'=' * 62}")
    print("Live tree restored — confirm with: git status --short")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
