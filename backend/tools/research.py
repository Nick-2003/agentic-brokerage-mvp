"""Research tools — fundamentals, consensus targets, full thesis data.

Two paths per tool, mock-first (PRIORITIES.md P2b):
  - USE_MOCK_RESEARCH=1 OR no FMP_API_KEY → hand-tuned mock for 7 demo tickers
    (NVDA/AAPL/MSFT/TSLA/AMD/GOOGL/TCEHY), matching the demo HTML.
  - else → real Financial Modeling Prep (FMP) data for ANY US ticker, via
    backend.fmp_client. On failure, surface `fmp_fetch_failed` (no silent mock).

Key nuance (get_full_research): the mock returns a *finished* thesis/catalysts/
risks (someone hand-wrote them). FMP has no written thesis, so the real path
returns the *raw material* (rating, target, valuation, fundamentals, sector,
recent filings) and the agent synthesises thesis/catalysts/risks itself — see
the matching rule in backend/prompts/system.md.
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import ToolDef, register

# Top-level sibling module (backend/ is NOT a package — the app runs as
# `uvicorn main:app` with backend/ on sys.path in both dev and Docker/Railway).
# So this is `from fmp_client`, NOT `from backend.fmp_client`. (The latter
# raises ModuleNotFoundError — see proposal 007, which fixes the same mistake
# in the applied technicals.py.)
from fmp_client import (  # noqa: E402
    FMPError,
    financial_growth,
    fmp_enabled,
    grades_consensus,
    price_target_consensus,
    profile,
    ratios_ttm,
    sec_filings,
    stock_peers,
)

# ---------------------------------------------------------------------------
# Per-ticker hand-tuned research data (mock path). Unchanged — the deterministic
# demo for the 7 names. Real (FMP) path covers everything else.
# ---------------------------------------------------------------------------

RESEARCH: dict[str, dict[str, Any]] = {
    "NVDA": {
        "rating": "BUY",
        "target_price": 1100,
        "horizon_months": 12,
        "thesis": (
            "NVIDIA remains the dominant compute platform for AI training and "
            "inference. Data-center revenue compounding at <strong>200%+ YoY</strong>, "
            "anchored by H100/H200 demand and an accelerating Blackwell ramp. The moat "
            "extends well beyond silicon — CUDA, NVLink switching, and Spectrum-X "
            "networking raise switching costs meaningfully. At <strong>38x FY25E EPS</strong> "
            "against a 60%+ EPS CAGR through FY27, the setup is asymmetric."
        ),
        "catalysts": [
            "<strong>Blackwell ramp</strong> — channel checks: GB200 shipping 8 weeks ahead of plan",
            "<strong>Sovereign AI</strong> — Saudi PIF, UAE, India multi-year deals add $15–20B TAM",
            "<strong>Networking attach</strong> — Spectrum-X + Mellanox approaching $10B run-rate, +35%",
            "<strong>Software monetisation</strong> — NIM, AI Enterprise, Omniverse near $2B ARR",
        ],
        "risks": [
            "<strong>China export controls</strong> — additional restrictions could remove $8–12B revenue",
            "<strong>Customer concentration</strong> — top 3 hyperscalers ≈ 40% of data-center revenue",
            "<strong>Custom silicon</strong> — TPU/Trainium/MTIA gain inference share over time",
            "<strong>AI capex digestion</strong> — risk of hyperscaler inventory build in H2 FY26",
        ],
        "valuation": {"pe_fy25e": 38.0, "ev_ebitda": 32.4, "peg_ratio": 0.45, "ev_sales": 18.6},
        "fundamentals": {"revenue_growth_pct": 95, "gross_margin_pct": 76.4, "op_margin_pct": 63.1, "fcf_margin_pct": 49},
        "sector": "Semis",
    },
    "AAPL": {
        "rating": "BUY",
        "target_price": 260,
        "horizon_months": 12,
        "thesis": (
            "Apple is in the early innings of an iPhone <strong>AI super-cycle</strong>. "
            "Services growing at <strong>14% YoY</strong> at <strong>74%</strong> gross "
            "margins. 5-year revenue CAGR of ~8% with operating leverage supports a premium "
            "multiple."
        ),
        "catalysts": [
            "<strong>iPhone 17 cycle</strong> — channel checks point to record Q4 shipments",
            "<strong>Services run-rate</strong> — crossed $100B annual, still accelerating",
            "<strong>Vision Pro 2</strong> — enterprise rollout begins H2 FY25",
        ],
        "risks": [
            "<strong>China demand</strong> — local OEM share gains continue",
            "<strong>Regulation</strong> — App Store fee structure under global attack",
            "<strong>AI lag</strong> — partnerships only; no in-house frontier model",
        ],
        "valuation": {"pe_fy25e": 32.1, "ev_ebitda": 23.5, "peg_ratio": 2.85, "ev_sales": 8.4},
        "fundamentals": {"revenue_growth_pct": 8, "gross_margin_pct": 46.2, "op_margin_pct": 31.8, "fcf_margin_pct": 28},
        "sector": "Large-cap tech",
    },
    "MSFT": {
        "rating": "BUY",
        "target_price": 510,
        "horizon_months": 12,
        "thesis": (
            "Microsoft is the <strong>enterprise AI platform</strong> winner. Azure OpenAI "
            "revenue at a <strong>$10B run-rate</strong>, growing 280% YoY. Copilot bundling "
            "into Office drives ~$30/seat ARPU lift over 3 years."
        ),
        "catalysts": [
            "<strong>Azure AI</strong> — $10B run-rate, growing 280% YoY",
            "<strong>Copilot bundle</strong> — 50M enterprise seats and accelerating",
            "<strong>Activision integration</strong> — gaming margin expansion",
        ],
        "risks": [
            "<strong>EU regulation</strong> — OpenAI partnership probe",
            "<strong>Capex intensity</strong> — $80B+ AI infra spend pressures FCF",
            "<strong>OpenAI dependency</strong> — strategic-partner concentration",
        ],
        "valuation": {"pe_fy25e": 36.4, "ev_ebitda": 24.1, "peg_ratio": 1.95, "ev_sales": 12.2},
        "fundamentals": {"revenue_growth_pct": 16, "gross_margin_pct": 69.8, "op_margin_pct": 44.5, "fcf_margin_pct": 30},
        "sector": "Large-cap tech",
    },
    "TSLA": {
        "rating": "HOLD",
        "target_price": 240,
        "horizon_months": 12,
        "thesis": (
            "Tesla's auto business faces margin pressure from price cuts, but "
            "<strong>FSD + Robotaxi</strong> optionality could re-rate the story. "
            "<strong>Energy storage</strong> growing at <strong>120% YoY</strong> and now "
            "meaningfully contributing to gross profit."
        ),
        "catalysts": [
            "<strong>Robotaxi event</strong> — August demo with unsupervised FSD",
            "<strong>Energy storage</strong> — 120% YoY, margin-accretive",
            "<strong>Optimus</strong> — internal deployment by FY25 year-end",
        ],
        "risks": [
            "<strong>Auto margins</strong> — sub-20% gross margin compression",
            "<strong>FSD timeline</strong> — perpetual \"next year\" promises",
            "<strong>Key-person risk</strong> — Musk attention across X, xAI, Tesla",
        ],
        "valuation": {"pe_fy25e": 62.3, "ev_ebitda": 38.4, "peg_ratio": 4.20, "ev_sales": 6.2},
        "fundamentals": {"revenue_growth_pct": 12, "gross_margin_pct": 17.8, "op_margin_pct": 8.2, "fcf_margin_pct": 5},
        "sector": "Auto/EV",
    },
    "AMD": {
        "rating": "BUY",
        "target_price": 220,
        "horizon_months": 12,
        "thesis": (
            "AMD is the <strong>credible #2 AI accelerator</strong> with MI300 ramping "
            "faster than expected. Server CPU share continues to take from Intel (now "
            "<strong>33% units</strong>). Below-NVDA multiple gives asymmetric setup if AI "
            "mix grows."
        ),
        "catalysts": [
            "<strong>MI325 ramp</strong> — production samples to hyperscalers",
            "<strong>Server CPU share</strong> — 33% units, still gaining vs Intel",
            "<strong>ZT Systems acquisition</strong> — full-stack AI offering",
        ],
        "risks": [
            "<strong>NVDA gap</strong> — CUDA moat hard to close",
            "<strong>Gaming weakness</strong> — console cycle past peak",
            "<strong>Inventory build</strong> — channel risk in client CPU",
        ],
        "valuation": {"pe_fy25e": 48.0, "ev_ebitda": 28.7, "peg_ratio": 1.20, "ev_sales": 11.2},
        "fundamentals": {"revenue_growth_pct": 14, "gross_margin_pct": 53.2, "op_margin_pct": 24.0, "fcf_margin_pct": 18},
        "sector": "Semis",
    },
    "GOOGL": {
        "rating": "BUY",
        "target_price": 215,
        "horizon_months": 12,
        "thesis": (
            "Search remains durable despite AI overhang; Gemini integration is improving "
            "query quality without breaking economics. <strong>Cloud at 28% growth</strong> "
            "and <strong>Waymo</strong> optionality underappreciated."
        ),
        "catalysts": [
            "<strong>Cloud growth</strong> — 28% YoY, margin inflection",
            "<strong>Waymo</strong> — Phoenix/SF rides up 5× YoY",
            "<strong>YouTube</strong> — Shorts monetisation unlock",
        ],
        "risks": [
            "<strong>Search disruption</strong> — long-term threat from AI-first search",
            "<strong>Antitrust</strong> — DOJ search ruling remedies pending",
            "<strong>Capex</strong> — AI infra spend running ahead of revenue",
        ],
        "valuation": {"pe_fy25e": 24.5, "ev_ebitda": 16.8, "peg_ratio": 1.55, "ev_sales": 6.1},
        "fundamentals": {"revenue_growth_pct": 15, "gross_margin_pct": 57.0, "op_margin_pct": 33.5, "fcf_margin_pct": 22},
        "sector": "Large-cap tech",
    },
    "TCEHY": {
        "rating": "BUY",
        "target_price": 72,
        "horizon_months": 12,
        "thesis": (
            "Tencent is the dominant China internet platform — WeChat's <strong>1.3B+ MAU</strong> "
            "anchors a super-app moat across social, payments, and mini-programs. Games are "
            "re-accelerating on a stronger release slate, while high-margin advertising "
            "(Video Accounts) and business services keep lifting the margin mix. The listed "
            "stake portfolio adds optionality the market underprices. At <strong>~16x forward EPS</strong> "
            "it trades at a discount to global large-cap internet peers."
        ),
        "catalysts": [
            "<strong>Games recovery</strong> — domestic + international titles back to growth",
            "<strong>Video Accounts</strong> — under-monetised ad inventory ramping fast",
            "<strong>Margin mix</strong> — high-margin ads & SaaS outgrowing payments",
            "<strong>Buybacks</strong> — sustained HK$100B+ annual repurchase program",
        ],
        "risks": [
            "<strong>China regulation</strong> — gaming approvals and platform rules can shift",
            "<strong>Macro / consumer</strong> — China spending recovery remains uneven",
            "<strong>ADR / geopolitical</strong> — listing and capital-flow risk for US holders",
            "<strong>FX</strong> — RMB weakness translates against a USD-reported book",
        ],
        "valuation": {"pe_fy25e": 16.0, "ev_ebitda": 11.8, "peg_ratio": 1.05, "ev_sales": 4.3},
        "fundamentals": {"revenue_growth_pct": 11, "gross_margin_pct": 53.0, "op_margin_pct": 35.2, "fcf_margin_pct": 27},
        "sector": "Large-cap tech",
    },
}

_PEER_SET = {
    "Semis": ["NVDA", "AMD", "AVGO", "TSM"],
    "Large-cap tech": ["AAPL", "MSFT", "GOOGL", "META"],
    "Auto/EV": ["TSLA", "F", "GM"],
}


# ---------------------------------------------------------------------------
# FMP real-path builders — each composes our return shape from FMP endpoints,
# fanning out concurrent calls. Raise FMPError on failure (caller surfaces it).
# ---------------------------------------------------------------------------


async def _fmp_fundamentals(ticker: str) -> dict[str, Any]:
    prof, rat, growth, cons, grades = await asyncio.gather(
        profile(ticker), ratios_ttm(ticker), financial_growth(ticker),
        price_target_consensus(ticker), grades_consensus(ticker),
    )
    return {
        "ticker": ticker,
        "rating": grades["consensus_rating"],
        "target_price": cons["mean_target"],
        "current_price": prof["price"],  # Proposal 008 — FMP profile price; robust to yfinance being down
        "horizon_months": 12,  # FMP consensus targets are ~12-month
        "fundamentals": {
            "revenue_growth_pct": growth["revenue_growth_pct"],
            "gross_margin_pct": rat["gross_margin_pct"],
            "op_margin_pct": rat["op_margin_pct"],
            "fcf_margin_pct": rat["fcf_margin_pct"],
        },
        "valuation": {
            "pe_fy25e": rat["pe"],
            "ev_ebitda": rat["ev_ebitda"],
            "peg_ratio": rat["peg"],
            "ev_sales": rat["ev_sales"],
        },
        "sector": prof["sector"],
        "is_mock": False,
        "sources": [
            {"name": "FMP — ratios (TTM)"},
            {"name": "FMP — analyst consensus"},
            {"name": "FMP — company profile"},
        ],
    }


async def _fmp_consensus(ticker: str) -> dict[str, Any]:
    cons, grades = await asyncio.gather(
        price_target_consensus(ticker), grades_consensus(ticker),
    )
    return {
        "ticker": ticker,
        "consensus_rating": grades["consensus_rating"],
        "high_target": cons["high_target"],
        "median_target": cons["median_target"],
        "low_target": cons["low_target"],
        "n_analysts": grades["n_analysts"],
        "distribution": grades["distribution"],
        # FMP's stable consensus endpoint doesn't expose 30-day upgrade/downgrade
        # counts cleanly — omit rather than fabricate (trust principle #5).
        "is_mock": False,
        "sources": [{"name": "FMP — price target consensus"}, {"name": "FMP — analyst grades"}],
    }


async def _fmp_full_research(ticker: str) -> dict[str, Any]:
    """Real path returns RAW FACTS, not a written thesis.

    No `thesis` / `catalysts` / `risks` keys — the agent synthesises those from
    these facts + filings (see system.md). This is the deliberate difference
    from the mock, which returns finished prose.
    """
    prof, rat, growth, cons, grades, filings = await asyncio.gather(
        profile(ticker), ratios_ttm(ticker), financial_growth(ticker),
        price_target_consensus(ticker), grades_consensus(ticker), sec_filings(ticker, limit=3),
    )
    return {
        "ticker": ticker,
        "company_name": prof["company_name"],
        "rating": grades["consensus_rating"],
        "target_price": cons["mean_target"],
        "current_price": prof["price"],  # Proposal 008 — FMP profile price; robust to yfinance being down
        "horizon_months": 12,
        "valuation": {
            "pe_fy25e": rat["pe"],
            "ev_ebitda": rat["ev_ebitda"],
            "peg_ratio": rat["peg"],
            "ev_sales": rat["ev_sales"],
        },
        "fundamentals": {
            "revenue_growth_pct": growth["revenue_growth_pct"],
            "gross_margin_pct": rat["gross_margin_pct"],
            "op_margin_pct": rat["op_margin_pct"],
            "fcf_margin_pct": rat["fcf_margin_pct"],
        },
        "sector": prof["sector"],
        "analyst_distribution": grades["distribution"],
        "n_analysts": grades["n_analysts"],
        "recent_filings": filings,
        # NOTE: no thesis/catalysts/risks — agent writes them from these facts.
        "needs_synthesis": True,
        "is_mock": False,
        "sources": [
            {"name": "FMP — consensus + ratios + profile"},
            {"name": "SEC EDGAR (via FMP) — recent filings"},
        ],
    }


async def _fmp_peers(ticker: str) -> dict[str, Any]:
    prof = await profile(ticker)
    sector = prof["sector"]
    peers = await stock_peers(ticker)
    if not peers:
        # Fall back to the static sector map (editorial peer choice).
        peers = [p for p in _PEER_SET.get(sector, []) if p != ticker]
    peers = peers[:4]

    async def _peer_row(p: str) -> dict[str, Any] | None:
        try:
            rat, grades = await asyncio.gather(ratios_ttm(p), grades_consensus(p))
        except FMPError:
            return None
        return {
            "ticker": p,
            "pe_fy25e": rat["pe"],
            "ev_ebitda": rat["ev_ebitda"],
            "peg_ratio": rat["peg"],
            "ev_sales": rat["ev_sales"],
            "rating": grades["consensus_rating"],
        }

    rows = [r for r in await asyncio.gather(*[_peer_row(p) for p in peers]) if r]
    return {"sector": sector, "anchor": ticker, "peers": rows, "is_mock": False,
            "sources": [{"name": "FMP — stock peers + ratios"}]}


# ---------------------------------------------------------------------------
# Tools (mock-first dispatch)
# ---------------------------------------------------------------------------


async def get_company_fundamentals(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Pull a single ticker's financial snapshot (FMP real, or mock for the 7)."""
    ticker = (args.get("ticker") or "").upper()
    if fmp_enabled():
        try:
            return await _fmp_fundamentals(ticker)
        except FMPError as e:
            return {"error": e.code, "ticker": ticker, "message": str(e)}
    r = RESEARCH.get(ticker)
    if not r:
        return {"error": "no_coverage", "ticker": ticker, "message": f"No coverage data for {ticker} yet."}
    return {
        "ticker": ticker,
        "rating": r["rating"],
        "target_price": r["target_price"],
        "horizon_months": r["horizon_months"],
        "fundamentals": r["fundamentals"],
        "valuation": r["valuation"],
        "sector": r["sector"],
        "is_mock": True,
    }


async def get_consensus_targets(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Get the sell-side consensus picture for a ticker (FMP real, or mock)."""
    ticker = (args.get("ticker") or "").upper()
    if fmp_enabled():
        try:
            return await _fmp_consensus(ticker)
        except FMPError as e:
            return {"error": e.code, "ticker": ticker, "message": str(e)}
    r = RESEARCH.get(ticker)
    if not r:
        return {"error": "no_coverage", "ticker": ticker}
    target = r["target_price"]
    return {
        "ticker": ticker,
        "consensus_rating": r["rating"],
        "high_target": int(target * 1.15),
        "median_target": target,
        "low_target": int(target * 0.85),
        "n_analysts": 38,
        "upgrades_last_30d": 4,
        "downgrades_last_30d": 1,
        "is_mock": True,
    }


async def get_full_research(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Full research package in one call.

    Mock: finished thesis + catalysts + risks + valuation (hand-written prose).
    Real (FMP): RAW FACTS only (`needs_synthesis: true`) — the agent writes the
    thesis/catalysts/risks from the facts + recent filings (see system.md).
    """
    ticker = (args.get("ticker") or "").upper()
    if fmp_enabled():
        try:
            return await _fmp_full_research(ticker)
        except FMPError as e:
            return {"error": e.code, "ticker": ticker, "message": str(e)}
    r = RESEARCH.get(ticker)
    if not r:
        return {"error": "no_coverage", "ticker": ticker, "message": f"No coverage for {ticker} yet."}
    return {"ticker": ticker, **r, "is_mock": True}


async def get_peer_set(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Return the peer set for a ticker, with valuation data for each peer."""
    ticker = (args.get("ticker") or "").upper()
    if fmp_enabled():
        try:
            return await _fmp_peers(ticker)
        except FMPError as e:
            return {"error": e.code, "ticker": ticker, "message": str(e)}
    r = RESEARCH.get(ticker)
    if not r:
        return {"error": "no_coverage", "ticker": ticker}
    sector = r["sector"]
    peers = _PEER_SET.get(sector, [])
    peer_data = []
    for p in peers:
        if p == ticker:
            continue
        pr = RESEARCH.get(p)
        if pr:
            peer_data.append({"ticker": p, **pr["valuation"], "rating": pr["rating"]})
    return {"sector": sector, "anchor": ticker, "peers": peer_data, "is_mock": True}


# ---------------------------------------------------------------------------
# Registration (unchanged signatures — only the data source behind them changed)
# ---------------------------------------------------------------------------

register(
    ToolDef(
        name="get_company_fundamentals",
        description=(
            "Get financial snapshot for one ticker: rating, target price, valuation "
            "multiples (P/E, EV/EBITDA, PEG), growth, margins, sector. Use this when "
            "you need numeric financials for a name."
        ),
        input_schema={
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Ticker symbol"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=get_company_fundamentals,
        thought_template="Pulling {ticker} fundamentals and consensus",
    )
)

register(
    ToolDef(
        name="get_consensus_targets",
        description=(
            "Get sell-side consensus for a ticker: rating, target price distribution "
            "(high/median/low), analyst count, recent upgrades/downgrades."
        ),
        input_schema={
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=get_consensus_targets,
        thought_template="Checking analyst targets and rating changes for {ticker}",
    )
)

register(
    ToolDef(
        name="get_full_research",
        description=(
            "Get the full research package for one ticker in a single call: rating, "
            "target, thesis paragraph, list of catalysts, list of risks, valuation "
            "multiples. Prefer this over multiple smaller calls when building a "
            "research_card widget."
        ),
        input_schema={
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=get_full_research,
        thought_template="Synthesising the investment thesis for {ticker}",
    )
)

register(
    ToolDef(
        name="get_peer_set",
        description=(
            "Get peer tickers in the same sector as the anchor, with their valuation "
            "data. Use this for compare-to-AMD or peer-comp widgets."
        ),
        input_schema={
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=get_peer_set,
        thought_template="Comparing {ticker} vs peer set",
    )
)
