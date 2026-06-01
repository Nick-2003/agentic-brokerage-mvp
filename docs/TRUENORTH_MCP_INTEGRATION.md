# Task — Real research data via TrueNorth's MCP server

**Type:** Data-source upgrade (not a scope change — same flows, same widgets, same prompts)
**Added:** 2026-05-21
**Status:** Proposed — ready for a build session
**Owner doc rule:** see "Process" at the bottom — this touches `CLAUDE.md`'s tech-stack table; `SCOPE.md` is unaffected.

---

## 1. Why

`backend/tools/research.py` is **hand-tuned mock data for exactly 7 tickers** (NVDA, AAPL, MSFT, TSLA, AMD, GOOGL, TCEHY). `get_company_fundamentals` / `get_consensus_targets` / `get_full_research` / `get_peer_set` all return `{"error": "no_coverage"}` for anything else. `get_technical_levels` is keyed off `MOCK_QUOTES`, same limitation.

So "analyze NVDA" demos perfectly and "analyze CRM" returns nothing. The product can't generalise beyond the demo names.

**TrueNorth's MCP server closes this gap.** It is a live server (verified 2026-05-21) exposing a real US-equities toolkit — analyst consensus, price targets, financial metrics, statements, SEC filings, technicals — for *any* liquid US ticker, with data provenance attached. It is free (same company) and already built.

Verified live:

```
analyst_estimates("NVDA")    → consensus "Buy" (58 buy / 16 hold / 3 sell),
                                target mean $276.75 (range $140–$360), EPS/rev consensus to 2031
stock_price_snapshot("NVDA") → $222.74, vol 106M, 50d MA $194.72, 200d MA $186.43
```

## 2. What changes — and what does NOT

**Does not change:** the widget JSON contract, `system.md` (one small addition — §7), the frontend, the tool *names* and *input schemas*, the agent loop, the mock-first pattern. The agent and UI never know the data source changed.

**Changes:** the *real path* inside 4 research tools + 1 technicals tool. Today they have only a mock path. We add a TrueNorth-backed real path next to the mock — exactly as `market.py` already does mock ↔ yfinance and `execution.py` does mock ↔ Alpaca.

Tools getting a real path: `get_company_fundamentals`, `get_consensus_targets`, `get_full_research`, `get_peer_set`, `get_technical_levels`. (`get_correlation_matrix` can stay mock for MVP — see §4.)

## 3. Connection

The backend (FastAPI + Anthropic SDK) calls TrueNorth's MCP server as an **MCP client**. This does **not** require switching to `claude-agent-sdk` — we run a client inside the existing backend; the agent loop is untouched.

Add `backend/mcp_client.py`:

- One long-lived MCP `ClientSession` to the TrueNorth server, lazily created (mirrors `agent.py::_get_client`).
- A typed helper `async def tn_call(tool: str, args: dict) -> dict` that calls the tool, parses the JSON result, and raises a clear error on failure.
- Connection retry + a clear `error: "truenorth_mcp_unreachable"` surfaced on failure (no silent fall-through to mock — same discipline as `alpaca_fetch_failed`).

**Transport — pick one (Tom to confirm access):**

- **(a) Hosted endpoint** — TrueNorth team provides a URL + auth. MCP `streamable-http` / SSE client transport. Cleanest for prod.
- **(b) Self-host** — run TrueNorth's MCP server from a *pulled* copy of `discovery-agents` (`python start_mcp_server.py`, `app/mcp_server/`). Local dev: stdio transport. Prod: deploy it as a separate Railway service, connect over HTTP. Pull-only — never push to the TrueNorth repo.

**Documented fallback — FMP-direct.** TrueNorth's MCP tools are themselves "backed by FMP" (Financial Modeling Prep). If MCP hosting is friction, the *same data* is reachable by calling FMP's REST API directly with an `FMP_API_KEY` — plain HTTP, no MCP client library, fits the existing yfinance-style pattern. Tradeoff: you re-assemble what TrueNorth already shapes (e.g. consensus distribution needs 2–3 FMP endpoints stitched). Primary path is TrueNorth MCP per the project decision; FMP-direct is the escape hatch.

Env vars (add to `backend/.env.example`): `TRUENORTH_MCP_URL`, `TRUENORTH_MCP_AUTH` (or `TRUENORTH_MCP_COMMAND`/`_ARGS` for stdio); `FMP_API_KEY` only if the fallback is used.

## 4. Tool-by-tool mapping

| Our tool | TrueNorth MCP tool(s) | Notes |
| --- | --- | --- |
| `get_consensus_targets` | `analyst_estimates` | Direct map. `price_targets.{target_high,target_low,target_mean,target_median}` → high/median/low; `recommendation_distribution.consensus_label` → `consensus_rating`; `num_analysts_*` → `n_analysts`. Upgrades/downgrades: drop or leave 0 (not in the API). |
| `get_company_fundamentals` | `financial_metrics` + `company_facts` | Valuation multiples + margins + growth from `financial_metrics`; `sector` from `company_facts`. `rating`/`target_price` from `analyst_estimates` (reuse the call). |
| `get_full_research` | `analyst_estimates` + `financial_metrics` + `financial_statements` + `company_facts` + `sec_filings` + `get_company_news` | **See §7** — the real path returns *structured facts + filing highlights + news*, and the agent synthesises the thesis/catalysts/risks narrative. The mock returns a finished thesis; the real path returns the inputs. |
| `get_peer_set` | `financial_metrics` (per peer) | Keep the static `_PEER_SET` sector→peers map (peer choice is editorial). Fill each peer's valuation via `financial_metrics`. |
| `get_technical_levels` | `stock_price_snapshot` + `technical_analysis` (or `_v3`) | `stock_price_snapshot` already returns `price_avg_50` / `price_avg_200` — use directly for SMA values. S/R levels + trend from `technical_analysis`. `screenshot_url` stays the mock SVG until live TradingView MCP. |
| `get_quote` (market.py) | `stock_price_snapshot` *(optional)* | Optional swap from yfinance — snapshot also gives 50/200 MA and is consistent with the rest. Low priority; yfinance works. |
| `get_correlation_matrix` | — | Leave mock for MVP. A real version needs `historical_bars` per ticker + a numpy correlation — defer; it's a `portfolio_risk` nicety, not a demo-blocker. |

## 5. Steps (ordered)

1. Add `backend/mcp_client.py` — connection manager + `tn_call()` helper. Add env vars to `.env.example`.
2. Add a `USE_MOCK_RESEARCH` env flag (sibling of `USE_MOCK_MARKET`) so the deterministic demo can still force the hand-tuned mocks.
3. `get_consensus_targets` — simplest, do it first. Add the real path; verify against a non-demo ticker.
4. `get_company_fundamentals` — real path via `financial_metrics` + `company_facts`.
5. `get_technical_levels` — real path via `stock_price_snapshot` + `technical_analysis`.
6. `get_peer_set` — real path: static peer map + `financial_metrics` per peer.
7. `get_full_research` — real path as a **data aggregator** (§7) + the one `system.md` addition.
8. Populate `sources` on every real-path return (§6).
9. Update `CLAUDE.md` tech-stack table; add a `SESSION_LOG.md` entry (§Process).

Each tool keeps its mock path intact. Pattern, per tool:

```python
async def get_consensus_targets(args, user_id):
    ticker = (args.get("ticker") or "").upper()
    if os.getenv("USE_MOCK_RESEARCH") == "1":
        return _mock_consensus(ticker)        # existing hand-tuned path
    try:
        return await _truenorth_consensus(ticker)   # new real path
    except TrueNorthError:
        return {"error": "truenorth_mcp_unreachable", "ticker": ticker}
```

## 6. Trust / citations

The widget schema has a `sources` field and citation chips depend on it. Every **real-path** return must include a `sources` array, e.g.:

```python
"sources": [{"name": "FMP — analyst estimates", "url": None},
            {"name": "SEC EDGAR — latest 10-Q", "url": "<filing url>"}]
```

TrueNorth tool results already carry provenance strings ("Backed by FMP", "FMP profile + SEC EDGAR") — map those into `sources`. Also set `is_mock: false` on real-path returns (currently every research return hardcodes `is_mock: true`).

## 7. The `get_full_research` nuance + the one `system.md` change

The mock `get_full_research` returns a *finished* thesis/catalysts/risks (someone hand-wrote them). TrueNorth has no pre-written thesis — so the real path **returns the raw material** and the agent writes the narrative:

Real `get_full_research` returns: `rating` (from consensus label), `target_price` (consensus mean), `valuation`, `fundamentals`, `sector`, recent `financial_statements` highlights, latest `sec_filings` items (esp. risk-factor section snippets), and recent news. **No `thesis`/`catalysts`/`risks` strings.**

Add one section to `backend/prompts/system.md`:
> When `get_full_research` returns `is_mock: false`, it returns raw facts, not a written thesis. Synthesise the `thesis`, `catalysts`, and `risks` fields of the `research_card` yourself from those facts, the filing highlights, and the news. Every number you state must be copied verbatim from the tool result (the existing "copy numbers digit-for-digit" rule applies). Catalysts and risks should be grounded in the filings/news returned — do not invent events.

This is consistent with trust principle #3: the agent composes *narrative* from sourced data; it never authors *numbers*. Verify the synthesised cards read as well as the hand-tuned ones — if not, iterate the prompt, not the tool.

## 8. Mock-first preserved

The hand-tuned mocks stay. `USE_MOCK_MARKET=1 USE_MOCK_BROKER=1 USE_MOCK_RESEARCH=1` remains the deterministic demo path whose numbers match the demo HTML ($942.50 etc.). Real mode (`USE_MOCK_RESEARCH` unset) makes "analyze *any* US ticker" produce genuine research. Do not delete the mocks — they are the demo.

## 9. Acceptance

- "analyze CRM" (or any non-demo liquid ticker) produces a full `research_card` with real consensus rating, real price target, real valuation/margins, and a synthesised thesis grounded in real filings/news.
- Every numeric field traces to a TrueNorth tool result; `sources` populated; citation chips render.
- The 7 demo tickers still produce identical deterministic output under `USE_MOCK_RESEARCH=1`.
- TrueNorth MCP unreachable → clean `truenorth_mcp_unreachable` error in the widget, no crash, no silent mock.

## 10. Risks

- **MCP access** — needs a hosted endpoint or self-host (§3). Critical-path: confirm before starting. FMP-direct fallback de-risks it.
- **Latency** — an extra network hop per research turn. Mitigate: `get_full_research` should batch its TrueNorth calls concurrently (`asyncio.gather`); cache hot results briefly.
- **Synthesis quality** — agent-written theses must match the hand-tuned bar (§7). Budget prompt-iteration time.
- **Rate limits** — TrueNorth/FMP free-tier limits at scale; fine for 5–10 MVP users; note in `SESSION_LOG.md` if hit.

## 11. Process

- This is a **data-source change**, not a scope change — no `SCOPE.md` amendment needed (same six flows, same widgets).
- It **does** change `CLAUDE.md`'s tech-stack table: the "Market data | yfinance" row should become "Market data / research | TrueNorth MCP (FMP-backed) + yfinance quotes". Update `CLAUDE.md` as the last step, per its own "update the doc, then build" rule.
- Add a `SESSION_LOG.md` entry when done.
- Repo rule: TrueNorth repos are pull-only / reference-only — never push.
</content>
