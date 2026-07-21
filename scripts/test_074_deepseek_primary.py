#!/usr/bin/env python3
"""Offline guard for Proposal 074 — DeepSeek as a selectable PRIMARY rail.

Network-free. Temp-apply → assert → restore-in-`finally`, with the NON-DESTRUCTIVE
guard from 073 (never delete a file this run did not create). Confirm with
`git status` after running — the live tree must end exactly as it started.

Context: both frontier rails are credit-blocked (Anthropic exhausted, OpenAI 429
insufficient_quota), so DeepSeek is the only funded model. 074 lets it be the
chosen primary (`LLM_RAIL=deepseek`) instead of only a fallback, so the app runs
with no doomed primary call and an honest "DeepSeek V3" (non-fallback) chip.

Covers:
  A. `_rail()` / `_brief_rail()` accept "deepseek"; unknown still → anthropic;
  B. run_chat(LLM_RAIL=deepseek) — ONE provider event {deepseek, fallback:false},
     answer delivered, NO Anthropic call, no ProviderFailover / restart;
  C. image turn under deepseek-primary → vision_unavailable, never reaches model;
  D. deepseek-primary failure → classified error, no restart loop;
  E. briefing deepseek-primary → model deepseek-chat, fallback False, LIGHT note
     present, "the usual model was unavailable" ABSENT;
  F. briefing deepseek-primary FAILURE → BriefingError, no self-"fallback";
  G. /healthz(LLM_RAIL=deepseek) → rail/model deepseek, no key leak.

Run:
    backend/.venv/bin/python scripts/test_074_deepseek_primary.py
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
PROP_BE = os.path.join(REPO, ".proposed_changes", "074-deepseek-primary", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["agent.py", "briefing.py", "main.py", ".env.example"]
_created: list[str] = []  # 074 has no net-new backend files, but keep the guard


def apply_proposal(backup_dir: str) -> None:
    for f in OVERWRITE:
        shutil.copy2(os.path.join(BACKEND, f), os.path.join(backup_dir, f))
        shutil.copy2(os.path.join(PROP_BE, f), os.path.join(BACKEND, f))


def restore(backup_dir: str) -> None:
    for f in OVERWRITE:
        b = os.path.join(backup_dir, f)
        if os.path.isfile(b):
            shutil.copy2(b, os.path.join(BACKEND, f))
    for p in _created:
        if os.path.isfile(p):
            os.remove(p)


async def drain(agen):
    return [ev async for ev in agen]


def _clear(*names):
    for n in names:
        os.environ.pop(n, None)


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ.setdefault("USE_MOCK_MARKET", "1")
    os.environ.setdefault("USE_MOCK_BROKER", "1")
    os.environ["WIDGET_VALIDATOR_MODE"] = "off"
    # DeepSeek real path active (mock off + a key) so it is the usable primary.
    os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test"
    os.environ["USE_MOCK_DEEPSEEK"] = "0"

    import deepseek_client
    import openai_client
    import agent
    import briefing

    # ---------------------------------------------------------------- A
    print("\nA. rail select accepts deepseek")
    os.environ["LLM_RAIL"] = "deepseek"
    check("_rail() → deepseek", agent._rail() == "deepseek")
    check("_brief_rail() → deepseek", briefing._brief_rail() == "deepseek")
    os.environ["LLM_RAIL"] = "nonsense"
    check("unknown still fails safe to anthropic", agent._rail() == "anthropic")
    os.environ["LLM_RAIL"] = "deepseek"

    # ---------------------------------------------------------------- B
    print("\nB. run_chat runs DeepSeek directly as primary")

    async def _ds_ok(system, messages, tools=None, max_tokens=None):
        return {"text": "Answer from DeepSeek.", "tool_calls": [],
                "usage": {"input_tokens": 3, "output_tokens": 2}}

    deepseek_client.complete = _ds_ok

    # Trip-wire: the Anthropic SDK client must never be constructed this turn.
    anthropic_called = {"n": 0}
    real_get_client = agent._get_client

    def _boom():
        anthropic_called["n"] += 1
        raise AssertionError("Anthropic client constructed on a DeepSeek-primary turn")

    agent._get_client = _boom
    try:
        evs = asyncio.run(drain(agent.run_chat("hi", "u1")))
    finally:
        agent._get_client = real_get_client

    prov = [e for e in evs if e["event"] == "provider"]
    check("exactly ONE provider event", len(prov) == 1, f"got {len(prov)}")
    check("provider is deepseek, NOT a fallback",
          prov and prov[0]["data"]["provider"] == "deepseek"
          and prov[0]["data"]["fallback"] is False
          and prov[0]["data"]["model"] == "deepseek-chat")
    check("DeepSeek's answer delivered", any(e["event"] == "message" for e in evs))
    check("no Anthropic call made", anthropic_called["n"] == 0)
    check("done event present, no restart", any(e["event"] == "done" for e in evs))

    # ---------------------------------------------------------------- C
    print("\nC. image turn under deepseek-primary is refused, not dropped")
    reached = {"model": False}

    async def _ds_should_not_run(system, messages, tools=None, max_tokens=None):
        reached["model"] = True
        return {"text": "x", "tool_calls": [], "usage": {}}

    deepseek_client.complete = _ds_should_not_run
    evs = asyncio.run(drain(agent.run_chat(
        "read this", "u1", attachments=[{"media_type": "image/png", "data": "QUJD"}])))
    err = [e for e in evs if e["event"] == "error"]
    check("vision_unavailable error", err and err[0]["data"]["code"] == "vision_unavailable")
    check("model never reached", reached["model"] is False)
    check("no message emitted on a refused turn", not any(e["event"] == "message" for e in evs))
    deepseek_client.complete = _ds_ok

    # ---------------------------------------------------------------- D
    print("\nD. deepseek-primary failure surfaces an error, no restart loop")

    async def _ds_fail(system, messages, tools=None, max_tokens=None):
        raise deepseek_client.DeepSeekError("boom")

    deepseek_client.complete = _ds_fail
    evs = asyncio.run(drain(agent.run_chat("hi", "u1")))
    check("error surfaced", any(e["event"] == "error" for e in evs))
    check("no second provider event (no failover)",
          len([e for e in evs if e["event"] == "provider"]) == 1)
    deepseek_client.complete = _ds_ok

    # ---------------------------------------------------------------- E
    print("\nE. briefing written by DeepSeek as a chosen primary")

    async def _ds_brief(system, messages, tools=None, max_tokens=None):
        return {"text": "Morning brief prose.", "tool_calls": [], "usage": {}}

    deepseek_client.complete = _ds_brief
    os.environ["USE_MOCK_BRIEFING"] = "0"
    snap = {"is_mock": False, "base_currency": "HKD", "as_of": "2026-07-01",
            "account_id": "U1", "nav": {"total": 100.0}, "holdings": []}
    out = asyncio.run(briefing.generate_briefing(snap, None))
    check("model is deepseek-chat", out["model"] == "deepseek-chat")
    check("NOT flagged as a fallback", out["fallback"] is False)
    check("light attribution present", "_Written by deepseek-chat._" in out["text"])
    check("no false 'usual model was unavailable' line",
          "the usual model was unavailable" not in out["text"])

    # ---------------------------------------------------------------- F
    print("\nF. briefing deepseek-primary failure raises, no self-fallback")
    ds_calls = {"n": 0}

    async def _ds_brief_fail(system, messages, tools=None, max_tokens=None):
        ds_calls["n"] += 1
        raise deepseek_client.DeepSeekError("429 insufficient_quota")

    deepseek_client.complete = _ds_brief_fail
    os.environ["BRIEFING_FALLBACK_ENABLED"] = "1"  # even armed, must NOT self-retry
    raised = False
    try:
        asyncio.run(briefing.generate_briefing(snap, None))
    except briefing.BriefingError:
        raised = True
    check("BriefingError raised", raised)
    check("DeepSeek called exactly ONCE (no pointless fallback to itself)",
          ds_calls["n"] == 1, f"called {ds_calls['n']}x")
    _clear("BRIEFING_FALLBACK_ENABLED")
    _clear("USE_MOCK_BRIEFING")

    # ---------------------------------------------------------------- G
    print("\nG. /healthz reflects the deepseek primary, leaks nothing")
    SENTINEL = "sk-ds-SENTINELVALUE"
    os.environ["DEEPSEEK_API_KEY"] = SENTINEL
    import main as main_mod
    payload = asyncio.run(main_mod.healthz())
    check("rail == deepseek", payload.get("rail") == "deepseek")
    check("model == deepseek-chat", payload.get("model") == "deepseek-chat")
    check("deepseek_key_present is True bool",
          payload.get("deepseek_key_present") is True)
    check("no key material leaked", "SENTINEL" not in json.dumps(payload))


def main() -> None:
    backup = tempfile.mkdtemp(prefix="074-backup-")
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
