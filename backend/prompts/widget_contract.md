# Widget JSON contract

When your response is a widget, output a SINGLE JSON code block (no surrounding prose) matching ONE of the schemas below. The app validates against these — invalid JSON falls back to a plain text bubble.

Every widget has:
- `type` (string, required) — must be one of the listed types
- `data` (object, required) — schema depends on type
- `sources` (array, required) — list of `{name: string, url?: string}` showing where the numbers came from

## morning_brief

```json
{
  "type": "morning_brief",
  "data": {
    "headline": "Your portfolio is up +1.46% overnight",
    "paragraphs": [
      "Led by **NVDA** (+1.98%) on strong data-center commentary at the AI conference and **TSLA** (+2.57%) on robotaxi headlines; **MSFT** slipped (-0.49%) on regulatory chatter.",
      "S&P futures point +0.4% higher, the **10Y yield** holds at 4.32%, and **DXY** is firm into the Fed minutes this afternoon.",
      "Watch **NVDA** for follow-through above $945 (next resistance $960) and **AMD** ahead of next week's earnings — flagged unusual call volume."
    ]
  },
  "sources": [
    {"name": "Your portfolio"},
    {"name": "Bloomberg"},
    {"name": "CME futures"}
  ]
}
```

## research_card

```json
{
  "type": "research_card",
  "data": {
    "ticker": "NVDA",
    "company_name": "NVIDIA Corp.",
    "current_price": 942.50,
    "currency": "$",
    "rating": "BUY",
    "target_price": 1100,
    "horizon_months": 12,
    "thesis_html": "NVIDIA remains the dominant compute platform for AI training and inference. <strong>Data-center revenue compounding at 200%+ YoY</strong>...",
    "catalysts": [
      "Blackwell ramp — GB200 shipping 8 weeks ahead of plan",
      "Sovereign AI deals adding $15-20B TAM",
      "Networking attach at $10B run-rate"
    ],
    "risks": [
      "China export controls — could remove $8-12B revenue",
      "Customer concentration — top 3 hyperscalers ~40% DC rev",
      "Custom silicon — TPU/Trainium/MTIA gain inference share"
    ]
  },
  "sources": [
    {"name": "NVDA 10-Q"},
    {"name": "Bloomberg"},
    {"name": "FactSet"},
    {"name": "Earnings call"}
  ]
}
```

`rating` must be one of: `BUY`, `HOLD`, `SELL`.

`current_price` is normally a number (from FMP profile or `get_quote`), but **may be `null`** when no live price source is available (e.g. real-market mode with yfinance down and no FMP profile price). The frontend renders `—` and omits the upside figure in that case — never fabricate a price to fill it.

## ta_chart

```json
{
  "type": "ta_chart",
  "data": {
    "ticker": "NVDA",
    "timeframe": "1D",
    "current_price": 942.50,
    "screenshot_url": "https://...supabase.co/storage/.../nvda_1d.png",
    "indicators_applied": ["SMA 50", "SMA 200"],
    "key_levels": {
      "resistance": [960, 998],
      "support": [880, 845]
    },
    "trend_summary_html": "<strong>Trend: bullish.</strong> Price above both SMAs with confirmed golden cross 6 weeks ago. Watch $960 for follow-through."
  },
  "sources": [
    {"name": "TradingView"},
    {"name": "Daily OHLC · 250d"}
  ]
}
```

## order_ticket

```json
{
  "type": "order_ticket",
  "data": {
    "side": "buy",
    "ticker": "NVDA",
    "shares": 10,
    "notional": 9450,
    "limit_price": 945,
    "currency": "$",
    "tp_price": 1100,
    "sl_price": 880,
    "rr_ratio": 2.2,
    "risk_amount": 650,
    "reward_amount": 1550,
    "portfolio_pct": 9.5,
    "within_risk_rule": true,
    "bracket_source": "from_prompt",
    "notes_html": "Sized within your 2% per-trade rule. OCO bracket arms TP and SL together."
  },
  "sources": [
    {"name": "Your 2% rule"},
    {"name": "Research stop"},
    {"name": "12-mo target"}
  ]
}
```

- `side`: `buy` or `sell`
- `bracket_source`: `from_prompt` (user specified TP/SL in their message) or `from_research` (we pulled from the research card)

## live_trade

```json
{
  "type": "live_trade",
  "data": {
    "order_id": "alpaca-order-id-here",
    "ticker": "NVDA",
    "side": "long",
    "shares": 10,
    "fill_price": 945.00,
    "current_price": 947.19,
    "currency": "$",
    "unrealized_pnl": 21.90,
    "unrealized_pnl_pct": 0.23,
    "tp_armed_at": 1100,
    "sl_armed_at": 880,
    "filled_at": "2026-05-19T22:32:00Z",
    "news_since_fill": [
      {"headline": "Goldman raises NVDA price target to $1,200, citing data-center share gains", "source": "Bloomberg", "ts": "2026-05-20T11:14:00Z"}
    ]
  },
  "sources": [
    {"name": "Alpaca paper fill"},
    {"name": "Real-time quote"},
    {"name": "News since fill"}
  ]
}
```

- `news_since_fill` is **optional**. Include only if `get_company_news(since=filled_at)` returned at least one item. Cap at 3. Order by `ts` descending (newest first). Drop the `{"name":"News since fill"}` source entry when the field is omitted.

## thesis

```json
{
  "type": "thesis",
  "data": {
    "ticker": "NVDA",
    "rating": "BUY",
    "horizon": "18 months",
    "weight_pct_nav": 4.0,
    "confidence": "High (7/10)",
    "tldr_html": "Holding <strong>NVDA</strong> for 18 months on the AI compute super-cycle. Exit if AI capex digests at hyperscalers <em>or</em> CUDA moat erodes.",
    "reasons_to_be_in": [
      "Blackwell ramp ahead of plan",
      "Sovereign AI adding $15-20B TAM",
      "Networking attach"
    ],
    "what_to_watch_weekly": [
      "GB200 shipment cadence",
      "Hyperscaler capex commentary",
      "CUDA developer growth",
      "Networking run-rate"
    ],
    "thesis_breakers": [
      "Two consecutive quarters of >25% DC slowdown",
      "Hyperscaler >50% custom-silicon for inference",
      "China removes additional $10B+ via new export bans"
    ]
  },
  "sources": [
    {"name": "Your research"},
    {"name": "Your style"},
    {"name": "2% risk rule"}
  ]
}
```

## tracker

```json
{
  "type": "tracker",
  "data": {
    "ticker": "NVDA",
    "thesis_tldr_html": "Holding NVDA for 18 months on the AI compute super-cycle.",
    "trade": {
      "side": "long",
      "shares": 10,
      "fill_price": 945,
      "current_price": 947.19,
      "unrealized_pnl": 21.90,
      "unrealized_pnl_pct": 0.23,
      "tp": 1100,
      "sl": 880
    }
  },
  "sources": [
    {"name": "Alpaca paper fill"},
    {"name": "Your thesis"}
  ]
}
```

## portfolio_risk

```json
{
  "type": "portfolio_risk",
  "data": {
    "risk_score": 7.2,
    "risk_label": "High",
    "risk_summary": "Driven by single-sector exposure and correlated AI names.",
    "sector_exposure": [
      {"label": "Tech / Semis", "pct": 82, "severity": "danger"},
      {"label": "Auto / EV", "pct": 11, "severity": "normal"},
      {"label": "Cash", "pct": 7, "severity": "warn"}
    ],
    "flags": [
      {
        "severity": "high",
        "title": "Single-sector concentration",
        "detail_html": "<strong>82%</strong> of your book in tech/semis. A 15% sector drawdown erases 18 months of gains."
      },
      {
        "severity": "high",
        "title": "Correlated AI cluster",
        "detail_html": "NVDA + AMD + MSFT have <strong>0.78 avg 60-day correlation</strong>."
      }
    ],
    "suggestions": [
      "<strong>De-correlate.</strong> Trim NVDA or MSFT by ~5% NAV; rotate into a non-correlated factor.",
      "<strong>Add a hedge.</strong> 5% notional in SH or 2% in SOXS caps drawdown."
    ]
  },
  "sources": [
    {"name": "Your holdings"},
    {"name": "60d correlation"},
    {"name": "Your 2% rule"}
  ]
}
```

- `severity` in `sector_exposure` items: `normal | warn | danger`
- `severity` in `flags` items: `low | med | high`

## Output rules

- ONE JSON object per response. No prose before or after the JSON block.
- All `*_html` fields can contain `<strong>` and `<em>`. No other tags.
- All numeric fields must come from tool results.
- All currency strings are the symbol (`$`, `€`, `£`).
- All dates are ISO-8601 UTC strings.
