#!/usr/bin/env python3
"""Offline guard for Proposal 083 — handling the Kimi failure error.

Network-free. Temp-apply → assert → restore-in-`finally`, with the 078 LIVE-MODE
and non-destructive (`_created`) guards. Confirm with `git status` after running.

The bug this exists for, seen live on 2026-07-24 (two PostHog `chat_error` events):

    Error: KimiError: kimi request failed:

Nothing after the colon, on the funded PRIMARY rail. Three separate defects
combined to produce it, and this suite pins all three:

  1. `str(httpx.ReadTimeout(...))` is the EMPTY STRING — httpx maps timeouts from a
     message-less httpcore exception — so `f"kimi request failed: {e}"` rendered
     nothing, and `detail` was empty too because a timeout has no `.response`.
  2. `KIMI_TIMEOUT_S=60` was a per-attempt read timeout sitting right at the
     measured working latency (~55s/iteration; the successful turns in the same
     session took 116s and 117s).
  3. A transport failure matched NONE of `_failover_reason`'s cases (no status, no
     billing prose), so the turn died with a funded DeepSeek rail sitting unused.

Covers:
  A. an error is NEVER empty — the exception class is always named, a message-less
     timeout gets a synthesised description, an HTTP body is still preserved;
  B. `classify_transport_error` across every class, from the exception TYPE;
  C. `post_chat_completion` — retries a transient failure, does NOT retry a 400,
     and the budget is a DEADLINE that bounds total wall clock across attempts;
  D. agent-side: `_transport_reason` (hint AND `__cause__` backstop), billing still
     beats a hint, `_should_failover`, and the user-facing copy names the ACTIVE
     rail rather than a hardcoded "Anthropic";
  E. end-to-end — a timing-out Kimi turn now FAILS OVER to DeepSeek and answers,
     and with `LLM_FAILOVER_ON` restricted it fails loudly with a readable message;
  F. structural — no rail keeps a private copy of the transport policy.

Run:
    backend/.venv/bin/python scripts/test_083_kimi_failure_handling.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
import types

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
PROP = os.path.join(REPO, ".proposed_changes", "083-kimi-failure-handling", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["agent.py", "kimi_client.py", "deepseek_client.py", "openai_client.py", ".env.example"]
NET_NEW = ["llm_transport.py"]
_created: list[str] = []
# 078 rule: staged dir gone ⇒ the proposal IS the live tree. Assert, apply nothing.
LIVE_MODE = not os.path.isdir(PROP)


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 083 is applied; asserting against the LIVE tree)")
        return
    for f in OVERWRITE:
        shutil.copy2(os.path.join(BACKEND, f), os.path.join(backup_dir, f))
        shutil.copy2(os.path.join(PROP, f), os.path.join(BACKEND, f))
    for f in NET_NEW:
        dst = os.path.join(BACKEND, f)
        if os.path.isfile(dst):
            # 078 rule: never delete a file this run did not create.
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


async def drain(agen):
    return [ev async for ev in agen]


# --- fake transport -----------------------------------------------------------
# A scripted AsyncClient: each queued item is either an exception to raise or a
# (status, body) response. Lets the retry/deadline logic be exercised with no
# network and no real waiting.


class _FakeResponse:
    def __init__(self, status: int, body: str, payload=None):
        self.status_code = status
        self.text = body
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        import httpx
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")
            raise httpx.HTTPStatusError(
                f"{self.status_code} error", request=req,
                response=httpx.Response(self.status_code, text=self.text, request=req),
            )


class _FakeClient:
    script: list = []
    calls: list = []
    clock: list = [0.0]      # advanced by each scripted step
    step_cost: list = [0.0]  # seconds each attempt "takes"

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.calls.append({"url": url, "timeout": self.timeout})
        _FakeClient.clock[0] += _FakeClient.step_cost[0]
        item = _FakeClient.script.pop(0) if _FakeClient.script else _FakeResponse(200, "{}", {"ok": True})
        if isinstance(item, BaseException):
            raise item
        return item


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ.setdefault("USE_MOCK_MARKET", "1")
    os.environ.setdefault("USE_MOCK_BROKER", "1")
    os.environ["WIDGET_VALIDATOR_MODE"] = "off"
    os.environ["CHAT_VERBOSE_ERRORS"] = "1"
    os.environ["LLM_RAIL"] = "kimi"
    os.environ["KIMI_API_KEY"] = "sk-kimi-test"
    os.environ["USE_MOCK_KIMI"] = "0"
    os.environ["KIMI_MODEL"] = "kimi-k2.6"
    os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test"
    os.environ["USE_MOCK_DEEPSEEK"] = "0"
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    for k in ("KIMI_TIMEOUT_S", "KIMI_MAX_ATTEMPTS", "KIMI_CONNECT_TIMEOUT_S", "LLM_FAILOVER_ON"):
        os.environ.pop(k, None)

    import httpx
    import agent
    import deepseek_client
    import kimi_client
    import llm_transport
    import openai_client

    req = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")

    def _status_error(code: int, body: str) -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError(
            f"{code} error", request=req,
            response=httpx.Response(code, text=body, request=req),
        )

    # ---------------------------------------------------------------- A
    print("\nA. an error is NEVER empty (the reported bug)")
    timeout_exc = httpx.ReadTimeout("", request=req)
    check("PRE-CONDITION: str(httpx.ReadTimeout) really IS empty",
          str(timeout_exc) == "", repr(str(timeout_exc)))
    # The exact pre-083 expression, for the record.
    check("PRE-CONDITION: the old f-string rendered nothing after the colon",
          f"kimi request failed: {timeout_exc}" == "kimi request failed: ")

    msg = llm_transport.describe_failure(
        timeout_exc, provider="kimi", attempts=1, budget_s=120.0,
        reason="timeout", host="api.moonshot.ai", model="kimi-k2.6",
    )
    check("names the exception class", "ReadTimeout" in msg, msg)
    check("synthesises a description for a message-less error",
          "no response within the 120s budget" in msg, msg)
    check("names host + model", "api.moonshot.ai" in msg and "kimi-k2.6" in msg)
    check("nothing dangles after a colon", not msg.rstrip().endswith(":"), msg)

    body_msg = llm_transport.describe_failure(
        _status_error(429, '{"error":{"message":"insufficient_quota"}}'),
        provider="kimi", attempts=1, budget_s=120.0, reason="rate_limit",
        host="api.moonshot.ai", model="kimi-k2.6",
    )
    check("an HTTP failure still carries the provider body (065 depends on it)",
          "insufficient_quota" in body_msg, body_msg[:120])

    keeps = llm_transport.describe_failure(
        httpx.ConnectError("nodename nor servname provided", request=req),
        provider="kimi", attempts=2, budget_s=120.0, reason="network",
        host="api.moonshot.ai", model="kimi-k2.6",
    )
    check("a non-empty transport message is kept verbatim",
          "nodename nor servname provided" in keeps and "2 attempts" in keeps, keeps[:100])

    # ---------------------------------------------------------------- B
    print("\nB. classify_transport_error — from the TYPE, so it can't be a wrong guess")
    cases = [
        (httpx.ReadTimeout("", request=req), "timeout"),
        (httpx.ConnectTimeout("", request=req), "timeout"),
        (httpx.ConnectError("refused", request=req), "network"),
        (httpx.RemoteProtocolError("peer closed", request=req), "network"),
        (_status_error(429, "slow down"), "rate_limit"),
        (_status_error(503, "overloaded"), "overloaded"),
        (_status_error(502, "bad gateway"), "overloaded"),
        (_status_error(400, "bad request"), None),
        (_status_error(401, "bad key"), None),
    ]
    for exc, want in cases:
        got = llm_transport.classify_transport_error(exc)
        check(f"{type(exc).__name__}"
              + (f"/{getattr(getattr(exc, 'response', None), 'status_code', '')}" if want != "timeout" else "")
              + f" → {want}", got == want, f"got {got}")

    # ---------------------------------------------------------------- C
    print("\nC. post_chat_completion — bounded retry inside a wall-clock DEADLINE")
    real_httpx = llm_transport.httpx
    real_asyncio = llm_transport.asyncio
    real_time = llm_transport.time
    slept: list[float] = []

    async def _no_sleep(s):
        slept.append(s)
        _FakeClient.clock[0] += s

    llm_transport.httpx = types.SimpleNamespace(
        AsyncClient=_FakeClient, Timeout=httpx.Timeout, HTTPError=httpx.HTTPError,
        TimeoutException=httpx.TimeoutException, TransportError=httpx.TransportError,
    )
    llm_transport.asyncio = types.SimpleNamespace(sleep=_no_sleep)
    llm_transport.time = types.SimpleNamespace(monotonic=lambda: _FakeClient.clock[0])

    def _post(**kw):
        base = dict(url="https://api.moonshot.ai/v1/chat/completions", api_key="k",
                    payload={}, provider="kimi", prefix="KIMI", model="kimi-k2.6")
        base.update(kw)
        return asyncio.run(llm_transport.post_chat_completion(**base))

    def _reset(script, cost=1.0, clock=0.0):
        _FakeClient.script = list(script)
        _FakeClient.calls = []
        _FakeClient.clock = [clock]
        _FakeClient.step_cost = [cost]
        slept.clear()

    try:
        os.environ["KIMI_TIMEOUT_S"] = "120"
        os.environ["KIMI_MAX_ATTEMPTS"] = "2"

        _reset([httpx.ConnectError("refused", request=req),
                _FakeResponse(200, "{}", {"choices": [{"message": {"content": "hi"}}]})])
        data = _post()
        check("a transient network failure is RETRIED and then succeeds",
              data.get("choices") is not None and len(_FakeClient.calls) == 2,
              f"{len(_FakeClient.calls)} attempts")
        check("the retry backed off", slept and slept[0] > 0, str(slept))

        _reset([_status_error(400, "bad request")])
        try:
            _post()
            check("a 400 is NOT retried", False, "no error raised")
        except llm_transport.LLMTransportError as e:
            check("a 400 is NOT retried", len(_FakeClient.calls) == 1, f"{len(_FakeClient.calls)} attempts")
            check("a 400 carries reason=None (config bug must stay loud)", e.reason is None, str(e.reason))
            check("a 400 still shows the provider body", "bad request" in str(e), str(e)[:90])

        # A read timeout eats the whole budget → the deadline itself refuses the retry.
        _reset([httpx.ReadTimeout("", request=req)], cost=120.0)
        try:
            _post()
            check("a full-budget timeout is not retried", False, "no error raised")
        except llm_transport.LLMTransportError as e:
            check("a full-budget timeout is not retried", len(_FakeClient.calls) == 1,
                  f"{len(_FakeClient.calls)} attempts")
            check("…and is classified `timeout`", e.reason == "timeout", str(e.reason))
            check("…with a readable message", "ReadTimeout" in str(e) and str(e).strip() != "", str(e)[:90])

        # THE deadline property: many allowed attempts must not multiply the wait.
        os.environ["KIMI_MAX_ATTEMPTS"] = "8"
        _reset([httpx.ConnectError("refused", request=req)] * 8, cost=30.0)
        try:
            _post()
        except llm_transport.LLMTransportError:
            pass
        elapsed = _FakeClient.clock[0]
        check("8 allowed attempts still stop at the 120s budget",
              elapsed <= 130.0 and len(_FakeClient.calls) < 8,
              f"{len(_FakeClient.calls)} attempts, {elapsed:.0f}s")
        check("no attempt starts without MIN_RETRY_BUDGET_S left",
              elapsed <= 120.0 + llm_transport.MIN_RETRY_BUDGET_S, f"{elapsed:.0f}s")

        # Per-attempt timeouts shrink as the deadline approaches.
        os.environ["KIMI_MAX_ATTEMPTS"] = "2"
        _reset([httpx.ConnectError("refused", request=req),
                _FakeResponse(200, "{}", {"ok": True})], cost=40.0)
        _post()
        t0, t1 = (c["timeout"] for c in _FakeClient.calls)
        check("attempt 2 gets only the REMAINING budget", t1.read < t0.read,
              f"{t0.read:.0f}s → {t1.read:.0f}s")
        check("connect timeout is short + separate from the read budget",
              t0.connect <= 10.0 < t0.read, f"connect {t0.connect}, read {t0.read}")

        # A 200 whose body isn't JSON should say what came back, not "invalid JSON".
        _reset([_FakeResponse(200, "<html>gateway</html>")])
        try:
            _post()
            check("a non-JSON 200 is reported with its body", False, "no error raised")
        except llm_transport.LLMTransportError as e:
            check("a non-JSON 200 is reported with its body", "gateway" in str(e), str(e)[:90])

        # KIMI_MAX_ATTEMPTS=1 disables retrying entirely.
        os.environ["KIMI_MAX_ATTEMPTS"] = "1"
        _reset([httpx.ConnectError("refused", request=req), _FakeResponse(200, "{}", {"ok": True})])
        try:
            _post()
        except llm_transport.LLMTransportError:
            pass
        check("MAX_ATTEMPTS=1 disables the retry", len(_FakeClient.calls) == 1,
              f"{len(_FakeClient.calls)} attempts")

        # The client wrapper re-raises as its OWN error type, carrying `reason`.
        os.environ["KIMI_MAX_ATTEMPTS"] = "2"
        _reset([httpx.ReadTimeout("", request=req)], cost=200.0)
        try:
            asyncio.run(kimi_client.complete("sys", [{"role": "user", "content": "hi"}]))
            check("kimi_client re-raises as KimiError", False, "no error raised")
        except kimi_client.KimiError as e:
            check("kimi_client re-raises as KimiError", True)
            check("…carrying reason='timeout'", e.reason == "timeout", str(e.reason))
            check("…exposing failover_reason for the agent", e.failover_reason == "timeout")
            check("…with the httpx error preserved as __cause__",
                  type(e.__cause__).__name__ == "ReadTimeout", type(e.__cause__).__name__)
            check("…and a message that is not empty after the colon",
                  "ReadTimeout" in str(e) and not str(e).rstrip().endswith(":"), str(e)[:100])
    finally:
        llm_transport.httpx = real_httpx
        llm_transport.asyncio = real_asyncio
        llm_transport.time = real_time
        os.environ.pop("KIMI_TIMEOUT_S", None)
        os.environ.pop("KIMI_MAX_ATTEMPTS", None)

    # ---------------------------------------------------------------- D
    print("\nD. agent-side classification")
    try:
        raise httpx.ReadTimeout("", request=req)
    except httpx.ReadTimeout as src:
        wrapped_bare = kimi_client.KimiError("kimi request failed: ")
        wrapped_bare.__cause__ = src

    hinted = kimi_client.KimiError("kimi request failed [timeout]: ReadTimeout: …", reason="timeout")
    check("_transport_reason reads the provider hint", agent._transport_reason(hinted) == "timeout")
    check("_transport_reason falls back to the __cause__ class name",
          agent._transport_reason(wrapped_bare) == "timeout")
    check("a plain error is not misread as transport",
          agent._transport_reason(ValueError("nope")) is None)

    check("_failover_reason → timeout", agent._failover_reason(hinted) == "timeout")
    check("billing STILL beats a transport hint (a quota 429 is a billing problem)",
          agent._failover_reason(
              kimi_client.KimiError("429 — insufficient_quota", reason="rate_limit")) == "billing")
    check("_should_failover fires on a timeout by default",
          agent._should_failover(hinted, None) == "timeout")
    os.environ["LLM_FAILOVER_ON"] = "billing,rate_limit,overloaded"
    check("…and LLM_FAILOVER_ON restores the pre-083 fail-fast behaviour",
          agent._should_failover(hinted, None) is None)
    os.environ.pop("LLM_FAILOVER_ON")

    msg, code = agent._classify_agent_error(hinted)
    check("timeout → code provider_timeout", code == "provider_timeout", code)
    check("timeout copy is actionable", "timed out" in msg.lower(), msg[:90])
    check("copy names the ACTIVE rail, not Anthropic",
          "Kimi (Moonshot) API" in msg and "anthropic" not in msg.lower(), msg[:90])

    net = kimi_client.KimiError("connect failed", reason="network")
    _, ncode = agent._classify_agent_error(net)
    check("network → provider_unavailable", ncode == "provider_unavailable", ncode)

    bmsg, _ = agent._classify_agent_error(Exception("your credit balance is too low"))
    check("billing copy points at the KIMI console on the kimi rail",
          "moonshot.ai" in bmsg and "console.anthropic.com" not in bmsg, bmsg[:110])
    os.environ["LLM_RAIL"] = "anthropic"
    amsg, _ = agent._classify_agent_error(Exception("your credit balance is too low"))
    check("…and at the Anthropic console on the anthropic rail",
          "console.anthropic.com" in amsg and "Anthropic API" in amsg, amsg[:110])
    os.environ["LLM_RAIL"] = "kimi"

    os.environ["CHAT_VERBOSE_ERRORS"] = "0"
    qmsg, qcode = agent._classify_agent_error(hinted)
    check("CHAT_VERBOSE_ERRORS=0 keeps the generic, non-leaky copy",
          "kimi" not in qmsg.lower() and "moonshot" not in qmsg.lower() and qcode == "provider_timeout",
          qmsg[:70])
    os.environ["CHAT_VERBOSE_ERRORS"] = "1"

    emsg, _ = agent._classify_agent_error(RuntimeError(""))
    check("REGRESSION: a message-less exception never renders an empty tail",
          not emsg.rstrip().endswith(":") and "(no detail)" in emsg, emsg)

    # ---------------------------------------------------------------- E
    print("\nE. end-to-end — the live failure now answers instead of dying")
    real_kimi, real_ds = kimi_client.complete, deepseek_client.complete

    async def _timeout_turn(system, messages, tools=None, max_tokens=None):
        try:
            raise httpx.ReadTimeout("", request=req)
        except httpx.ReadTimeout as src:
            raise kimi_client.KimiError(
                llm_transport.describe_failure(
                    src, provider="kimi", attempts=1, budget_s=120.0, reason="timeout",
                    host="api.moonshot.ai", model="kimi-k2.6"),
                reason="timeout",
            ) from src

    async def _ds_ok(system, messages, tools=None, max_tokens=None):
        return {"text": "From DeepSeek.", "tool_calls": [], "usage": {}}

    try:
        kimi_client.complete = _timeout_turn
        deepseek_client.complete = _ds_ok
        evs = asyncio.run(drain(agent.run_chat("build me a trade plan", "u1")))
        prov = [e for e in evs if e["event"] == "provider"]
        check("a Kimi TIMEOUT now announces a failover (2 provider events)", len(prov) == 2,
              f"{len(prov)} provider events")
        check("…to deepseek, reason kimi_timeout",
              len(prov) == 2 and prov[1]["data"]["provider"] == "deepseek"
              and prov[1]["data"]["fallback"] is True
              and prov[1]["data"]["reason"] == "kimi_timeout",
              str(prov[-1]["data"].get("reason")))
        check("…and the user gets an answer, not a red bubble",
              any(e["event"] == "message" for e in evs)
              and not any(e["event"] == "error" for e in evs))

        # With failover switched off, it must fail LOUDLY but READABLY.
        os.environ["LLM_FAILOVER_ON"] = "billing,rate_limit,overloaded"
        evs = asyncio.run(drain(agent.run_chat("build me a trade plan", "u1")))
        errs = [e for e in evs if e["event"] == "error"]
        check("failover off → the turn still ends in an error", len(errs) == 1, str(len(errs)))
        emsg = errs[0]["data"]["message"] if errs else ""
        check("…whose message is the OPPOSITE of the reported bug (never empty)",
              bool(emsg.strip()) and not emsg.rstrip().endswith(":"), emsg[:90])
        check("…and names the timeout + the right rail",
              "timed out" in emsg.lower() and "Kimi" in emsg, emsg[:90])
        check("…with a stable code for telemetry",
              errs and errs[0]["data"].get("code") == "provider_timeout")
        os.environ.pop("LLM_FAILOVER_ON")
    finally:
        kimi_client.complete, deepseek_client.complete = real_kimi, real_ds

    # ---------------------------------------------------------------- F
    print("\nF. structural — one transport policy, not three")
    srcs = {n: open(os.path.join(BACKEND, f"{n}_client.py"), encoding="utf-8").read()
            for n in ("kimi", "deepseek", "openai")}
    for n, src in srcs.items():
        check(f"{n}_client routes through llm_transport",
              "llm_transport.post_chat_completion(" in src)
        check(f"{n}_client no longer builds its own AsyncClient",
              "httpx.AsyncClient(" not in src)
        check(f"{n}_client no longer interpolates a bare `{{e}}` into its error",
              f'{n} request failed: {{e}}' not in src)
    check("every rail error class accepts reason=",
          all(getattr(cls("m", reason="timeout"), "failover_reason", None) == "timeout"
              for cls in (kimi_client.KimiError, deepseek_client.DeepSeekError,
                          openai_client.OpenAIError)))
    env = open(os.path.join(BACKEND, ".env.example"), encoding="utf-8").read()
    check(".env.example documents the new budget", "KIMI_TIMEOUT_S=120" in env)
    check(".env.example ships the widened failover default",
          "LLM_FAILOVER_ON=billing,rate_limit,overloaded,timeout,network" in env)


def main() -> int:
    backup = tempfile.mkdtemp(prefix="p083_")
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
