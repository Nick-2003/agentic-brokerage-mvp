#!/usr/bin/env python3
"""Offline guard for Proposal 077 — data-only options chain (get_option_chain).

Network-free. Temp-apply → assert → restore-in-`finally`, non-destructive guard
(never delete a file this run did not create — the 073 lesson). Confirm with
`git status` after running.

Covers:
  A. mock chain shape — ticker/expiration/expirations + calls/puts rows carrying
     strike, implied_vol_pct, open_interest; greeks_available is False + note;
  B. IV rendered as a PERCENT (fraction × 100), rounded (real-path parse);
  C. honest error — a yfinance stub that RAISES → source "yfinance_options_error"
     + error, and NOT a mock chain;
  D. empty .options → no_options_for_ticker;
  E. strike-window limiting — `strikes=N` → ≤ 2N+1 rows;
  F. registration — get_option_chain in TOOL_REGISTRY, tool count 18 → 19;
  G. prompt carve-out present — the options routing row + the :45 exception + the
     "no Greeks" honesty rule in system.md.

Run:
    backend/.venv/bin/python scripts/test_077_options_chain.py
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
PROP = os.path.join(REPO, ".proposed_changes", "077-options-chain", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["tools/__init__.py", "prompts/system.md"]
NET_NEW = ["tools/options.py"]
_created: list[str] = []


def apply_proposal(backup_dir: str) -> None:
    for f in OVERWRITE:
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        shutil.copy2(os.path.join(BACKEND, f), bak)
        shutil.copy2(os.path.join(PROP, f), os.path.join(BACKEND, f))
    for f in NET_NEW:
        dst = os.path.join(BACKEND, f)
        if os.path.isfile(dst):              # already applied → back up, don't delete
            shutil.copy2(dst, os.path.join(backup_dir, f.replace("/", "__")))
            OVERWRITE.append(f)
        else:
            _created.append(dst)
        shutil.copy2(os.path.join(PROP, f), dst)


def restore(backup_dir: str) -> None:
    for f in OVERWRITE:
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        if os.path.isfile(bak):
            shutil.copy2(bak, os.path.join(BACKEND, f))
    for p in _created:                       # ONLY what this run created
        if os.path.isfile(p):
            os.remove(p)


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ["USE_MOCK_OPTIONS"] = "1"

    import tools
    import tools.options as options

    # ---------------------------------------------------------------- A
    print("\nA. mock chain shape")
    r = asyncio.run(options.get_option_chain({"ticker": "NVDA", "strikes": 4}, "u"))
    check("has expiration + expirations list",
          isinstance(r.get("expiration"), str) and isinstance(r.get("expirations"), list) and r["expirations"])
    check("calls + puts present (both)", "calls" in r and "puts" in r)
    row = (r.get("calls") or [{}])[0]
    check("rows carry strike / implied_vol_pct / open_interest",
          "strike" in row and "implied_vol_pct" in row and "open_interest" in row, str(row))
    check("greeks_available is False", r.get("greeks_available") is False)
    check("note names the missing Greeks",
          "greek" in (r.get("note") or "").lower())
    check("source mock / is_mock True", r.get("source") == "mock" and r.get("is_mock") is True)

    # calls-only honours option_type
    ronly = asyncio.run(options.get_option_chain({"ticker": "NVDA", "option_type": "calls"}, "u"))
    check("option_type=calls → no puts", "calls" in ronly and "puts" not in ronly)

    # ---------------------------------------------------------------- B
    print("\nB. IV parsed to percent on the real path")
    # Build a fake yfinance module: a Ticker with .options + .option_chain returning
    # a tiny DataFrame-like object (list of records) whose impliedVolatility is a FRACTION.
    made = _install_fake_yf(options, iv_fraction=0.4231)
    os.environ["USE_MOCK_OPTIONS"] = "0"
    try:
        rr = asyncio.run(options.get_option_chain({"ticker": "NVDA", "strikes": 2}, "u"))
        ivs = [row["implied_vol_pct"] for row in rr.get("calls", []) if row.get("implied_vol_pct") is not None]
        check("IV fraction 0.4231 → ~42.3%", ivs and abs(ivs[0] - 42.3) < 0.05, str(ivs[:1]))
        check("source yfinance, not mock", rr.get("source") == "yfinance" and rr.get("is_mock") is False)

        # ------------------------------------------------------------ E
        print("\nE. strike-window limiting")
        check("strikes=2 → ≤ 5 rows per side", all(len(rr.get(k, [])) <= 5 for k in ("calls", "puts")),
              f"calls={len(rr.get('calls', []))}")

        # ------------------------------------------------------------ D
        print("\nD. empty .options → no_options_for_ticker")
        made["ticker"].options = []
        rd = asyncio.run(options.get_option_chain({"ticker": "ZZZZ"}, "u"))
        check("no_options_for_ticker", rd.get("error") == "no_options_for_ticker")

        # ------------------------------------------------------------ C
        print("\nC. honest error on a raising provider (no silent mock)")

        def _boom(*a, **k):
            raise RuntimeError("network exploded")

        made["yf"].Ticker = _boom
        rc = asyncio.run(options.get_option_chain({"ticker": "NVDA"}, "u"))
        check("source yfinance_options_error", rc.get("source") == "yfinance_options_error")
        check("carries error + is NOT mock",
              rc.get("error") == "yfinance_options_failed" and not rc.get("is_mock"))
    finally:
        _uninstall_fake_yf()
        os.environ["USE_MOCK_OPTIONS"] = "1"

    # ---------------------------------------------------------------- F
    print("\nF. registration")
    check("get_option_chain registered", "get_option_chain" in tools.TOOL_REGISTRY)
    check("tool count is 19 (18 + options)", len(tools.TOOL_REGISTRY) == 19,
          str(len(tools.TOOL_REGISTRY)))
    check("thought_template drops the {ticker} placeholder in",
          "{ticker}" in tools.TOOL_REGISTRY["get_option_chain"]["thought_template"])

    # ---------------------------------------------------------------- G
    print("\nG. prompt carve-out present in system.md")
    sm = open(os.path.join(BACKEND, "prompts", "system.md")).read()
    check("options routing row", "get_option_chain" in sm and "options chain" in sm.lower())
    check("markdown-table exception carved", "ONE exception" in sm or "one markdown-table exception" in sm.lower())
    check("no-Greeks honesty rule", "No Greeks" in sm or "greeks aren't available" in sm.lower())


# --- fake yfinance plumbing ----------------------------------------------------
_REAL_YF = None


class _FakeDF:
    """Minimal stand-in for a pandas DataFrame: only `.to_dict('records')` is used."""
    def __init__(self, records):
        self._records = records

    def to_dict(self, _how):
        return list(self._records)


def _install_fake_yf(options_mod, iv_fraction: float):
    global _REAL_YF
    _REAL_YF = sys.modules.get("yfinance")

    def _mk_rows(base):
        return [
            {"strike": base + i * 5, "lastPrice": 10.0 + i, "bid": 9.5 + i, "ask": 10.5 + i,
             "volume": 100 + i, "openInterest": 500 + i, "impliedVolatility": iv_fraction,
             "inTheMoney": i < 3}
            for i in range(8)
        ]

    class _Chain:
        calls = _FakeDF(_mk_rows(900))
        puts = _FakeDF(_mk_rows(900))
        underlying = {"regularMarketPrice": 915.0}

    class _Ticker:
        options = ["2026-08-21", "2026-09-18"]
        fast_info = {"last_price": 915.0}

        def __init__(self, *a, **k):
            pass

        def option_chain(self, _exp):
            return _Chain()

    fake = types.ModuleType("yfinance")
    fake.Ticker = _Ticker
    sys.modules["yfinance"] = fake
    # options.py does `import yfinance as yf` lazily inside _fetch_chain, so replacing
    # the module in sys.modules is enough. _use_mock() also imports yfinance → available.
    return {"yf": fake, "ticker": _Ticker}


def _uninstall_fake_yf():
    if _REAL_YF is not None:
        sys.modules["yfinance"] = _REAL_YF
    else:
        sys.modules.pop("yfinance", None)


def main() -> None:
    backup = tempfile.mkdtemp(prefix="077-backup-")
    try:
        apply_proposal(backup)
        run()
    finally:
        restore(backup)
        shutil.rmtree(backup, ignore_errors=True)

    total, ok = len(results), sum(results)
    print(f"\n{'=' * 60}\n  {ok}/{total} checks passed\n{'=' * 60}")
    print("Live tree restored — confirm with: git status --short")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
