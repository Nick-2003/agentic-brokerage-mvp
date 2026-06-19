#!/usr/bin/env python3
"""Offline test for Proposal 053 — guest/demo sample Alpaca portfolio fallback.

Self-contained: temp-applies the proposal's tools/portfolio.py over the live file,
stubs the Alpaca TradingClient (and connections) so there's NO network, runs the
dispatch cases, then restores the live file in a finally.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_053_sample_portfolio.py
"""
import asyncio
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
# Proposal backend dir derived from THIS test's location (robust to the
# .proposed_changes dot-folder and to being applied into scripts/).
PROP_BACKEND = os.path.normpath(os.path.join(HERE, os.pardir, "backend"))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "news_context.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"repo root not found above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
LIVE = os.path.join(BACKEND, "tools", "portfolio.py")
PROP = os.path.join(PROP_BACKEND, "tools", "portfolio.py")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


# --- fake Alpaca SDK (injected into sys.modules so the lazy import picks it up) ---
class _FakeAcct:
    equity = "53000.00"
    last_equity = "52000.00"
    cash = "2000.00"
    buying_power = "4000.00"


class _FakePos:
    def __init__(self, s, q, ac, mv, pl):
        self.symbol, self.qty, self.avg_entry_price, self.market_value, self.unrealized_pl = s, q, ac, mv, pl


class _FakeClient:
    def __init__(self, *a, **k):
        pass
    def get_account(self):
        return _FakeAcct()
    def get_all_positions(self):
        return [_FakePos("NVDA", "8", "880", "7200", "160"),
                _FakePos("AAPL", "20", "190", "4000", "200")]


def _install_fake_alpaca():
    client_mod = types.ModuleType("alpaca.trading.client")
    client_mod.TradingClient = _FakeClient
    sys.modules["alpaca"] = types.ModuleType("alpaca")
    sys.modules["alpaca.trading"] = types.ModuleType("alpaca.trading")
    sys.modules["alpaca.trading.client"] = client_mod


def _set_sample_env(enabled: bool, creds: bool) -> None:
    os.environ["SAMPLE_PORTFOLIO_ENABLED"] = "1" if enabled else "0"
    if creds:
        os.environ["SAMPLE_ALPACA_API_KEY"] = "PKSAMPLE"
        os.environ["SAMPLE_ALPACA_API_SECRET"] = "secret"
    else:
        os.environ.pop("SAMPLE_ALPACA_API_KEY", None)
        os.environ.pop("SAMPLE_ALPACA_API_SECRET", None)


async def run(portfolio):
    _install_fake_alpaca()
    os.environ["PORTFOLIO_SOURCE"] = "ibkr"  # default; the signed-in path

    print("\n=== guest + enabled + creds → LIVE sample (dedicated Alpaca paper) ===")
    portfolio._sample_cache.update(at=0.0, data=None)
    _set_sample_env(enabled=True, creds=True)
    p = await portfolio.get_portfolio({}, "demo")
    check("is_sample True", p.get("is_sample") is True, str(p.get("is_sample")))
    check("source alpaca_sample", p.get("source") == "alpaca_sample")
    check("read_only", p.get("read_only") is True)
    check("equity from fake account", p.get("total_equity") == 53000.0, str(p.get("total_equity")))
    check("day P&L computed (equity − last_equity)", round(p.get("day_pnl"), 2) == 1000.0, str(p.get("day_pnl")))
    check("positions mapped", len(p.get("positions") or []) == 2, str(len(p.get("positions") or [])))
    check("not is_mock", p.get("is_mock") is False)

    print("\n=== guest + enabled + NO creds → static MOCK, tagged sample ===")
    portfolio._sample_cache.update(at=0.0, data=None)
    _set_sample_env(enabled=True, creds=False)
    p = await portfolio.get_portfolio({}, "demo")
    check("is_sample True", p.get("is_sample") is True)
    check("source alpaca_sample", p.get("source") == "alpaca_sample")
    check("equity == MOCK_PORTFOLIO ($51,000)", p.get("total_equity") == 51000.00, str(p.get("total_equity")))

    print("\n=== guest + DISABLED → today's behavior (nil, not sample) ===")
    _set_sample_env(enabled=False, creds=True)  # creds present but flag off
    p = await portfolio.get_portfolio({}, "demo")  # demo → IBKR path → connections short-circuits None → nil
    check("not a sample", not p.get("is_sample"))
    check("connected False (nil)", p.get("connected") is False, str(p.get("connected")))
    check("total_equity None", p.get("total_equity") is None)

    print("\n=== signed-in user is NEVER served the sample (even when enabled) ===")
    import connections
    async def _no_conn(_uid):
        return None
    connections.get_connection_with_token_admin = _no_conn  # stub: no IBKR connection
    _set_sample_env(enabled=True, creds=True)
    p = await portfolio.get_portfolio({}, "a1b2c3d4-uuid")
    check("uuid user not sampled", not p.get("is_sample"), str(p.get("is_sample")))
    check("uuid user → nil (their own IBKR, not connected)", p.get("connected") is False)
    check("uuid user no fabricated equity", p.get("total_equity") is None)

    print("\n=== helper truth table ===")
    _set_sample_env(enabled=True, creds=True)
    check("_sample_enabled True", portfolio._sample_enabled() is True)
    check("_sample_creds present", portfolio._sample_creds() == ("PKSAMPLE", "secret"))
    _set_sample_env(enabled=False, creds=False)
    check("_sample_enabled False", portfolio._sample_enabled() is False)
    check("_sample_creds None", portfolio._sample_creds() is None)

    for k in ("SAMPLE_PORTFOLIO_ENABLED", "SAMPLE_ALPACA_API_KEY", "SAMPLE_ALPACA_API_SECRET", "PORTFOLIO_SOURCE"):
        os.environ.pop(k, None)


def main() -> int:
    backup = None
    try:
        if os.path.isfile(PROP) and os.path.abspath(PROP) != os.path.abspath(LIVE):
            with open(LIVE, "rb") as fh:
                backup = fh.read()
            shutil.copyfile(PROP, LIVE)
        sys.path.insert(0, BACKEND)
        from tools import portfolio  # noqa: E402
        asyncio.run(run(portfolio))
    finally:
        if backup is not None:
            with open(LIVE, "wb") as fh:
                fh.write(backup)

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
