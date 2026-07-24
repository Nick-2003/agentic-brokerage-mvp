#!/usr/bin/env python3
"""Offline guard for Proposal 082 — inline-HTML emphasis normalisation in plain replies.

Network-free. Temp-apply → assert → restore-in-`finally`, with the 078 LIVE-MODE
and non-destructive (`_created`) guards. Confirm with `git status` after running.

**Why this test exists, and why it differs from 062's.**
`test_062_markdown_emphasis.py` asserts the system-prompt *text* — that the rule is
written down. It never checks what the model's output actually becomes. That's
precisely why the bug returned: the rule was intact, but the rule was tuned against
Claude and the rail is now Kimi (080), via DeepSeek (074). 077 then widened the blast
radius — plain replies used to be rare, but every options-chain answer is now a plain
reply *and* a table.

So 082 fixes it deterministically and this suite asserts **behaviour**, not prose:
the emitted `message` event is normalised no matter which rail produced it.

Covers:
  A. the conversion itself — strong/b → **, em/i → _, nested, attributes;
  B. conservative behaviour — empty pairs, stray/unclosed tags left ALONE
     (no broken `****`), idempotent, non-emphasis tags NOT touched (082 is not
     a sanitiser — 049's no-raw-HTML choice must survive);
  C. code is sacred — inline spans and fenced blocks pass through verbatim;
  D. END-TO-END through the real agent loop: a rail that emits `<strong>` yields a
     `message` event containing `**` and no `<strong>`;
  E. **WIDGETS ARE UNTOUCHED** — widget text fields legitimately use `<strong>`
     (rendered via DOMPurify `SafeHtml`), so a widget turn must be byte-identical;
  F. rail-agnostic — same normalisation on the Anthropic-format path.

Run:
    backend/.venv/bin/python scripts/test_082_emphasis_normalisation.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        p = os.path.dirname(d)
        if p == d:
            raise RuntimeError("repo root not found")
        d = p


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes",
                    "082-markdown-emphasis-normalisation", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["agent.py"]
_created: list[str] = []
LIVE_MODE = not os.path.isdir(PROP)


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 082 is applied; asserting against the LIVE tree)")
        return
    for f in OVERWRITE:
        shutil.copy2(os.path.join(BACKEND, f), os.path.join(backup_dir, f))
        shutil.copy2(os.path.join(PROP, f), os.path.join(BACKEND, f))


def restore(backup_dir: str) -> None:
    if LIVE_MODE:
        return
    for f in OVERWRITE:
        b = os.path.join(backup_dir, f)
        if os.path.isfile(b):
            shutil.copy2(b, os.path.join(BACKEND, f))
    for p in _created:
        if os.path.isfile(p):
            os.remove(p)


async def drain(agen):
    return [ev async for ev in agen]


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ.setdefault("USE_MOCK_MARKET", "1")
    os.environ.setdefault("USE_MOCK_BROKER", "1")
    os.environ["WIDGET_VALIDATOR_MODE"] = "off"
    os.environ["KIMI_API_KEY"] = "sk-kimi-test"
    os.environ["USE_MOCK_KIMI"] = "0"
    os.environ["LLM_RAIL"] = "kimi"

    import agent
    import kimi_client

    f = agent._html_emphasis_to_markdown

    # ---------------------------------------------------------------- A
    print("\nA. conversion")
    check("<strong> → **", f("<strong>NVDA</strong> is up") == "**NVDA** is up")
    check("<em> → _", f("<em>maybe</em>") == "_maybe_")
    check("<b>/<i> too", f("<b>x</b> and <i>y</i>") == "**x** and _y_")
    check("nested resolves fully", f("<strong><em>both</em></strong>") == "**_both_**")
    check("attributes tolerated", f('<strong class="a">attr</strong>') == "**attr**")
    check("case-insensitive", f("<STRONG>X</STRONG>") == "**X**")
    check("multiple pairs in one line",
          f("<strong>a</strong> then <strong>b</strong>") == "**a** then **b**")

    # ---------------------------------------------------------------- B
    print("\nB. conservative — never make it worse")
    check("empty pair NOT turned into broken ****",
          f("<strong></strong>") == "<strong></strong>")
    check("stray/unclosed tag left alone",
          f("unclosed <strong>oops") == "unclosed <strong>oops")
    check("idempotent", f(f("<strong>a</strong>")) == "**a**")
    check("plain text untouched", f("plain text") == "plain text")
    check("NOT a sanitiser — other tags stay literal (049 preserved)",
          f("<script>alert(1)</script>") == "<script>alert(1)</script>")
    check("<div> untouched", f("<div>x</div>") == "<div>x</div>")

    # ---------------------------------------------------------------- C
    print("\nC. code is sacred")
    check("inline code span untouched",
          f("use `<strong>` in HTML") == "use `<strong>` in HTML")
    fence = "```html\n<strong>keep</strong>\n```"
    check("fenced block untouched", f(fence) == fence)
    check("prose around a fence IS converted",
          f("<strong>hi</strong>\n" + fence) == "**hi**\n" + fence)

    # ---------------------------------------------------------------- D
    print("\nD. end-to-end — the emitted message event is normalised")

    async def _emits_html(system, messages, tools=None, max_tokens=None):
        return {"text": "Here is <strong>NVDA</strong> at <em>942.50</em>.",
                "tool_calls": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

    kimi_client.complete = _emits_html
    evs = asyncio.run(drain(agent.run_chat("hi", "u1")))
    msgs = [e for e in evs if e["event"] == "message"]
    text = msgs[0]["data"]["text"] if msgs else ""
    check("message event emitted", bool(msgs))
    check("no raw <strong> reaches the client", "<strong>" not in text, text[:70])
    check("rendered as Markdown bold/italic",
          "**NVDA**" in text and "_942.50_" in text, text[:70])

    # ---------------------------------------------------------------- E
    print("\nE. WIDGETS untouched (they legitimately use <strong> via DOMPurify)")
    widget_payload = {
        "type": "morning_brief",
        "data": {"headline": "Up today",
                 "paragraphs": ["<strong>NVDA</strong> led the move."]},
        "sources": [{"name": "Your portfolio"}],
    }

    async def _emits_widget(system, messages, tools=None, max_tokens=None):
        return {"text": json.dumps(widget_payload), "tool_calls": [], "usage": {}}

    kimi_client.complete = _emits_widget
    evs = asyncio.run(drain(agent.run_chat("brief me", "u1")))
    wids = [e for e in evs if e["event"] == "widget"]
    check("widget event emitted", bool(wids))
    para = wids[0]["data"]["data"]["paragraphs"][0] if wids else ""
    check("widget text field KEEPS <strong> (byte-identical)",
          para == "<strong>NVDA</strong> led the move.", para)
    check("widget was NOT markdown-ified", "**NVDA**" not in para)

    # ---------------------------------------------------------------- F
    print("\nF. rail-agnostic — one place covers every provider")
    src = open(os.path.join(BACKEND, "agent.py")).read()
    check("normalisation sits on the SHARED terminal path",
          src.count("_html_emphasis_to_markdown(full_text)") == 1,
          "one call site = every rail")
    check("call site is the plain-message branch, not the widget branch",
          "_html_emphasis_to_markdown(full_text)" in
          src.split('yield {"event": "widget"')[-1])


def main() -> None:
    backup = tempfile.mkdtemp(prefix="082-backup-")
    try:
        apply_proposal(backup)
        run()
    finally:
        restore(backup)
        shutil.rmtree(backup, ignore_errors=True)

    total, ok = len(results), sum(results)
    print(f"\n{'=' * 62}\n  {ok}/{total} checks passed\n{'=' * 62}")
    print("Live tree restored — confirm with: git status --short")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
