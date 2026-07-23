#!/usr/bin/env python3
"""Offline guard for Proposal 073 — OpenAI cutover + explicit token limits.

Network-free. Backend files use the repo's temp-apply → assert → restore-in-
`finally` pattern (net-new files deleted), so the live tree ends exactly as it
started — confirm with `git status`. The frontend files are checked statically
against the STAGED copies, so nothing in `frontend/` is ever touched.

Covers:
  A. `llm_limits` resolution — unset ⇒ exactly 4096/1024 (the byte-equivalence
     proof that 073 changes nothing until you opt in), general cap, per-rail
     override precedence, and fail-safe on garbage / 0 / negative;
  B. the cap actually reaches the wire, and `OPENAI_USE_COMPLETION_TOKENS=1`
     flips the payload KEY while still carrying the resolved VALUE (regression
     guard on 071's rename branch);
  C. **the 071 defect**: `record_generation` must receive PER-ITERATION deltas,
     not cumulative running totals, and must match the Anthropic rail. The `done`
     totals must stay unchanged — that's the number the budget reads;
  D. the OpenAI rail reports `set_output` when it hits max iterations (parity
     with the Anthropic rail);
  E. `main.py` ACCUMULATES token usage across `done` events (AST-level, so it
     can't be satisfied by a comment) — a failover bills both attempts;
  F. `/healthz` names the ACTIVE rail + model, and leaks no key material;
  G. frontend: `'openai'` in the provider union, no hardcoded "Claude was",
     rail-prefix-agnostic `reasonPhrase`, `primaryLabel` retained;
  H. `.env.example` documents all 8 × 071 vars + the both-services warning.

Run:
    backend/.venv/bin/python scripts/test_073_openai_cutover.py
"""
import ast
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
PROP = os.path.join(REPO, ".proposed_changes", "073-openai-cutover")
PROP_BE = os.path.join(PROP, "backend")
PROP_FE = os.path.join(PROP, "frontend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["agent.py", "briefing.py", "deepseek_client.py", "openai_client.py",
             "main.py", ".env.example"]
NET_NEW = ["llm_limits.py"]


# ⚠️ Files this run actually CREATED. Only these may be deleted on restore.
#
# This guard exists because its absence was destructive in `test_071`: that test
# deleted its NET_NEW list unconditionally, so once 071 was applied — making
# `openai_client.py`/`openai_compat.py` LIVE production modules rather than test
# artifacts — simply running the test removed two files the backend imports at
# startup. A restore must never delete a file it did not create.
_created: list[str] = []

# 078 — LIVE MODE. Once a proposal is applied and its staged dir removed, the
# proposal IS the live tree: assert against what's installed, apply/restore
# nothing. Without this the test crashed on a missing staged file (and only the
# `_created` guard above stopped the `finally` from deleting live modules).
LIVE_MODE = not os.path.isdir(PROP_BE)


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 073 is applied; asserting against the LIVE tree)")
        return
    for f in OVERWRITE:
        shutil.copy2(os.path.join(BACKEND, f), os.path.join(backup_dir, f))
        shutil.copy2(os.path.join(PROP_BE, f), os.path.join(BACKEND, f))
    for f in NET_NEW:
        dst = os.path.join(BACKEND, f)
        pre_existing = os.path.isfile(dst)
        if pre_existing:  # 073 already applied — back it up, don't delete it later
            shutil.copy2(dst, os.path.join(backup_dir, f))
            OVERWRITE.append(f)
        else:
            _created.append(dst)
        shutil.copy2(os.path.join(PROP_BE, f), dst)


def restore(backup_dir: str) -> None:
    if LIVE_MODE:
        return
    for f in OVERWRITE:
        b = os.path.join(backup_dir, f)
        if os.path.isfile(b):
            shutil.copy2(b, os.path.join(BACKEND, f))
    for p in _created:  # ONLY files this run created
        if os.path.isfile(p):
            os.remove(p)


async def drain(agen):
    return [ev async for ev in agen]


class RecordingTracer:
    """Captures what the agent loop reports, so C and D assert on real calls."""

    def __init__(self):
        self.generations: list[dict] = []
        self.outputs: list[dict] = []

    def record_generation(self, **kw):
        self.generations.append(kw)

    def record_tool(self, **kw):
        pass

    def set_output(self, out):
        self.outputs.append(out)

    def __getattr__(self, _):  # tolerate any other Tracer protocol member
        return lambda *a, **k: None


def _clear(*names):
    for n in names:
        os.environ.pop(n, None)


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ.setdefault("USE_MOCK_MARKET", "1")
    os.environ.setdefault("USE_MOCK_BROKER", "1")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-REPLACE")
    os.environ["WIDGET_VALIDATOR_MODE"] = "off"  # 067 has its own suite
    _clear("LLM_MAX_OUTPUT_TOKENS", "BRIEF_MAX_OUTPUT_TOKENS",
           "OPENAI_MAX_OUTPUT_TOKENS", "ANTHROPIC_MAX_OUTPUT_TOKENS",
           "DEEPSEEK_MAX_OUTPUT_TOKENS", "OPENAI_BRIEF_MAX_OUTPUT_TOKENS")

    import llm_limits
    import openai_client
    import deepseek_client
    import agent

    # ---------------------------------------------------------------- A
    print("\nA. llm_limits resolution")
    check("unset ⇒ 4096 on every rail (behaviour identical to pre-073)",
          llm_limits.max_output_tokens("openai") == 4096
          and llm_limits.max_output_tokens("anthropic") == 4096
          and llm_limits.max_output_tokens("deepseek") == 4096)
    check("unset ⇒ brief 1024", llm_limits.brief_max_output_tokens("openai") == 1024)

    os.environ["LLM_MAX_OUTPUT_TOKENS"] = "2000"
    check("general cap applies to BOTH rails",
          llm_limits.max_output_tokens("openai") == 2000
          and llm_limits.max_output_tokens("anthropic") == 2000)
    check("chat cap does NOT bleed into the brief",
          llm_limits.brief_max_output_tokens("openai") == 1024)

    os.environ["OPENAI_MAX_OUTPUT_TOKENS"] = "1500"
    check("per-rail override beats the general cap, others unaffected",
          llm_limits.max_output_tokens("openai") == 1500
          and llm_limits.max_output_tokens("anthropic") == 2000)

    for bad in ("abc", "0", "-1", "", "   "):
        os.environ["OPENAI_MAX_OUTPUT_TOKENS"] = bad
        if llm_limits.max_output_tokens("openai") != 2000:
            check(f"invalid cap {bad!r} falls back safely", False)
            break
    else:
        check("invalid caps ('abc'/0/-1/empty) fall back safely, no crash", True)

    _clear("OPENAI_MAX_OUTPUT_TOKENS", "LLM_MAX_OUTPUT_TOKENS")

    # ---------------------------------------------------------------- B
    print("\nB. the cap reaches the wire")
    os.environ["OPENAI_API_KEY"] = "sk-test-key"
    os.environ["USE_MOCK_OPENAI"] = "0"
    os.environ["OPENAI_MODEL"] = "gpt-5"
    captured: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured.clear()
            captured.update(json or {})
            return _Resp()

    real_httpx_client = openai_client.httpx.AsyncClient
    openai_client.httpx.AsyncClient = _FakeClient
    try:
        os.environ["OPENAI_USE_COMPLETION_TOKENS"] = "0"
        asyncio.run(openai_client.complete("s", [{"role": "user", "content": "hi"}]))
        check("default resolves to 4096 on the wire under `max_tokens`",
              captured.get("max_tokens") == 4096 and "max_completion_tokens" not in captured)

        os.environ["LLM_MAX_OUTPUT_TOKENS"] = "777"
        asyncio.run(openai_client.complete("s", [{"role": "user", "content": "hi"}]))
        check("env cap reaches the wire", captured.get("max_tokens") == 777)

        os.environ["OPENAI_USE_COMPLETION_TOKENS"] = "1"
        asyncio.run(openai_client.complete("s", [{"role": "user", "content": "hi"}]))
        check("reasoning-model switch flips the KEY and keeps the VALUE",
              captured.get("max_completion_tokens") == 777 and "max_tokens" not in captured)
    finally:
        openai_client.httpx.AsyncClient = real_httpx_client
        _clear("LLM_MAX_OUTPUT_TOKENS")
        os.environ["OPENAI_USE_COMPLETION_TOKENS"] = "0"

    # ---------------------------------------------------------------- C
    print("\nC. usage reported as PER-ITERATION deltas (the 071 defect)")
    USAGE = [(100, 10), (200, 20), (300, 30)]

    def _scripted():
        calls = {"n": 0}

        async def _complete(system, messages, tools=None, max_tokens=None):
            i = calls["n"]
            calls["n"] += 1
            u = {"input_tokens": USAGE[i][0], "output_tokens": USAGE[i][1]}
            if i < len(USAGE) - 1:  # keep the loop going with a tool call
                return {"text": "", "usage": u,
                        "tool_calls": [{"id": f"tc{i}", "name": "__no_such_tool__", "input": {}}]}
            return {"text": "Done.", "tool_calls": [], "usage": u}

        return _complete

    agent.MAX_ITERATIONS = 10
    openai_client.complete = _scripted()
    tr = RecordingTracer()
    evs = asyncio.run(drain(agent.run_agent_openai_compat(
        "hi", "u1", tracer=tr, client=openai_client)))
    got = [(g["usage_details"]["input"], g["usage_details"]["output"]) for g in tr.generations]
    check("three generations recorded", len(got) == 3, str(got))
    check("deltas, NOT cumulative totals",
          got == USAGE, f"got {got}, cumulative would be [(100,10),(300,30),(600,60)]")
    done = [e for e in evs if e["event"] == "done"]
    check("done totals still the SUM (the number the budget reads)",
          done and done[0]["data"]["input_tokens"] == 600
          and done[0]["data"]["output_tokens"] == 60)

    # ---------------------------------------------------------------- D
    print("\nD. max-iteration parity")
    agent.MAX_ITERATIONS = 1

    async def _always_tool(system, messages, tools=None, max_tokens=None):
        return {"text": "", "usage": {"input_tokens": 1, "output_tokens": 1},
                "tool_calls": [{"id": "t", "name": "__no_such_tool__", "input": {}}]}

    openai_client.complete = _always_tool
    tr2 = RecordingTracer()
    evs2 = asyncio.run(drain(agent.run_agent_openai_compat(
        "hi", "u1", tracer=tr2, client=openai_client)))
    check("set_output recorded on max iterations (was Anthropic-only)",
          any(o.get("kind") == "error" for o in tr2.outputs), str(tr2.outputs))
    check("error event still emitted",
          any(e["event"] == "error" for e in evs2))
    agent.MAX_ITERATIONS = 10

    # ---------------------------------------------------------------- E
    print("\nE. main.py accumulates token usage across `done` events")
    tree = ast.parse(open(os.path.join(BACKEND, "main.py")).read())
    aug = {n.target.id for n in ast.walk(tree)
           if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name)
           and isinstance(n.op, ast.Add)}
    plain = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
    for var in ("turn_input_tokens", "turn_output_tokens"):
        check(f"{var} uses += (failover bills BOTH attempts)", var in aug)
    check("no leftover plain re-assignment inside the stream loop",
          # they are still initialised to 0 once, so exactly that one Assign is expected
          all(v in plain for v in ("turn_input_tokens", "turn_output_tokens")))

    # ---------------------------------------------------------------- F
    print("\nF. /healthz names the active rail and leaks nothing")
    SENTINEL = "sk-SENTINELVALUE0123456789"
    os.environ["OPENAI_API_KEY"] = SENTINEL
    os.environ["USE_MOCK_OPENAI"] = "0"
    os.environ["LLM_RAIL"] = "openai"
    import main as main_mod
    payload = asyncio.run(main_mod.healthz())
    blob = json.dumps(payload)
    check("rail reported as openai", payload.get("rail") == "openai")
    check("model is the ACTIVE primary, not the Anthropic constant",
          payload.get("model") == "gpt-5" and "claude" not in str(payload.get("model")))
    check("openai_key_present is a bool", isinstance(payload.get("openai_key_present"), bool)
          and payload["openai_key_present"] is True)
    check("NO key material in the response", "SENTINEL" not in blob)
    check("max_output_tokens surfaced", payload.get("max_output_tokens") == 4096)
    os.environ["LLM_RAIL"] = "anthropic"
    payload2 = asyncio.run(main_mod.healthz())
    check("rail=anthropic reports the Anthropic model again",
          payload2.get("rail") == "anthropic" and payload2.get("model") == agent.MODEL)

    # ---------------------------------------------------------------- G
    # 078 — read the LIVE frontend once 073 is applied (the staged copies are gone);
    # the staged copies otherwise. Same assertions either way.
    fe_root = os.path.join(REPO, "frontend") if LIVE_MODE else PROP_FE
    print(f"\nG. frontend (static, against the {'LIVE' if LIVE_MODE else 'STAGED'} copies)")
    sse = open(os.path.join(fe_root, "lib", "sse.ts")).read()
    tc = open(os.path.join(fe_root, "components", "ThinkingCard.tsx")).read()
    pg = open(os.path.join(fe_root, "app", "page.tsx")).read()
    check("'openai' in the provider union", "'openai'" in sse)
    check("no hardcoded 'Claude was' in the fallback notice", "Claude was" not in tc)
    check("reasonPhrase is rail-prefix agnostic (endsWith)", "endsWith('billing')" in tc)
    check("ThinkingCard accepts primaryLabel", "primaryLabel" in tc)
    check("page.tsx retains primaryLabel across the failover event",
          "primaryLabel" in pg and "next.provider?.label" in pg)

    # ---------------------------------------------------------------- H
    print("\nH. .env.example documentation")
    env = open(os.path.join(BACKEND, ".env.example")).read()
    missing = [v for v in ("LLM_RAIL", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
                           "OPENAI_TIMEOUT_S", "USE_MOCK_OPENAI", "OPENAI_VISION",
                           "OPENAI_USE_COMPLETION_TOKENS") if v not in env]
    check("all 8 × 071 vars documented", not missing, f"missing {missing}" if missing else "")
    check("LLM_RAIL defaults to anthropic", "LLM_RAIL=anthropic" in env)
    check("both-services warning present",
          "BOTH" in env and "cron" in env.lower())
    check("input-side levers documented",
          "CHAT_HISTORY_MAX_CHARS" in env and "MAX_TOOL_ITERATIONS" in env)


def main() -> None:
    backup = tempfile.mkdtemp(prefix="073-backup-")
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
