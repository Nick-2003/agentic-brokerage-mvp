#!/usr/bin/env python3
"""P2b regression test — real FMP research data (proposals 006 + 008 + 009).

One-command check that the research tools return real FMP data with the
field-mapping + price fixes intact. Mirrors scripts/test_P1_003.py in style
(check/PASS-FAIL tally rather than fail-fast asserts).

Run from anywhere (resolves backend/ + backend/.env relative to itself):

    FMP_API_KEY=<key> backend/.venv/bin/python scripts/test_P2_006.py
    # or rely on backend/.env having FMP_API_KEY + USE_MOCK_RESEARCH=0:
    backend/.venv/bin/python scripts/test_P2_006.py

Behaviour:
  - FMP enabled (key present, USE_MOCK_RESEARCH != "1") → runs the live
    real-data + 006/008/009 invariant checks (~20 FMP requests for AAPL;
    free tier ~250/day, so run sparingly).
  - FMP not enabled → skips the live checks, runs only the mock-first gating
    checks (no network, no key needed).

Covers:
  006 — research tools return real data (is_mock:false); get_full_research real
        path returns RAW FACTS (no pre-written thesis/catalysts) → agent synthesises.
  008 — get_full_research / get_company_fundamentals carry a numeric current_price.
  009 — fcf_margin_pct derived (not None); ev_sales is true EV/Sales, distinct
        from P/S (ps_ratio); both numeric.
  mock-first — USE_MOCK_RESEARCH=1 forces the hand-tuned 7-ticker path.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

# Anchor on this file so cwd doesn't matter: scripts/ -> ../backend
_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "backend")
sys.path.insert(0, _BACKEND)
load_dotenv(os.path.join(_BACKEND, ".env"))  # FMP_API_KEY, USE_MOCK_RESEARCH

import fmp_client as fc  # noqa: E402
from tools.research import (  # noqa: E402
    get_company_fundamentals,
    get_consensus_targets,
    get_full_research,
    get_peer_set,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


async def real_data_checks(sym: str = "AAPL") -> None:
    print(f"\n=== 006/008/009 live checks ({sym}) ===")

    # 009 — data layer: fcf_margin derived, ev_sales != P/S
    rat = await fc.ratios_ttm(sym)
    check("009 fcf_margin_pct derived (not None)", _isnum(rat.get("fcf_margin_pct")), f"fcf={rat.get('fcf_margin_pct')}")
    check("009 ev_sales numeric", _isnum(rat.get("ev_sales")), f"ev_sales={rat.get('ev_sales')}")
    check("009 ev_sales != ps_ratio (not mislabelled P/S)",
          _isnum(rat.get("ps_ratio")) and rat.get("ev_sales") != rat.get("ps_ratio"),
          f"ev_sales={rat.get('ev_sales')} ps_ratio={rat.get('ps_ratio')}")
    check("ev_ebitda/pe/peg numeric", all(_isnum(rat.get(k)) for k in ("ev_ebitda", "pe", "peg")))

    # 006 — get_full_research real path: raw facts, no pre-written narrative
    fr = await get_full_research({"ticker": sym}, "demo")
    check("006 get_full_research is_mock=false", fr.get("is_mock") is False, f"error={fr.get('error')}")
    check("006 needs_synthesis=true", fr.get("needs_synthesis") is True)
    check("006 real path has NO pre-written thesis/catalysts/risks",
          all(k not in fr for k in ("thesis", "catalysts", "risks")))
    # 008 — current_price present on the research package
    check("008 get_full_research current_price numeric", _isnum(fr.get("current_price")), f"px={fr.get('current_price')}")
    check("rating in BUY/HOLD/SELL", fr.get("rating") in {"BUY", "HOLD", "SELL"}, f"rating={fr.get('rating')}")
    check("target_price numeric", _isnum(fr.get("target_price")))

    # 008 — fundamentals current_price + 009 fcf in fundamentals
    cf = await get_company_fundamentals({"ticker": sym}, "demo")
    check("006 get_company_fundamentals is_mock=false", cf.get("is_mock") is False)
    check("008 fundamentals current_price numeric", _isnum(cf.get("current_price")), f"px={cf.get('current_price')}")
    check("009 fundamentals.fcf_margin_pct present", _isnum((cf.get("fundamentals") or {}).get("fcf_margin_pct")))

    # consensus
    ct = await get_consensus_targets({"ticker": sym}, "demo")
    check("006 consensus is_mock=false", ct.get("is_mock") is False)
    check("consensus n_analysts > 0", _isnum(ct.get("n_analysts")) and ct["n_analysts"] > 0, f"n={ct.get('n_analysts')}")
    hi, md, lo = ct.get("high_target"), ct.get("median_target"), ct.get("low_target")
    check("consensus high>=median>=low", _isnum(hi) and _isnum(md) and _isnum(lo) and hi >= md >= lo, f"{lo}/{md}/{hi}")

    # peers
    ps = await get_peer_set({"ticker": sym}, "demo")
    check("006 peer_set is_mock=false + non-empty", ps.get("is_mock") is False and bool(ps.get("peers")),
          f"peers={[p.get('ticker') for p in (ps.get('peers') or [])]}")


async def mock_gating_checks() -> None:
    print("\n=== mock-first gating (no network) ===")
    prev = os.environ.get("USE_MOCK_RESEARCH")
    os.environ["USE_MOCK_RESEARCH"] = "1"
    try:
        check("USE_MOCK_RESEARCH=1 → fmp_enabled() False", fc.fmp_enabled() is False)
        fr = await get_full_research({"ticker": "AAPL"}, "demo")  # AAPL is one of the 7 mocks
        check("mock get_full_research(AAPL) is_mock=true", fr.get("is_mock") is True, f"is_mock={fr.get('is_mock')}")
        check("mock path DOES carry a pre-written thesis", "thesis" in fr)
        cov = await get_full_research({"ticker": "ZZZZ"}, "demo")  # not covered in mock
        check("mock get_full_research(ZZZZ) → no_coverage", cov.get("error") == "no_coverage")
    finally:
        if prev is None:
            os.environ.pop("USE_MOCK_RESEARCH", None)
        else:
            os.environ["USE_MOCK_RESEARCH"] = prev


async def main() -> None:
    print("P2b verification — proposals 006 + 008 + 009")
    if fc.fmp_enabled():
        await real_data_checks(sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL")
    else:
        print(f"\n  [{SKIP}] live FMP checks — fmp_enabled() is False "
              "(set FMP_API_KEY, leave USE_MOCK_RESEARCH unset, to run them).")
    await mock_gating_checks()
    print(f"\n{'='*52}")
    ok = all(results)
    print(f"RESULT: {sum(results)}/{len(results)} checks passed",
          "— ALL PASS ✅" if ok else "— SOME FAILED ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
