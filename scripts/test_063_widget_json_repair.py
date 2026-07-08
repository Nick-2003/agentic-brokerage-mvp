#!/usr/bin/env python3
"""Offline guard for Proposal 063 — widget JSON survives an unescaped inner quote.

The reported bug: a `morning_brief` rendered as raw JSON in a code block because a
paragraph contained an unescaped double quote (``even with "infinite money."``),
so `json.loads` failed and the widget fell through to a plain-text bubble.

063 = (a) `agent._extract_widget_json` gains a best-effort quote-repair so the
widget still renders; (b) `widget_contract.md` tells the model to avoid raw quotes
in string values. Fail-closed: a bad repair parses to nothing → same plain-text
fallback as before, never a corrupted widget.

Covers:
  A. the EXACT failing payload (fenced ```json block) now parses to a morning_brief;
  B. valid JSON is untouched (incl. structural-looking chars inside a string);
  C. already-escaped quotes are preserved;
  D. non-widget object → None; unrepairable garbage → None (fail-closed);
  E. the known-imperfect `",` case fails closed (None, not a corrupted widget);
  F. widget_contract.md carries the no-raw-quote rule.

Self-contained: temp-applies backend/{agent.py, prompts/widget_contract.md} over
live, imports, asserts, restores in a finally. Anchored on backend/auth.py.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_063_widget_json_repair.py
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
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
PROP = os.path.join(REPO, ".proposed_changes", "063-widget-json-repair")
FILES = [
    (os.path.join(BACKEND, "agent.py"), os.path.join(PROP, "backend", "agent.py")),
    (os.path.join(BACKEND, "prompts", "widget_contract.md"),
     os.path.join(PROP, "backend", "prompts", "widget_contract.md")),
]

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


# The reported failing widget (trimmed), wrapped in the ```json fence the model emits.
FAILING = r'''```json
{
  "type": "morning_brief",
  "data": {
    "headline": "Tech under pressure: yields surge",
    "paragraphs": [
      "<strong>Nasdaq futures -0.84%</strong>, S&P futures <strong>-0.33%</strong>.",
      "<strong>META</strong> outperformed the broader decline. Zuckerberg signaled he has no plans to slow down even with "infinite money." CoreWeave sank as Mag 7 moves rattled AI infrastructure investors.",
      "<strong>TSLA</strong> in focus on potential SpaceX merger speculation."
    ]
  },
  "sources": [{"name": "yfinance company news"}, {"name": "Macro snapshot"}]
}
```'''


def run() -> None:
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    import agent  # noqa: E402 — after temp-apply

    print("\n=== A. the exact failing payload now renders ===")
    w = agent._extract_widget_json(FAILING)
    check("failing payload → a widget (not None)", w is not None)
    if w:
        check("type is morning_brief", w.get("type") == "morning_brief")
        paras = w.get("data", {}).get("paragraphs", [])
        check("all 3 paragraphs recovered", len(paras) == 3, f"n={len(paras)}")
        check("inner quotes preserved as content",
              len(paras) > 1 and '"infinite money."' in paras[1],
              paras[1][:80] if len(paras) > 1 else "")

    print("\n=== B/C. valid JSON untouched; escapes preserved ===")
    valid = '```json\n{"type":"x","data":{"a":"b, c: d]","q":"say \\"hi\\" ok"}}\n```'
    v = agent._extract_widget_json(valid)
    check("valid widget parses", v is not None and v.get("type") == "x")
    check("structural chars inside string kept", v is not None and v["data"]["a"] == "b, c: d]")
    check("already-escaped quotes preserved", v is not None and v["data"]["q"] == 'say "hi" ok')

    print("\n=== D. non-widget / garbage → None ===")
    check("no type/data → None", agent._extract_widget_json('```json\n{"foo":1}\n```') is None)
    check("not JSON at all → None", agent._extract_widget_json("just a plain sentence.") is None)

    print("\n=== E. ambiguous cases stay SAFE (valid widget or None, never garbage) ===")
    # comma INSIDE the quoted phrase (…"hi,"…) — repairs correctly.
    inside = ('```json\n{"type":"morning_brief","data":{"paragraphs":'
              '["he said "hi," then left"]}}\n```')
    ri = agent._extract_widget_json(inside)
    check("comma-inside-quotes repairs to correct content",
          ri is not None and ri["data"]["paragraphs"][0] == 'he said "hi," then left',
          None if ri is None else ri["data"]["paragraphs"][0])
    # comma OUTSIDE the quoted phrase (…"hi",…) — genuinely ambiguous → fail-closed.
    outside = ('```json\n{"type":"morning_brief","data":{"paragraphs":'
               '["he said "hi", then left"]}}\n```')
    ro = agent._extract_widget_json(outside)
    check("comma-outside-quotes → None (fail-closed to plain text)", ro is None)
    # The invariant that matters: always None OR a well-formed widget, never garbage/crash.
    for probe in (FAILING, inside, outside, '```json\n{"type":"x"}\n```', "nonsense"):
        r = agent._extract_widget_json(probe)
        ok = r is None or (isinstance(r, dict) and "type" in r and "data" in r)
        if not ok:
            check("invariant: None or well-formed widget", False, repr(r)[:80])
            break
    else:
        check("invariant: every input → None or a well-formed widget", True)

    print("\n=== F. prompt rule present ===")
    wc = open(os.path.join(BACKEND, "prompts", "widget_contract.md"), encoding="utf-8").read()
    check("widget_contract warns about raw double quotes", "raw double quote" in wc.lower())
    check("widget_contract shows the escape/single-quote fix", "infinite money" in wc)


def main() -> int:
    backups: list[tuple[str, str]] = []
    try:
        for live, prop in FILES:
            if not os.path.isfile(prop):
                print(f"missing proposal file: {prop}")
                return 1
            bak = live + ".063bak"
            shutil.copy2(live, bak)
            backups.append((live, bak))
            shutil.copy2(prop, live)
        run()
    finally:
        for live, bak in backups:
            shutil.copy2(bak, live)
            os.remove(bak)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
