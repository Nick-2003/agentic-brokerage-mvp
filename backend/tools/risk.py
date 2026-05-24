"""Portfolio risk computation — concentration, sector exposure, correlation flags.

Hand-tuned mock matching the demo's risk audit numbers. Real implementation
pulls live positions, computes sector mix from a static ticker→sector map,
runs correlation via tools/technicals.get_correlation_matrix.
"""

from __future__ import annotations

from typing import Any

from . import ToolDef, register

# Static ticker → sector map. Production: pull from a real classifier.
TICKER_SECTOR = {
    "NVDA": "Tech / Semis", "AMD": "Tech / Semis", "AVGO": "Tech / Semis",
    "TSM": "Tech / Semis", "INTC": "Tech / Semis", "QCOM": "Tech / Semis",
    "AAPL": "Tech / Large-cap", "MSFT": "Tech / Large-cap", "GOOGL": "Tech / Large-cap",
    "META": "Tech / Large-cap", "AMZN": "Tech / Large-cap",
    "TSLA": "Auto / EV", "F": "Auto / EV", "GM": "Auto / EV",
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials",
    "XOM": "Energy", "CVX": "Energy",
    "JNJ": "Healthcare", "PFE": "Healthcare",
}


async def compute_portfolio_risk(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Compute risk score, sector exposure, concentration flags, and suggestions
    for a given portfolio.

    Expected args:
        positions: list of {ticker, market_value}
        total_equity: number (excluding cash unless you want to include it)
        cash: number
    """
    positions = args.get("positions") or []
    cash = float(args.get("cash") or 0)
    total = float(args.get("total_equity") or 0) or (
        sum(float(p.get("market_value", 0)) for p in positions) + cash
    )
    if total <= 0:
        return {"error": "no_equity"}

    # Sector exposure
    sector_totals: dict[str, float] = {}
    for p in positions:
        sec = TICKER_SECTOR.get(p["ticker"].upper(), "Other")
        sector_totals[sec] = sector_totals.get(sec, 0) + float(p.get("market_value", 0))
    sector_totals["Cash"] = cash

    sector_exposure = []
    for label, val in sorted(sector_totals.items(), key=lambda x: -x[1]):
        pct = round(val / total * 100, 1)
        sev = "danger" if pct >= 60 else ("warn" if pct < 10 and label == "Cash" else "normal")
        sector_exposure.append({"label": label, "pct": pct, "severity": sev})

    # Top concentration
    top_3_pct = sum(
        sorted(
            [float(p.get("market_value", 0)) for p in positions],
            reverse=True,
        )[:3]
    ) / total * 100

    # Flags
    flags = []
    semi_pct = sector_totals.get("Tech / Semis", 0) / total * 100
    largecap_pct = sector_totals.get("Tech / Large-cap", 0) / total * 100
    tech_pct = semi_pct + largecap_pct
    if tech_pct > 50:
        flags.append({
            "severity": "high",
            "title": "Single-sector concentration",
            "detail_html": f"<strong>{tech_pct:.0f}%</strong> of your book sits in tech. A sector-wide drawdown of 15% would erase 18 months of gains.",
        })
    if top_3_pct > 50:
        names = sorted(positions, key=lambda p: -float(p.get("market_value", 0)))[:3]
        names_str = " · ".join(f"<strong>{p['ticker']} {float(p.get('market_value',0))/total*100:.0f}%</strong>" for p in names)
        flags.append({
            "severity": "high" if top_3_pct > 60 else "med",
            "title": f"Top-3 names = {top_3_pct:.0f}% of NAV",
            "detail_html": f"{names_str}. A bad print in any one would move your book 4-6%.",
        })
    # Mock AI cluster correlation flag (real version uses tools/technicals.get_correlation_matrix)
    ai_cluster = {p["ticker"].upper() for p in positions} & {"NVDA", "AMD", "MSFT", "GOOGL", "META"}
    if len(ai_cluster) >= 3:
        flags.append({
            "severity": "high",
            "title": "Correlated AI cluster",
            "detail_html": (
                f"{' + '.join(sorted(ai_cluster))} have <strong>0.78 avg 60-day correlation</strong>. "
                "They move together — your \"diversification\" is thinner than it looks."
            ),
        })
    if cash / total < 0.1:
        flags.append({
            "severity": "med",
            "title": "Low cash buffer",
            "detail_html": f"Cash is only <strong>{cash/total*100:.1f}%</strong> of NAV. Limits ability to add on dips.",
        })

    # Score: simple heuristic 1-10
    score = 5.0
    if tech_pct > 60: score += 1.5
    if top_3_pct > 60: score += 1.5
    if len(ai_cluster) >= 3: score += 0.5
    if cash / total < 0.05: score += 0.5
    score = round(min(score, 10), 1)
    risk_label = "Low" if score <= 4 else "Moderate" if score <= 6 else "High" if score <= 8 else "Severe"

    suggestions = []
    if tech_pct > 50:
        suggestions.append(
            "<strong>De-correlate.</strong> Trim NVDA or MSFT by ~5% of NAV and rotate into a non-correlated factor (energy, healthcare, or international)."
        )
    if len(ai_cluster) >= 3:
        suggestions.append(
            "<strong>Add a hedge.</strong> 5% notional in <code>SH</code> or 2% in <code>SOXS</code> would meaningfully cap drawdown in an AI-capex sell-off without crushing upside."
        )
    if top_3_pct > 50:
        suggestions.append(
            "<strong>Cap single-name weight.</strong> Set a 15% per-name ceiling — you're well above what your stated 2%-risk rule supports when accounting for vol."
        )
    if not suggestions:
        suggestions.append("Portfolio looks balanced. Maintain current allocations and re-check after the next earnings cycle.")

    return {
        "risk_score": score,
        "risk_label": risk_label,
        "risk_summary": "Driven by single-sector exposure and correlated AI names." if score >= 7 else "Moderate concentration; no immediate red flags.",
        "sector_exposure": sector_exposure,
        "flags": flags,
        "suggestions": suggestions,
        "is_mock": True,
    }


async def compose_thesis(args: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Build a thesis package combining research + user style.

    Designed to be called AFTER get_full_research so the LLM can have both
    the research findings and the user's trading style on hand. Returns a
    structured thesis blob the agent uses to build a `thesis` widget.
    """
    ticker = (args.get("ticker") or "").upper()
    horizon = args.get("horizon", "18 months")
    weight_pct = float(args.get("weight_pct_nav", 4.0))

    # Pull research data via the research tool's data (deterministic mock)
    from .research import RESEARCH
    r = RESEARCH.get(ticker)
    if not r:
        return {"error": "no_coverage", "ticker": ticker}

    tldr = args.get(
        "tldr_html",
        f"Holding <strong>{ticker}</strong> for {horizon} on the {r['sector']} thesis. "
        f"Exit if any two thesis-breakers fire within 6 months.",
    )

    # Catalysts and breakers come from research; agent can override via args.
    catalysts = args.get("reasons_to_be_in") or r["catalysts"]
    watch_weekly = args.get("what_to_watch_weekly") or [
        f"{ticker} weekly closing levels vs key resistance",
        f"Sector news flow ({r['sector']})",
        "Consensus revisions (upgrades / downgrades)",
        "Macro: 10Y yield, DXY, Fed commentary",
    ]
    breakers = args.get("thesis_breakers") or [
        f"Two consecutive quarters of slowing growth in {r['sector']}",
        f"Material new competitive entrant to {ticker}'s core market",
        "Macro regime shift (rates +100bps surprise)",
    ]

    return {
        "ticker": ticker,
        "rating": r["rating"],
        "horizon": horizon,
        "weight_pct_nav": weight_pct,
        "confidence": "High (7/10)",
        "tldr_html": tldr,
        "reasons_to_be_in": catalysts,
        "what_to_watch_weekly": watch_weekly,
        "thesis_breakers": breakers,
        "is_mock": True,
    }


register(
    ToolDef(
        name="compute_portfolio_risk",
        description=(
            "Compute portfolio risk score, sector exposure breakdown, concentration "
            "flags, and suggested hedges. Pass positions + cash + total_equity from "
            "get_portfolio. Returns data shaped for a portfolio_risk widget."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "market_value": {"type": "number"},
                        },
                        "required": ["ticker", "market_value"],
                    },
                },
                "cash": {"type": "number"},
                "total_equity": {"type": "number"},
            },
            "required": ["positions", "total_equity"],
            "additionalProperties": False,
        },
        callable=compute_portfolio_risk,
        thought_template="Computing sector exposure and 60-day correlation matrix",
    )
)

register(
    ToolDef(
        name="compose_thesis",
        description=(
            "Build a structured thesis for a ticker, combining research findings "
            "with the user's trading style. Returns TLDR, reasons-to-be-in, weekly "
            "watch list, and thesis-breakers. Use this for thesis widgets and "
            "deploy_tracker calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "horizon": {"type": "string", "default": "18 months"},
                "weight_pct_nav": {"type": "number", "default": 4.0},
                "tldr_html": {"type": "string"},
                "reasons_to_be_in": {"type": "array", "items": {"type": "string"}},
                "what_to_watch_weekly": {"type": "array", "items": {"type": "string"}},
                "thesis_breakers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
        callable=compose_thesis,
        thought_template="Drafting your personalised thesis for {ticker}",
    )
)
