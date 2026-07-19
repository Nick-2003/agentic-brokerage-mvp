#!/usr/bin/env python3
"""Offline guard for Proposal 070 — DeepSeek fallback for the daily brief.

069 gave the CHAT a fallback; `briefing.py` builds its own Anthropic client and was
untouched, so the WhatsApp/email brief still died whenever Anthropic was usage-
limited. 070 re-issues the same tool-less system+facts prompt to DeepSeek.

Network-free (Anthropic + DeepSeek both mocked). Covers:
  A. gating — _brief_fallback_enabled() inherits LLM_FALLBACK_ENABLED, and
     BRIEFING_FALLBACK_ENABLED overrides it (so chat can fall back while the
     brief does not);
  B. _is_usage_limit_error — billing/rate-limit/overload yes; auth/bad-request no;
  C. happy path unchanged — Anthropic succeeds → no fallback, model = Anthropic;
  D. failover — Anthropic usage-limited → DeepSeek writes it, `fallback: True`,
     model = deepseek, and the DELIVERED TEXT discloses the model;
  E. non-eligible error (auth) still raises BriefingError — no silent fallback;
  F. fallback disabled → raises BriefingError even on a billing error;
  G. DeepSeek also failing → BriefingError naming both failures;
  H. empty DeepSeek text → BriefingError (never sends a blank brief).

Run with the backend venv (against the LIVE backend, per the post-apply pattern):
    backend/.venv/bin/python scripts/test_070_briefing_fallback.py
"""
import asyncio
import os
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

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


SNAP = {
    "is_mock": False,
    "base_currency": "HKD",
    "as_of": "2026-07-01",
    "account_id": "U123",
    "nav": {"total": 878160.76},
    "change_in_nav": {"starting": 878008.88, "ending": 878160.76},
    "positions": [],
}


class _FakeAnthropicOK:
    class messages:
        @staticmethod
        async def create(**kw):
            class B:
                type = "text"
                text = "Your IBKR book is flat overnight."
            class R:
                content = [B()]
            return R()


def _fake_anthropic_raising(exc):
    class _C:
        class messages:
            @staticmethod
            async def create(**kw):
                raise exc
    return _C()


def run():
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    import briefing as B
    import deepseek_client as ds

    # Force the REAL (non-mock) generation path for these tests.
    os.environ["USE_MOCK_BRIEFING"] = "0"
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"

    billing = Exception("Error code: 400 - your credit balance is too low")
    auth = Exception("Error code: 401 authentication_error")

    print("\n=== A. gating ===")
    os.environ.pop("BRIEFING_FALLBACK_ENABLED", None)
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    check("inherits LLM_FALLBACK_ENABLED=1", B._brief_fallback_enabled() is True)
    os.environ["LLM_FALLBACK_ENABLED"] = "0"
    check("inherits LLM_FALLBACK_ENABLED=0", B._brief_fallback_enabled() is False)
    os.environ["BRIEFING_FALLBACK_ENABLED"] = "1"
    check("explicit override ON beats master OFF", B._brief_fallback_enabled() is True)
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    os.environ["BRIEFING_FALLBACK_ENABLED"] = "0"
    check("explicit override OFF beats master ON (chat-only fallback)",
          B._brief_fallback_enabled() is False)

    print("\n=== B. _is_usage_limit_error ===")
    check("billing → True", B._is_usage_limit_error(billing) is True)
    check("rate limit → True", B._is_usage_limit_error(Exception("429 rate limit")) is True)
    check("auth → False", B._is_usage_limit_error(auth) is False)

    # arm the fallback for the rest
    os.environ["BRIEFING_FALLBACK_ENABLED"] = "1"
    os.environ["DEEPSEEK_API_KEY"] = "sk-real"
    os.environ["USE_MOCK_DEEPSEEK"] = "0"

    orig_client, orig_complete = B._get_client, ds.complete
    try:
        print("\n=== C. happy path (Anthropic succeeds) ===")
        B._get_client = lambda: _FakeAnthropicOK()
        out = asyncio.run(B.generate_briefing(SNAP))
        check("no fallback flag", out["fallback"] is False)
        check("model is the Anthropic model", out["model"] == B._model(), out["model"])
        check("text has no fallback disclosure", "Written by" not in out["text"])

        print("\n=== D. failover to DeepSeek ===")
        B._get_client = lambda: _fake_anthropic_raising(billing)

        async def ds_ok(system, messages, tools=None, max_tokens=1024):
            return {"text": "Your IBKR book is flat overnight (deepseek).",
                    "tool_calls": [], "usage": {"input_tokens": 5, "output_tokens": 9}}

        ds.complete = ds_ok
        out = asyncio.run(B.generate_briefing(SNAP))
        check("fallback flag set", out["fallback"] is True)
        check("model is the DeepSeek model", out["model"] == ds.deepseek_model(), out["model"])
        check("brief text came from DeepSeek", "deepseek" in out["text"])
        check("DELIVERED text discloses the model (recipient can't see logs)",
              "Written by" in out["text"] and ds.deepseek_model() in out["text"],
              out["text"][-90:])
        check("discloses figures are unchanged", "Figures are unchanged" in out["text"])

        print("\n=== E/F/G/H. refusals ===")
        B._get_client = lambda: _fake_anthropic_raising(auth)
        try:
            asyncio.run(B.generate_briefing(SNAP)); check("auth error → BriefingError", False, "no raise")
        except B.BriefingError:
            check("auth error → BriefingError (no silent fallback)", True)

        B._get_client = lambda: _fake_anthropic_raising(billing)
        os.environ["BRIEFING_FALLBACK_ENABLED"] = "0"
        try:
            asyncio.run(B.generate_briefing(SNAP)); check("fallback OFF → BriefingError", False, "no raise")
        except B.BriefingError:
            check("fallback OFF → BriefingError even on billing", True)
        os.environ["BRIEFING_FALLBACK_ENABLED"] = "1"

        async def ds_boom(system, messages, tools=None, max_tokens=1024):
            raise ds.DeepSeekError("deepseek down")
        ds.complete = ds_boom
        try:
            asyncio.run(B.generate_briefing(SNAP)); check("both providers fail → BriefingError", False, "no raise")
        except B.BriefingError as be:
            check("both providers fail → BriefingError names both",
                  "DeepSeek fallback also failed" in str(be), str(be)[:70])

        async def ds_empty(system, messages, tools=None, max_tokens=1024):
            return {"text": "   ", "tool_calls": [], "usage": {}}
        ds.complete = ds_empty
        try:
            asyncio.run(B.generate_briefing(SNAP)); check("empty fallback → BriefingError", False, "no raise")
        except B.BriefingError as be:
            check("empty fallback text → BriefingError (never send a blank brief)",
                  getattr(be, "code", "") == "briefing_empty", getattr(be, "code", ""))
    finally:
        B._get_client, ds.complete = orig_client, orig_complete


def main():
    keys = ("USE_MOCK_BRIEFING", "ANTHROPIC_API_KEY", "LLM_FALLBACK_ENABLED",
            "BRIEFING_FALLBACK_ENABLED", "DEEPSEEK_API_KEY", "USE_MOCK_DEEPSEEK")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        run()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
