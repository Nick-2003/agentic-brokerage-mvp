#!/usr/bin/env python3
"""Offline guard for Proposal 071 — OpenAI rail + LLM_RAIL select.

Network-free. Uses the repo's **temp-apply → restore** pattern: the proposal
files are copied over the live backend, asserted against, then restored in a
`finally` (net-new files deleted), so the live tree ends exactly as it started.
Verify with `git status` after running.

Covers:
  A. openai_compat translations — tool specs, neutral history, tool_calls/tool
     messages, IMAGE parts (059 shape → OpenAI `image_url` data URI), parse_choice;
  B. deepseek_client parity — the delegators produce byte-identical output to the
     pre-071 implementation (this is a refactor of a LIVE rail, so parity is the
     whole safety argument);
  C. _rail() select — anthropic default, openai honoured, unknown → anthropic;
  D. _failover_reason — OpenAI phrasings (`insufficient_quota`, "exceeded your
     current quota") classify as billing, and httpx 429/5xx map correctly. This is
     the 068 trap: Anthropic-direct markers alone would NEVER fire on a new rail;
  E. run_chat(LLM_RAIL=openai) — emits provider=openai and runs the shared loop;
  F. vision refusal — OPENAI_VISION=0 + attachments → `vision_unavailable`, never
     a silent image drop (069's non-negotiable rule, applied per-rail);
  G. OpenAI primary usage-limit → fails over to DeepSeek, announced with
     reason `openai_billing`;
  H. DeepSeek never fails over to itself (no infinite restart);
  I. briefing._brief_rail — the cron honours the same select (070's lesson);
  J. briefing on the OpenAI rail is NOT marked a fallback and carries no
     "written by" disclosure (a chosen primary is not a degradation).

Run:
    backend/.venv/bin/python scripts/test_071_openai_rail.py
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
PROP = os.path.join(REPO, ".proposed_changes", "071-openai-rail", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


# --- temp-apply / live mode --------------------------------------------------
# ⚠️ FIXED BY 073 — this used to be DESTRUCTIVE.
#
# Originally the test copied the staged proposal over the live tree and, in its
# `finally`, deleted every NET_NEW file unconditionally. That was safe only while
# 071 was unapplied. Once 071 was applied and committed, `openai_client.py` and
# `openai_compat.py` became LIVE production modules that `agent.py` imports at
# startup — and the staged dir was removed (only `applied/README-071-openai-rail.md`
# remains). So `apply_proposal` raised FileNotFoundError, `finally` still ran, and
# **merely running this test deleted two modules the backend needs to boot.**
#
# Two rules now, both worth copying into any future proposal test:
#   1. If the staged dir is gone, the proposal IS the live tree → run in LIVE
#      MODE: assert against what's installed, apply and restore nothing.
#      (Same posture `test_070_briefing_fallback` documents as "post-apply".)
#   2. Never delete a file this run did not create.
OVERWRITE = ["agent.py", "briefing.py", "deepseek_client.py"]
NET_NEW = ["openai_client.py", "openai_compat.py"]

LIVE_MODE = not os.path.isdir(PROP)
_created: list[str] = []


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 071 is applied; asserting against the LIVE tree)")
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
    for p in _created:  # ONLY what this run created
        if os.path.isfile(p):
            os.remove(p)


# The pre-071 DeepSeek translations, inlined, so section B can prove the
# delegators are a true no-op refactor rather than trusting they are.
def _legacy_to_openai_messages(system, neutral):
    out = [{"role": "system", "content": system}]
    for m in neutral:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""),
                        "content": m.get("content", "")})
        elif role == "assistant" and m.get("tool_calls"):
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": [
                            {"id": tc["id"], "type": "function",
                             "function": {"name": tc["name"],
                                          "arguments": json.dumps(tc.get("input") or {})}}
                            for tc in m["tool_calls"]]})
        else:
            out.append({"role": role or "user", "content": m.get("content", "")})
    return out


async def drain(agen):
    return [ev async for ev in agen]


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ.setdefault("USE_MOCK_MARKET", "1")
    os.environ.setdefault("USE_MOCK_BROKER", "1")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-REPLACE")
    os.environ["WIDGET_VALIDATOR_MODE"] = "off"  # 067 is exercised by test_067

    import openai_compat
    import openai_client
    import deepseek_client
    import agent
    import briefing

    # --- A. translations ----------------------------------------------------
    print("\nA. openai_compat translations")
    tools = openai_compat.to_openai_tools(
        [{"name": "get_quote", "description": "d", "input_schema": {"type": "object"}}]
    )
    check("tool spec → OpenAI function shape",
          tools == [{"type": "function", "function": {
              "name": "get_quote", "description": "d", "parameters": {"type": "object"}}}])

    msgs = openai_compat.to_openai_messages("SYS", [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [{"id": "tc1", "name": "get_quote", "input": {"ticker": "NVDA"}}]},
        {"role": "tool", "tool_call_id": "tc1", "content": "{}"},
    ])
    check("system prepended", msgs[0] == {"role": "system", "content": "SYS"})
    check("tool_calls → OpenAI function call",
          msgs[2]["tool_calls"][0]["function"]["name"] == "get_quote"
          and json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"ticker": "NVDA"})
    check("tool result → role:tool", msgs[3]["role"] == "tool" and msgs[3]["tool_call_id"] == "tc1")

    vis = openai_compat.to_openai_messages("SYS", [
        {"role": "user", "content": "what is this",
         "attachments": [{"media_type": "image/png", "data": "QUJD"}]},
    ])
    parts = vis[1]["content"]
    check("059 attachment → OpenAI image_url data URI",
          parts[0]["type"] == "text"
          and parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD")

    imgonly = openai_compat.to_openai_messages("SYS", [
        {"role": "user", "content": "", "attachments": [{"media_type": "image/png", "data": "QUJD"}]},
    ])
    check("image-only turn omits the empty text part", len(imgonly[1]["content"]) == 1)

    parsed = openai_compat.parse_choice({
        "choices": [{"message": {"content": "hey", "tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": '{"a":1}'}}]}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    })
    check("parse_choice → uniform shape",
          parsed["text"] == "hey" and parsed["tool_calls"][0]["input"] == {"a": 1}
          and parsed["usage"] == {"input_tokens": 7, "output_tokens": 3})
    check("malformed tool arguments → empty input, no crash",
          openai_compat.parse_choice({"choices": [{"message": {"tool_calls": [
              {"id": "c", "function": {"name": "f", "arguments": "{not json"}}]}}]})
          ["tool_calls"][0]["input"] == {})

    # --- B. deepseek parity -------------------------------------------------
    print("\nB. deepseek_client delegator parity (refactor safety)")
    hist = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "tool_calls": [{"id": "t", "name": "n", "input": {"x": 1}}]},
        {"role": "tool", "tool_call_id": "t", "content": "r"},
    ]
    check("to_openai_messages identical to pre-071",
          deepseek_client.to_openai_messages("S", hist) == _legacy_to_openai_messages("S", hist))
    check("deepseek still reports no vision", deepseek_client.supports_vision() is False)
    check("deepseek refuses image turns",
          deepseek_client.can_fall_back([{"media_type": "image/png", "data": "x"}]) is False)
    check("openai accepts image turns when vision on",
          openai_client.can_fall_back([{"media_type": "image/png", "data": "x"}]) is True)

    # --- C. rail select -----------------------------------------------------
    print("\nC. LLM_RAIL select")
    os.environ.pop("LLM_RAIL", None)
    check("default is anthropic (unchanged 069 behaviour)", agent._rail() == "anthropic")
    os.environ["LLM_RAIL"] = "openai"
    check("openai honoured", agent._rail() == "openai")
    os.environ["LLM_RAIL"] = "OpenAI  "
    check("case/whitespace tolerant", agent._rail() == "openai")
    os.environ["LLM_RAIL"] = "gpt-9-turbo"
    check("unknown value fails SAFE to anthropic", agent._rail() == "anthropic")
    os.environ.pop("LLM_RAIL", None)

    # --- D. failover classification on the new rail -------------------------
    print("\nD. _failover_reason covers OpenAI phrasing (the 068 trap)")
    check("insufficient_quota → billing",
          agent._failover_reason(Exception("429 insufficient_quota")) == "billing")
    check("'exceeded your current quota' → billing",
          agent._failover_reason(Exception("You exceeded your current quota")) == "billing")
    check("Anthropic credit-balance still → billing",
          agent._failover_reason(Exception("Your credit balance is too low")) == "billing")
    check("auth error → None (must stay loud)",
          agent._failover_reason(Exception("invalid_api_key: incorrect api key")) is None)

    class _Resp:
        def __init__(self, s): self.status_code = s

    class _HttpErr(Exception):
        def __init__(self, s): super().__init__(f"http {s}"); self.response = _Resp(s)

    check("httpx 429 → rate_limit", agent._failover_reason(_HttpErr(429)) == "rate_limit")
    check("httpx 503 → overloaded", agent._failover_reason(_HttpErr(503)) == "overloaded")
    check("httpx 400 → None", agent._failover_reason(_HttpErr(400)) is None)

    # --- E/F/G/H. run_chat behaviour ---------------------------------------
    print("\nE–H. run_chat rail dispatch, vision refusal, failover")
    os.environ["LLM_RAIL"] = "openai"
    os.environ["OPENAI_API_KEY"] = "sk-test"
    os.environ["USE_MOCK_OPENAI"] = "0"
    os.environ["OPENAI_MODEL"] = "gpt-5"
    os.environ["OPENAI_VISION"] = "1"

    async def _ok(system, messages, tools=None, max_tokens=4096):
        return {"text": "All good.", "tool_calls": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

    openai_client.complete = _ok
    evs = asyncio.run(drain(agent.run_chat("hi", "u1")))
    prov = [e for e in evs if e["event"] == "provider"]
    check("provider event names the openai rail",
          prov and prov[0]["data"]["provider"] == "openai"
          and prov[0]["data"]["model"] == "gpt-5"
          and prov[0]["data"]["fallback"] is False)
    check("openai rail produces a terminal message",
          any(e["event"] == "message" for e in evs))
    check("no failover announced on success", len(prov) == 1)

    # F — vision off + attachment must REFUSE, not drop
    os.environ["OPENAI_VISION"] = "0"
    evs = asyncio.run(drain(agent.run_chat(
        "read this", "u1", attachments=[{"media_type": "image/png", "data": "QUJD"}])))
    err = [e for e in evs if e["event"] == "error"]
    check("vision-off + image → explicit refusal",
          err and err[0]["data"]["code"] == "vision_unavailable")
    check("refused turn never reaches the model",
          not any(e["event"] == "message" for e in evs))
    os.environ["OPENAI_VISION"] = "1"

    # G — OpenAI usage-limited → DeepSeek
    async def _quota(system, messages, tools=None, max_tokens=4096):
        raise openai_client.OpenAIError("openai request failed: 429 — insufficient_quota")

    async def _ds_ok(system, messages, tools=None, max_tokens=4096):
        return {"text": "From DeepSeek.", "tool_calls": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

    openai_client.complete = _quota
    deepseek_client.complete = _ds_ok
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test"
    os.environ["USE_MOCK_DEEPSEEK"] = "0"

    evs = asyncio.run(drain(agent.run_chat("hi", "u1")))
    prov = [e for e in evs if e["event"] == "provider"]
    check("failover announced as a second provider event", len(prov) == 2)
    check("fallback event names deepseek + the openai reason",
          len(prov) == 2 and prov[1]["data"]["fallback"] is True
          and prov[1]["data"]["provider"] == "deepseek"
          and prov[1]["data"]["reason"] == "openai_billing")
    check("DeepSeek's answer is delivered",
          any(e["event"] == "message" for e in evs))

    # H — DeepSeek must not fail over to itself
    async def _ds_quota(system, messages, tools=None, max_tokens=4096):
        raise deepseek_client.DeepSeekError("insufficient_quota")

    deepseek_client.complete = _ds_quota
    evs = asyncio.run(drain(agent.run_agent_deepseek("hi", "u1")))
    check("DeepSeek failure surfaces an error, never a restart loop",
          any(e["event"] == "error" for e in evs)
          and any(e["event"] == "done" for e in evs))

    # --- I/J. briefing ------------------------------------------------------
    print("\nI–J. briefing honours the same rail (070's lesson)")
    os.environ["LLM_RAIL"] = "openai"
    check("briefing rail select mirrors chat", briefing._brief_rail() == "openai")
    os.environ["LLM_RAIL"] = "nonsense"
    check("briefing unknown rail → anthropic", briefing._brief_rail() == "anthropic")
    os.environ["LLM_RAIL"] = "openai"
    check("OpenAI quota phrasing classifies as a usage limit for the brief too",
          briefing._is_usage_limit_error(Exception("429 insufficient_quota")) is True)
    check("brief auth error still NOT a usage limit",
          briefing._is_usage_limit_error(Exception("invalid_api_key")) is False)

    async def _oa_brief(system, messages, tools=None, max_tokens=1024):
        return {"text": "Morning brief prose.", "tool_calls": [], "usage": {}}

    openai_client.complete = _oa_brief
    os.environ["USE_MOCK_BRIEFING"] = "0"
    snap = {"is_mock": False, "base_currency": "HKD", "as_of": "2026-07-01",
            "account_id": "U1", "nav": {"total": 100.0}, "holdings": []}
    out = asyncio.run(briefing.generate_briefing(snap, None))
    check("brief written by the chosen OpenAI primary", out["model"] == "gpt-5")
    check("a chosen primary is NOT flagged as a fallback", out["fallback"] is False)
    check("no 'written by' disclosure on a chosen primary",
          "the usual model was unavailable" not in out["text"])


def main() -> None:
    backup = tempfile.mkdtemp(prefix="071-backup-")
    try:
        apply_proposal(backup)
        run()
    finally:
        restore(backup)
        shutil.rmtree(backup, ignore_errors=True)

    total, ok = len(results), sum(results)
    print(f"\n{'='*60}\n  {ok}/{total} checks passed\n{'='*60}")
    print("Live tree restored — confirm with: git status --short")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
