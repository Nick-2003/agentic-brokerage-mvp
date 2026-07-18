#!/usr/bin/env python3
"""Offline guard for Proposal 069 phase 1 — DeepSeek client + translators.

Network-free (fake httpx). Covers:
  A. gating — deepseek_available() / fallback_enabled() / can_fall_back(attachments)
  B. to_openai_tools — Anthropic {name,description,input_schema} → OpenAI function shape
  C. to_openai_messages — neutral history (incl. tool_use / tool) → OpenAI messages
  D. _parse_choice — OpenAI response (text + tool_calls) → the loop's uniform shape,
     incl. multiple/parallel tool_calls and malformed-args tolerance
  E. complete() — mock path (no key) is tool-less + deterministic; real path posts
     and parses (fake httpx), and raises DeepSeekError on transport failure
  F. vision guard — a turn with attachments is NOT fall-back-eligible

Self-contained: the client is a standalone module (no live counterpart), so this
copies it onto sys.path from the proposal, asserts, and touches no live file.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_069_deepseek_client.py
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROP_BACKEND = os.path.join(HERE, os.pardir, "backend")  # 069's backend mirror
sys.path.insert(0, PROP_BACKEND)

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self._payload


class _FakeClient:
    payload = {}
    def __init__(self, *a, **k):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def post(self, url, headers=None, json=None):
        _FakeClient.last_url = url
        _FakeClient.last_body = json
        return _FakeResp(_FakeClient.payload)


def run() -> None:
    import deepseek_client as ds

    print("\n=== A. gating ===")
    for k in ("USE_MOCK_DEEPSEEK", "DEEPSEEK_API_KEY", "LLM_FALLBACK_ENABLED"):
        os.environ.pop(k, None)
    check("no key → not available", ds.deepseek_available() is False)
    os.environ["DEEPSEEK_API_KEY"] = "REPLACE"
    check("REPLACE sentinel → not available", ds.deepseek_available() is False)
    os.environ["DEEPSEEK_API_KEY"] = "sk-real"
    check("real key → available", ds.deepseek_available() is True)
    os.environ["USE_MOCK_DEEPSEEK"] = "1"
    check("mock forced → not available", ds.deepseek_available() is False)
    check("fallback OFF by default", ds.fallback_enabled() is False)
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    check("fallback ON when set", ds.fallback_enabled() is True)

    print("\n=== B. to_openai_tools ===")
    specs = [{"name": "get_quote", "description": "Live quote.",
              "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}}]
    ot = ds.to_openai_tools(specs)
    check("wrapped as function", ot[0]["type"] == "function" and ot[0]["function"]["name"] == "get_quote")
    check("input_schema → parameters", ot[0]["function"]["parameters"]["required"] == ["ticker"])

    print("\n=== C. to_openai_messages ===")
    neutral = [
        {"role": "user", "content": "quotes for NVDA and AMD?"},
        {"role": "assistant", "tool_calls": [{"id": "c1", "name": "get_quote", "input": {"ticker": "NVDA"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"price": 942.5}'},
    ]
    om = ds.to_openai_messages("SYS", neutral)
    check("system prepended", om[0] == {"role": "system", "content": "SYS"})
    asst = om[2]
    check("assistant tool_calls translated", asst["tool_calls"][0]["function"]["name"] == "get_quote"
          and asst["tool_calls"][0]["function"]["arguments"] == '{"ticker": "NVDA"}')
    check("tool result → role:tool with id", om[3]["role"] == "tool" and om[3]["tool_call_id"] == "c1")

    print("\n=== D. _parse_choice ===")
    resp = {"choices": [{"message": {"content": "here", "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "get_quote", "arguments": '{"ticker":"NVDA"}'}},
        {"id": "b", "type": "function", "function": {"name": "get_quote", "arguments": '{"ticker":"AMD"}'}},
        {"id": "c", "type": "function", "function": {"name": "x", "arguments": "not json"}},
    ]}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
    p = ds._parse_choice(resp)
    check("text extracted", p["text"] == "here")
    check("parallel tool_calls preserved (3)", len(p["tool_calls"]) == 3)
    check("args parsed to dict", p["tool_calls"][0]["input"] == {"ticker": "NVDA"})
    check("malformed args → {} (no crash)", p["tool_calls"][2]["input"] == {})
    check("usage mapped to input/output tokens", p["usage"] == {"input_tokens": 11, "output_tokens": 7})

    print("\n=== E. complete() ===")
    # mock path (no real key)
    os.environ["USE_MOCK_DEEPSEEK"] = "1"
    m = asyncio.run(ds.complete("s", [{"role": "user", "content": "hi"}]))
    check("mock reply is tool-less + labelled", m["tool_calls"] == [] and "mock DeepSeek" in m["text"])

    # real path via fake httpx
    os.environ["USE_MOCK_DEEPSEEK"] = "0"
    os.environ["DEEPSEEK_API_KEY"] = "sk-real"
    _FakeClient.payload = {"choices": [{"message": {"content": "real", "tool_calls": []}}],
                           "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    orig = ds.httpx.AsyncClient
    ds.httpx.AsyncClient = _FakeClient
    try:
        r = asyncio.run(ds.complete("SYS", [{"role": "user", "content": "hi"}],
                                    tools=ds.to_openai_tools(specs)))
        check("real path parses response", r["text"] == "real" and r["usage"]["input_tokens"] == 3)
        check("posts to /chat/completions", _FakeClient.last_url.endswith("/chat/completions"))
        check("tools + tool_choice in body", _FakeClient.last_body.get("tool_choice") == "auto" and "tools" in _FakeClient.last_body)
        check("system folded into messages", _FakeClient.last_body["messages"][0] == {"role": "system", "content": "SYS"})

        class _Boom(_FakeClient):
            async def post(self, *a, **k):
                raise ds.httpx.ConnectError("down")
        ds.httpx.AsyncClient = _Boom
        try:
            asyncio.run(ds.complete("s", [{"role": "user", "content": "hi"}]))
            check("transport failure → DeepSeekError", False, "no raise")
        except ds.DeepSeekError:
            check("transport failure → DeepSeekError", True)
    finally:
        ds.httpx.AsyncClient = orig

    print("\n=== F. vision guard ===")
    check("no attachments → can fall back", ds.can_fall_back(None) is True and ds.can_fall_back([]) is True)
    check("image attachment → CANNOT fall back", ds.can_fall_back([{"media_type": "image/png", "data": "x"}]) is False)


def main() -> int:
    for k in ("USE_MOCK_DEEPSEEK", "DEEPSEEK_API_KEY", "LLM_FALLBACK_ENABLED"):
        os.environ.pop(k, None)
    try:
        run()
    finally:
        for k in ("USE_MOCK_DEEPSEEK", "DEEPSEEK_API_KEY", "LLM_FALLBACK_ENABLED"):
            os.environ.pop(k, None)
    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
