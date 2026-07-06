#!/usr/bin/env python3
"""Offline guard for Proposal 062 — plain messages use Markdown emphasis, not HTML.

Root cause: the chat has two render paths. Widget text fields render as sanitised
HTML (SafeHtml/DOMPurify — `<strong>`/`<em>` allowed), but a plain "loose chat"
message bubble renders through react-markdown WITHOUT rehype-raw (a deliberate
XSS-safety choice), so a raw `<strong>` shows as literal tag text. 062 is
prompt-only: it tells the model that plain markdown replies must use `**bold**`/
`_italic_` and reserve `<strong>`/`<em>` for widget fields.

Guards the two things that would break the fix:
  1. the new plain-markdown rule is present (Markdown, not HTML; `**bold**`;
     never `<strong>`; explains raw HTML → literal text);
  2. the existing WIDGET `<strong>` guidance is preserved AND now scoped to
     widget fields only (so the widget path is untouched).

Self-contained: temp-applies the proposal's system.md over live, asserts on the
live file, restores in a finally. Anchored on backend/auth.py (not in 062's
mirror).

Run with the backend venv (or any python3):
    backend/.venv/bin/python scripts/test_062_markdown_emphasis.py
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
LIVE = os.path.join(REPO, "backend", "prompts", "system.md")
PROP = os.path.join(
    REPO, ".proposed_changes", "062-plain-message-markdown-emphasis",
    "backend", "prompts", "system.md",
)

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def run() -> None:
    txt = open(LIVE, encoding="utf-8").read()
    low = txt.lower()

    print("\n=== new plain-markdown emphasis rule ===")
    check("states a plain reply is Markdown not HTML", "plain markdown reply is markdown" in low)
    check("prescribes **bold** / _italic_", "`**bold**`" in txt and "`_italic_`" in txt)
    check("forbids <strong>/<em> in plain replies",
          "never `<strong>`" in txt or "never `<strong>`/`<em>`" in txt)
    check("explains raw HTML renders as literal text", "literal text" in low)

    print("\n=== widget HTML guidance preserved + scoped ===")
    check("widget Style rule still present", "`<strong>` for tickers" in txt)
    check("Style rule scoped to widget fields only", "widget fields only" in low)
    check("morning_brief example still uses <strong>", "<strong>nvda</strong>" in low)


def main() -> int:
    if not os.path.isfile(PROP):
        print(f"missing proposal file: {PROP}")
        return 1
    bak = LIVE + ".062bak"
    shutil.copy2(LIVE, bak)
    try:
        shutil.copy2(PROP, LIVE)
        run()
    finally:
        shutil.copy2(bak, LIVE)
        os.remove(bak)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
