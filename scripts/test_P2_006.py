#!/usr/bin/env python3
"""Layer-2 real-data test for Proposal 006 (FMP). Needs FMP_API_KEY.
Run from repo root: FMP_API_KEY=... backend/.venv/bin/python scripts/test_P2_006.py AAPL"""
import asyncio, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "backend"))
from tools import research  # noqa: E402
import fmp_client  # noqa: E402

async def main(sym):
    if not os.getenv("FMP_API_KEY"):
        sys.exit("set FMP_API_KEY first")
    os.environ.pop("USE_MOCK_RESEARCH", None)          # force real path
    assert fmp_client.fmp_enabled(), "fmp_enabled() false — key missing/placeholder"

    f = await research.get_company_fundamentals({"ticker": sym}, "demo")
    assert f.get("is_mock") is False and not f.get("error"), f
    print(f"fundamentals: rating={f['rating']} target={f['target_price']} sector={f['sector']} pe={f['valuation']['pe_fy25e']}")

    c = await research.get_consensus_targets({"ticker": sym}, "demo")
    assert c.get("is_mock") is False and not c.get("error"), c
    print(f"consensus: {c['consensus_rating']} {c['low_target']}/{c['median_target']}/{c['high_target']} n={c['n_analysts']}")

    r = await research.get_full_research({"ticker": sym}, "demo")
    assert r.get("is_mock") is False and r.get("needs_synthesis") is True, r
    assert "thesis" not in r and "catalysts" not in r, "real path must NOT pre-write thesis"
    print(f"full_research: raw facts ✓  filings={len(r.get('recent_filings', []))}  (agent synthesises thesis)")

    p = await research.get_peer_set({"ticker": sym}, "demo")
    assert p.get("is_mock") is False and not p.get("error"), p
    print(f"peers: {[x['ticker'] for x in p['peers']]}")
    print("\nLAYER 2 PASS ✅")

asyncio.run(main((sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()))