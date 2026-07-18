#!/usr/bin/env python3
"""Offline guard for Proposal 069 phase 2 — DeepSeek failover wiring.

Network-free (mocked deepseek_client.complete + _call_tool). Covers:
  A. _failover_reason / _should_failover — billing/rate_limit/overloaded fail over;
     auth/bad-request do NOT; gated by LLM_FALLBACK_ENABLED + key + can_fall_back
     (image turns never fail over).
  B. _compact_for_llm — account_id redacted from LLM context; screenshot strip kept.
  C. run_agent_deepseek — scripted (tool turn → terminal widget): emits thought/
     tool_call/tool_result/widget/done; the SHARED 067 finalizer runs (enforce
     passes a sourced widget, blocks a fabricated one).
  D. run_chat — emits provider(anthropic); on ProviderFailover emits provider(
     deepseek, fallback) then runs the DeepSeek loop; no failover → no 2nd provider.

Self-contained: temp-applies backend/{agent.py, deepseek_client.py} over live
(deepseek_client is new → deleted on cleanup), asserts, restores in a finally.
Anchored on backend/auth.py.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_069_phase2_failover.py
"""
import asyncio
import os
import shutil
import sys

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
PROP = os.path.join(REPO, ".proposed_changes", "069-deepseek-rail")
FILES = [
    (os.path.join(BACKEND, "deepseek_client.py"), os.path.join(PROP, "backend", "deepseek_client.py")),
    (os.path.join(BACKEND, "agent.py"), os.path.join(PROP, "backend", "agent.py")),
]

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


async def _collect(agen):
    return [ev async for ev in agen]


def _events(gen):
    return asyncio.run(_collect(gen))


def _arm_fallback():
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    os.environ["DEEPSEEK_API_KEY"] = "sk-real"
    os.environ.pop("USE_MOCK_DEEPSEEK", None)
    os.environ.pop("LLM_FAILOVER_ON", None)


def run():
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    import agent

    print("\n=== A. failover decision ===")
    _arm_fallback()
    billing = Exception("Error code: 400 - your credit balance is too low")
    rate = Exception("Error code: 429 rate limit exceeded")
    auth = Exception("Error code: 401 authentication_error")
    check("billing → reason 'billing'", agent._failover_reason(billing) == "billing")
    check("rate → reason 'rate_limit'", agent._failover_reason(rate) == "rate_limit")
    check("auth → no reason", agent._failover_reason(auth) is None)
    check("should_failover(billing, no images)", agent._should_failover(billing, None) == "billing")
    check("should NOT fail over with image attachment",
          agent._should_failover(billing, [{"media_type": "image/png", "data": "x"}]) is None)
    check("auth never fails over", agent._should_failover(auth, None) is None)
    os.environ["LLM_FALLBACK_ENABLED"] = "0"
    check("fallback OFF → no failover", agent._should_failover(billing, None) is None)
    _arm_fallback()

    print("\n=== B. _compact_for_llm strips account_id ===")
    c = agent._compact_for_llm({"account_id": "U1234567", "total_equity": 869000, "positions": []})
    check("account_id redacted", c["account_id"] == "[redacted]")
    check("other fields untouched", c["total_equity"] == 869000)
    check("no account_id → unchanged", agent._compact_for_llm({"x": 1}) == {"x": 1})

    print("\n=== C. run_agent_deepseek (scripted) ===")
    # a tool turn, then a terminal widget whose numbers trace to the tool result
    calls = {"n": 0}

    async def fake_complete(system, messages, tools=None, max_tokens=4096):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": "", "usage": {"input_tokens": 5, "output_tokens": 2},
                    "tool_calls": [{"id": "c1", "name": "get_quote", "input": {"ticker": "NVDA"}}]}
        return {"text": '```json\n{"type":"research_card","data":{"current_price":942.5,"target_price":1100}}\n```',
                "usage": {"input_tokens": 7, "output_tokens": 30}, "tool_calls": []}

    async def fake_call_tool(name, args, user_id):
        return True, {"ticker": "NVDA", "price": 942.5, "median_target": 1100}

    orig_complete = agent.deepseek_client.complete
    orig_call = agent._call_tool
    agent.deepseek_client.complete = fake_complete
    agent._call_tool = fake_call_tool
    os.environ["WIDGET_VALIDATOR_MODE"] = "enforce"
    try:
        evs = _events(agent.run_agent_deepseek("tldr on NVDA", "u1"))
        kinds = [e["event"] for e in evs]
        check("emits thought", "thought" in kinds)
        check("emits tool_call + tool_result", "tool_call" in kinds and "tool_result" in kinds)
        widgets = [e for e in evs if e["event"] == "widget"]
        check("emits a validated widget (enforce, numbers sourced)", len(widgets) == 1, str(kinds))
        check("no widget_unverified error", not any(e.get("data", {}).get("code") == "widget_unverified" for e in evs))
        done = [e for e in evs if e["event"] == "done"]
        check("emits done with tokens", bool(done) and done[0]["data"]["output_tokens"] == 32,
              str(done[0]["data"] if done else None))

        # fabricated number → enforce blocks the widget
        calls["n"] = 0

        async def fake_complete_bad(system, messages, tools=None, max_tokens=4096):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"text": "", "usage": {}, "tool_calls": [{"id": "c1", "name": "get_quote", "input": {"ticker": "NVDA"}}]}
            return {"text": '```json\n{"type":"research_card","data":{"current_price":1234.56,"target_price":1100}}\n```',
                    "usage": {}, "tool_calls": []}

        agent.deepseek_client.complete = fake_complete_bad
        evs2 = _events(agent.run_agent_deepseek("tldr", "u1"))
        check("fabricated price → widget_unverified (shared 067 finalizer)",
              any(e.get("data", {}).get("code") == "widget_unverified" for e in evs2)
              and not any(e["event"] == "widget" for e in evs2))
    finally:
        agent.deepseek_client.complete = orig_complete
        agent._call_tool = orig_call
        os.environ["WIDGET_VALIDATOR_MODE"] = "warn"

    print("\n=== D. run_chat failover orchestration ===")
    async def _raise_failover(*a, **k):
        raise agent.ProviderFailover("billing")
        yield  # make it an async generator

    async def _normal(*a, **k):
        yield {"event": "message", "data": {"text": "hi from claude"}}
        yield {"event": "done", "data": {"elapsed_ms": 1, "iterations": 1, "input_tokens": 1, "output_tokens": 1}}

    orig_run_agent = agent.run_agent
    orig_ds = agent.run_agent_deepseek
    try:
        # failover path — real deepseek loop, scripted
        async def ds_stub(*a, **k):
            yield {"event": "message", "data": {"text": "hi from deepseek"}}
            yield {"event": "done", "data": {"elapsed_ms": 1, "iterations": 1, "input_tokens": 2, "output_tokens": 3}}
        agent.run_agent = _raise_failover
        agent.run_agent_deepseek = ds_stub
        evs = _events(agent.run_chat("q", "u1"))
        provs = [e for e in evs if e["event"] == "provider"]
        check("provider(anthropic) emitted first", provs and provs[0]["data"]["provider"] == "anthropic" and provs[0]["data"]["fallback"] is False)
        check("provider(deepseek, fallback) emitted on failover",
              len(provs) == 2 and provs[1]["data"]["provider"] == "deepseek" and provs[1]["data"]["fallback"] is True
              and provs[1]["data"]["reason"] == "anthropic_billing")
        check("deepseek output forwarded", any(e["event"] == "message" and e["data"]["text"] == "hi from deepseek" for e in evs))

        # no-failover path — anthropic completes, no 2nd provider event
        agent.run_agent = _normal
        evs = _events(agent.run_chat("q", "u1"))
        provs = [e for e in evs if e["event"] == "provider"]
        check("no failover → single provider(anthropic) event", len(provs) == 1 and provs[0]["data"]["provider"] == "anthropic")
        check("anthropic output forwarded", any(e["event"] == "message" and e["data"]["text"] == "hi from claude" for e in evs))
    finally:
        agent.run_agent = orig_run_agent
        agent.run_agent_deepseek = orig_ds


def main():
    for k in ("LLM_FALLBACK_ENABLED", "DEEPSEEK_API_KEY", "USE_MOCK_DEEPSEEK", "LLM_FAILOVER_ON", "WIDGET_VALIDATOR_MODE"):
        os.environ.pop(k, None)
    backups = []
    try:
        for live, prop in FILES:
            if not os.path.isfile(prop):
                print(f"missing: {prop}"); return 1
            existed = os.path.isfile(live)
            bak = live + ".069p2bak"
            if existed:
                shutil.copy2(live, bak)
            backups.append((live, bak, existed))
            shutil.copy2(prop, live)
        run()
    finally:
        for live, bak, existed in backups:
            if existed:
                shutil.copy2(bak, live); os.remove(bak)
            elif os.path.isfile(live):
                os.remove(live)
        for k in ("LLM_FALLBACK_ENABLED", "DEEPSEEK_API_KEY", "USE_MOCK_DEEPSEEK", "LLM_FAILOVER_ON", "WIDGET_VALIDATOR_MODE"):
            os.environ.pop(k, None)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
