#!/usr/bin/env python3
"""Offline guard for Proposal 048 — NER (name→ticker) resolution rule.

048 is prompt-only (system.md) + a one-sentence sharpening of the
`get_company_news` tool DESCRIPTION (market.py). There's no behaviour to unit
test, so this guards the two failure modes that *would* break things:

  1. the market.py description edit didn't disturb the `get_company_news`
     ToolDef registration (still registered, schema unchanged: `tickers` array,
     maxItems 10, required) — a typo in the ToolDef would break the agent;
  2. the new description carries the resolution guidance;
  3. the system.md section was added and still closes with the trust caveat
     (so the rule can't be read as license to fabricate).

Self-contained: temp-applies the proposal's market.py over the live file,
imports the tools registry, asserts, then restores the live file in a finally.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_048_news_ner.py

Exit code 0 = all pass, 1 = a check failed.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    """Walk up to the real repo root. Anchored on backend/auth.py — NOT part of
    048's mirror tree (which only carries backend/prompts/system.md +
    backend/tools/market.py), so the search can't stop early in the proposal."""
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
PROP = os.path.join(REPO, ".proposed_changes", "048-news-ner-ticker-resolution")
LIVE_MARKET = os.path.join(BACKEND, "tools", "market.py")
PROP_MARKET = os.path.join(PROP, "backend", "tools", "market.py")
PROP_SYSTEM = os.path.join(PROP, "backend", "prompts", "system.md")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def test_registry():
    print("\n=== get_company_news ToolDef intact after the description edit ===")
    from tools import TOOL_REGISTRY  # noqa: E402 — imported after temp-apply

    check("get_company_news still registered", "get_company_news" in TOOL_REGISTRY)
    td = TOOL_REGISTRY.get("get_company_news", {})
    schema = td.get("input_schema", {})
    props = schema.get("properties", {})
    tickers = props.get("tickers", {})
    check("tickers is an array", tickers.get("type") == "array", str(tickers.get("type")))
    check("tickers maxItems == 10", tickers.get("maxItems") == 10, str(tickers.get("maxItems")))
    check("tickers required", schema.get("required") == ["tickers"], str(schema.get("required")))
    check("limit + since still present",
          "limit" in props and "since" in props, str(list(props)))

    desc = (td.get("description") or "").lower()
    check("description carries resolution guidance",
          ("resolve" in desc and "ticker" in desc and "mag 7" in desc), desc[:120])
    check("callable still wired", callable(td.get("callable")))


def test_prompt_section():
    print("\n=== system.md NER section present + trust caveat intact ===")
    text = open(PROP_SYSTEM, encoding="utf-8").read()
    check("new section heading added",
          "## Resolving names to tickers (before any data tool)" in text)
    check("covers all data tools (news + quote + research)",
          "get_company_news" in text and "get_quote" in text and "research tools" in text)
    check("fallback-to-own-knowledge clause present",
          "best company" in text.lower() and "rather than refusing" in text.lower())
    check("closing trust caveat present (no fabrication)",
          "never invent a headline, price, or event a tool didn't return" in text)
    # the pre-existing catalysts-grounding rule must be untouched
    check("existing catalysts-grounding rule preserved",
          "do **not** invent events, partnerships, or product launches" in text)


def main() -> int:
    backup = None
    try:
        if os.path.isfile(PROP_MARKET) and os.path.abspath(PROP_MARKET) != os.path.abspath(LIVE_MARKET):
            with open(LIVE_MARKET, "rb") as f:
                backup = f.read()
            shutil.copyfile(PROP_MARKET, LIVE_MARKET)
        sys.path.insert(0, BACKEND)
        test_registry()
        test_prompt_section()
    finally:
        if backup is not None:
            with open(LIVE_MARKET, "wb") as f:
                f.write(backup)  # restore — never leave the live file modified

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
