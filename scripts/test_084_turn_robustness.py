#!/usr/bin/env python3
"""Offline guard for Proposal 084 — turn robustness (no silent turns, no raw-JSON
leaks, a whole-turn wall-clock cap, and a /healthz failover-armed self-check).

Network-free (scripted `client.complete` + `_call_tool`), LIVE-MODE + `_created`
guards per 078. Confirm with `git status` after running.

The three live failures on 2026-07-26 (screenshots + PostHog export) this pins:
  1. **Silent turn** — Kimi ran every tool then returned an empty terminal message,
     so `_finalize_terminal_widget` emitted NOTHING: a "DONE" card with no answer
     and no error. "loads entire process but may fail to provide response and not
     provide explicit error of failure."
  2. **Raw-JSON leak** — a `portfolio_risk` widget came back truncated/unparseable,
     so the finalizer dumped the raw `{"type":"portfolio_risk"…}` as a chat bubble.
     "only part of backend content."
  3. **Unbounded turn** — a per-CALL 120s budget (083) does not cap a turn that
     makes several model calls; turns ran 189s+ before erroring.
Plus a 083 copy bug: the timeout message leaked a literal `<RAIL>_TIMEOUT_S`
placeholder + an operator instruction into the user's bubble.

Covers:
  A. `_looks_like_widget_attempt` — tells a broken widget from prose;
  B. finalizer: empty terminal → explicit `empty_response` (never silent);
     unparseable widget → `widget_unrenderable`, NO raw JSON in the payload;
     genuine prose still a `message`; a valid widget still a `widget`;
  C. end-to-end on the Kimi rail — an all-tools-then-empty turn yields an explicit
     error, not a silent `done`; a truncated widget turn never leaks JSON;
  D. per-turn cap — `_turn_over_budget`; a turn past `CHAT_TURN_BUDGET_S` stops with
     `turn_timeout` and does NOT restart on DeepSeek (a restart would double it);
     `CHAT_TURN_BUDGET_S=0` disables;
  E. the 083 timeout copy no longer leaks `<RAIL>` / operator ops to the user;
  F. `failover_status()` + /healthz — `armed` is false when `timeout` is missing
     from `LLM_FAILOVER_ON`, true when the chain is complete, N/A on the DeepSeek rail.

Run:
    backend/.venv/bin/python scripts/test_084_turn_robustness.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
import time

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
PROP = os.path.join(REPO, ".proposed_changes", "084-turn-robustness", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["agent.py", "main.py", ".env.example"]
NET_NEW: list[str] = []
_created: list[str] = []
LIVE_MODE = not os.path.isdir(PROP)


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 084 is applied; asserting against the LIVE tree)")
        return
    for f in OVERWRITE:
        shutil.copy2(os.path.join(BACKEND, f), os.path.join(backup_dir, f))
        shutil.copy2(os.path.join(PROP, f), os.path.join(BACKEND, f))
    for f in NET_NEW:
        dst = os.path.join(BACKEND, f)
        if os.path.isfile(dst):
            shutil.copy2(dst, os.path.join(backup_dir, f))
            OVERWRITE.append(f)
        else:
            _created.append(dst)
        shutil.copy2(os.path.join(PROP, f), dst)


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


async def _collect(agen):
    return [ev async for ev in agen]


def _events(gen):
    return asyncio.run(_collect(gen))


def _codes(evs):
    return [e["data"].get("code") for e in evs if e["event"] == "error"]


PORTFOLIO_RISK_JSON = (
    '{"type":"portfolio_risk","data":{"risk_score":5.0,"risk_label":"Moderate",'
    '"sector_exposure":[{"label":"Tech","pct":52.4}],"flags":[]}}'
)


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ.setdefault("USE_MOCK_MARKET", "1")
    os.environ.setdefault("USE_MOCK_BROKER", "1")
    os.environ["WIDGET_VALIDATOR_MODE"] = "off"
    os.environ["CHAT_VERBOSE_ERRORS"] = "1"
    os.environ["LLM_RAIL"] = "kimi"
    os.environ["KIMI_API_KEY"] = "sk-kimi-test"
    os.environ["USE_MOCK_KIMI"] = "0"
    os.environ.pop("CHAT_TURN_BUDGET_S", None)

    import agent
    from observability import NOOP_TRACER

    # ---------------------------------------------------------------- A
    print("\nA. _looks_like_widget_attempt — broken widget vs prose")
    check("bare truncated widget object is an attempt",
          agent._looks_like_widget_attempt('{"type":"portfolio_risk","data":{"risk_sco'))
    check("fenced json widget is an attempt",
          agent._looks_like_widget_attempt('```json\n{"type":"research_card"'))
    check("plain prose is NOT an attempt",
          not agent._looks_like_widget_attempt("Your book is moderately risky — mostly tech."))
    check("prose that merely mentions the word type is NOT an attempt",
          not agent._looks_like_widget_attempt("There are two type of risk here."))
    check("empty string is NOT an attempt", not agent._looks_like_widget_attempt(""))

    # ---------------------------------------------------------------- B
    print("\nB. finalizer never ends silently, never leaks raw JSON")

    # empty terminal → explicit error, exactly one event
    evs = _events(agent._finalize_terminal_widget("", [], {}, NOOP_TRACER))
    check("empty terminal → an explicit error (not silence)",
          _codes(evs) == ["empty_response"], str([e["event"] for e in evs]))
    check("empty terminal → NO message/widget event",
          not any(e["event"] in ("message", "widget") for e in evs))

    # unparseable widget → graceful error, and the raw JSON must NOT be in any payload
    truncated = PORTFOLIO_RISK_JSON[:60]  # cut mid-object → json.loads fails
    evs = _events(agent._finalize_terminal_widget(truncated, [], {}, NOOP_TRACER))
    check("truncated widget → widget_unrenderable error",
          _codes(evs) == ["widget_unrenderable"], str(_codes(evs)))
    dumped = " ".join(str(e["data"]) for e in evs)
    check("the raw widget JSON is NEVER shown to the user",
          "portfolio_risk" not in dumped and "risk_score" not in dumped, dumped[:120])
    check("no plain message event carrying the JSON",
          not any(e["event"] == "message" for e in evs))

    # genuine prose → a normal message
    evs = _events(agent._finalize_terminal_widget("Your book skews tech-heavy.", [], {}, NOOP_TRACER))
    check("prose → a message event", [e["event"] for e in evs] == ["message"])
    check("prose text preserved",
          evs[0]["data"]["text"] == "Your book skews tech-heavy.")

    # a VALID widget still works (regression)
    evs = _events(agent._finalize_terminal_widget(
        "```json\n" + PORTFOLIO_RISK_JSON + "\n```", [], {}, NOOP_TRACER))
    check("valid widget → a widget event (unbroken)",
          [e["event"] for e in evs] == ["widget"]
          and evs[0]["data"]["type"] == "portfolio_risk", str([e["event"] for e in evs]))

    # ---------------------------------------------------------------- C
    print("\nC. end-to-end on the Kimi rail")
    orig_complete = agent.kimi_client.complete
    orig_call = agent._call_tool

    async def _tool_then_empty(system, messages, tools=None, max_tokens=None):
        # 1st call → a tool; 2nd call → an EMPTY terminal (the live silent turn).
        if not any(m.get("role") == "tool" for m in messages):
            return {"text": "", "tool_calls": [
                {"id": "c1", "name": "get_portfolio", "input": {}}], "usage": {}}
        return {"text": "   ", "tool_calls": [], "usage": {}}

    async def _tool_then_truncated_widget(system, messages, tools=None, max_tokens=None):
        if not any(m.get("role") == "tool" for m in messages):
            return {"text": "", "tool_calls": [
                {"id": "c1", "name": "get_portfolio", "input": {}}], "usage": {}}
        return {"text": PORTFOLIO_RISK_JSON[:70], "tool_calls": [], "usage": {}}

    async def _fake_tool(name, args, user_id):
        return True, {"positions": [], "total_equity": 100000}

    try:
        agent._call_tool = _fake_tool

        agent.kimi_client.complete = _tool_then_empty
        evs = _events(agent.run_chat("give me a tldr on my portfolio", "u1"))
        check("a silent turn now ends in an explicit error",
              "empty_response" in _codes(evs), str(_codes(evs)))
        check("the tools still ran (breadcrumbs present)",
              any(e["event"] == "tool_result" for e in evs))
        check("a done event still closes the stream",
              any(e["event"] == "done" for e in evs))

        agent.kimi_client.complete = _tool_then_truncated_widget
        evs = _events(agent.run_chat("how risky is my book?", "u1"))
        check("a truncated widget turn → widget_unrenderable, not raw JSON",
              "widget_unrenderable" in _codes(evs), str(_codes(evs)))
        check("no raw portfolio_risk JSON reached the user",
              not any(e["event"] == "message" and "portfolio_risk" in str(e["data"]) for e in evs))
    finally:
        agent.kimi_client.complete = orig_complete
        agent._call_tool = orig_call

    # ---------------------------------------------------------------- D
    print("\nD. per-turn wall-clock cap")
    os.environ["CHAT_TURN_BUDGET_S"] = "300"
    check("_turn_over_budget false when fresh", not agent._turn_over_budget(time.monotonic()))
    check("_turn_over_budget true when past budget",
          agent._turn_over_budget(time.monotonic() - 301))
    os.environ["CHAT_TURN_BUDGET_S"] = "0"
    check("CHAT_TURN_BUDGET_S=0 disables the cap",
          not agent._turn_over_budget(time.monotonic() - 100000))

    # A turn that runs one tool then loops back must stop with turn_timeout, and
    # must NOT fail over (a restart would double the wait). Driven by a deterministic
    # fake clock so it's non-flaky: the loop's SECOND budget check (after the tool
    # round) reads a time past the budget.
    os.environ["CHAT_TURN_BUDGET_S"] = "300"
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    os.environ["DEEPSEEK_API_KEY"] = "sk-ds"
    os.environ.pop("USE_MOCK_DEEPSEEK", None)
    ds_called = {"n": 0}

    # fake monotonic: 0, then jumps 1000s on every subsequent read → any check after
    # start_time is instantly "over budget", but the FIRST iteration (which captured
    # start_time) still runs, so we exercise "breach mid-turn" not "breach at entry".
    clock = {"t": [0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0]}

    def _fake_monotonic():
        return clock["t"].pop(0) if len(clock["t"]) > 1 else clock["t"][0]

    async def _one_tool_then_more(system, messages, tools=None, max_tokens=None):
        # Always ask for a tool → the loop always continues to another budget check.
        return {"text": "", "tool_calls": [
            {"id": "c1", "name": "get_portfolio", "input": {}}], "usage": {}}

    async def _ds_spy(system, messages, tools=None, max_tokens=None):
        ds_called["n"] += 1
        return {"text": "from deepseek", "tool_calls": [], "usage": {}}

    orig_k, orig_ds = agent.kimi_client.complete, agent.deepseek_client.complete
    real_monotonic = agent.time.monotonic
    try:
        agent.kimi_client.complete = _one_tool_then_more
        agent.deepseek_client.complete = _ds_spy
        agent._call_tool = _fake_tool
        agent.time.monotonic = _fake_monotonic
        evs = _events(agent.run_chat("build me a huge multi-step plan", "u1"))
        check("an over-budget turn stops with turn_timeout", "turn_timeout" in _codes(evs), str(_codes(evs)))
        check("a turn-budget breach does NOT fail over to DeepSeek", ds_called["n"] == 0,
              f"deepseek called {ds_called['n']}×")
        check("the turn-timeout message is user-appropriate (no <RAIL>, no env var)",
              all("_TIMEOUT_S" not in e["data"].get("message", "") and "<RAIL>" not in e["data"].get("message", "")
                  for e in evs if e["event"] == "error"))
    finally:
        agent.kimi_client.complete = orig_k
        agent.deepseek_client.complete = orig_ds
        agent._call_tool = orig_call
        agent.time.monotonic = real_monotonic
        os.environ.pop("CHAT_TURN_BUDGET_S", None)

    # ---------------------------------------------------------------- E
    print("\nE. the 083 timeout copy no longer leaks <RAIL>/operator ops")
    import kimi_client
    timeout_err = kimi_client.KimiError("kimi request failed [timeout]: ReadTimeout: …", reason="timeout")
    msg, code = agent._classify_agent_error(timeout_err)
    check("code still provider_timeout", code == "provider_timeout", code)
    check("no literal <RAIL> placeholder in the user copy", "<RAIL>" not in msg, msg)
    check("no *_TIMEOUT_S env var leaked to the user", "_TIMEOUT_S" not in msg, msg)
    check("no operator instruction in the user bubble", "operator" not in msg.lower(), msg)
    check("still names the rail + that it timed out",
          "Kimi (Moonshot) API" in msg and "timed out" in msg.lower(), msg[:80])

    # ---------------------------------------------------------------- F
    print("\nF. failover_status() self-check + /healthz")
    os.environ["LLM_RAIL"] = "kimi"
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    os.environ["DEEPSEEK_API_KEY"] = "sk-ds"
    os.environ.pop("USE_MOCK_DEEPSEEK", None)

    os.environ["LLM_FAILOVER_ON"] = "billing,rate_limit,overloaded"  # timeout MISSING
    st = agent.failover_status()
    check("timeout missing from LLM_FAILOVER_ON → armed is FALSE",
          st["armed"] is False and st["timeout_covered"] is False, str(st))
    check("…but the misconfig is visible (fallback_enabled + key both true)",
          st["fallback_enabled"] and st["deepseek_key_present"])

    os.environ["LLM_FAILOVER_ON"] = "billing,rate_limit,overloaded,timeout,network"
    st = agent.failover_status()
    check("full chain → armed is TRUE", st["armed"] is True and st["timeout_covered"], str(st))

    os.environ["LLM_RAIL"] = "deepseek"  # last resort — nothing beneath it
    st = agent.failover_status()
    check("DeepSeek rail → applicable False, armed False (correct, not a misconfig)",
          st["applicable"] is False and st["armed"] is False, str(st))
    os.environ["LLM_RAIL"] = "kimi"
    os.environ.pop("LLM_FAILOVER_ON", None)

    # /healthz surfaces it
    src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
    check("/healthz exposes the failover self-check", '"failover": failover_status()' in src)
    env = open(os.path.join(BACKEND, ".env.example"), encoding="utf-8").read()
    check(".env.example documents CHAT_TURN_BUDGET_S", "CHAT_TURN_BUDGET_S=300" in env)


def main() -> int:
    backup = tempfile.mkdtemp(prefix="p084_")
    try:
        apply_proposal(backup)
        run()
    finally:
        restore(backup)
        shutil.rmtree(backup, ignore_errors=True)
    total, ok = len(results), sum(results)
    print(f"\n{'=' * 60}\n  {ok}/{total} checks passed\n{'=' * 60}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
