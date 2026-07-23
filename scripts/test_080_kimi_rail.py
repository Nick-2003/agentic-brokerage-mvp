#!/usr/bin/env python3
"""Offline guard for Proposal 080 — Kimi (Moonshot) as a selectable rail.

Network-free. Temp-apply → assert → restore-in-`finally`, with the 078 LIVE-MODE
and non-destructive (`_created`) guards. Confirm with `git status` after running.

Context: with Anthropic + OpenAI credit-blocked, DeepSeek was the only funded rail
and 074 made it primary with NOTHING beneath it. Kimi K2.6 is funded, does parallel
tool calls, and — the differentiator — is VISION-capable, so it restores both a real
fallback chain and the 059 image turns DeepSeek has to refuse.

Covers:
  A. rail select accepts "kimi"; unknown still fails safe to anthropic;
  B. run_chat(LLM_RAIL=kimi) — ONE provider event {kimi, fallback:false}, answer
     delivered, no Anthropic call, no ProviderFailover;
  C. **VISION**: an image turn RUNS on kimi (contrast: the same turn on deepseek is
     refused). This is the capability 080 exists for;
  D. KIMI_VISION=0 → image turns refused with vision_unavailable, never dropped;
  E. kimi usage-limit → fails over to DeepSeek (a real chain, announced);
  F. briefing: rail select + rail-aware mock gate + kimi primary branch writes the
     brief with NO false "usual model unavailable" disclosure;
  G. /healthz reports rail/model kimi + kimi_key_present, leaking no key;
  H. llm_limits picks up KIMI_MAX_OUTPUT_TOKENS via provider_name().

Run:
    backend/.venv/bin/python scripts/test_080_kimi_rail.py
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
PROP = os.path.join(REPO, ".proposed_changes", "080-kimi-rail", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


OVERWRITE = ["agent.py", "briefing.py", "main.py", ".env.example"]
NET_NEW = ["kimi_client.py"]
_created: list[str] = []
LIVE_MODE = not os.path.isdir(PROP)


def apply_proposal(backup_dir: str) -> None:
    if LIVE_MODE:
        print("  (staged dir absent — 080 is applied; asserting against the LIVE tree)")
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


async def drain(agen):
    return [ev async for ev in agen]


IMG = [{"media_type": "image/png", "data": "QUJD"}]


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ.setdefault("USE_MOCK_MARKET", "1")
    os.environ.setdefault("USE_MOCK_BROKER", "1")
    os.environ["WIDGET_VALIDATOR_MODE"] = "off"
    os.environ["KIMI_API_KEY"] = "sk-kimi-test"
    os.environ["USE_MOCK_KIMI"] = "0"
    os.environ["KIMI_MODEL"] = "kimi-k2.6"
    os.environ["KIMI_VISION"] = "1"
    for k in ("KIMI_MAX_OUTPUT_TOKENS", "LLM_MAX_OUTPUT_TOKENS"):
        os.environ.pop(k, None)

    import kimi_client
    import deepseek_client
    import llm_limits
    import agent
    import briefing

    # ---------------------------------------------------------------- A
    print("\nA. rail select")
    os.environ["LLM_RAIL"] = "kimi"
    check("_rail() → kimi", agent._rail() == "kimi")
    check("_brief_rail() → kimi", briefing._brief_rail() == "kimi")
    os.environ["LLM_RAIL"] = "gibberish"
    check("unknown fails safe to anthropic", agent._rail() == "anthropic")
    os.environ["LLM_RAIL"] = "kimi"

    # ---------------------------------------------------------------- B
    print("\nB. run_chat runs Kimi directly as primary")

    async def _ok(system, messages, tools=None, max_tokens=None):
        return {"text": "Answer from Kimi.", "tool_calls": [],
                "usage": {"input_tokens": 5, "output_tokens": 3}}

    kimi_client.complete = _ok
    anthropic_calls = {"n": 0}
    real_get_client = agent._get_client

    def _boom():
        anthropic_calls["n"] += 1
        raise AssertionError("Anthropic client built on a Kimi-primary turn")

    agent._get_client = _boom
    try:
        evs = asyncio.run(drain(agent.run_chat("hi", "u1")))
    finally:
        agent._get_client = real_get_client
    prov = [e for e in evs if e["event"] == "provider"]
    check("exactly ONE provider event", len(prov) == 1)
    check("provider kimi, not a fallback, labelled",
          prov and prov[0]["data"]["provider"] == "kimi"
          and prov[0]["data"]["fallback"] is False
          and prov[0]["data"]["model"] == "kimi-k2.6")
    check("chip label is friendly ('Kimi K2.6')",
          prov and prov[0]["data"]["label"] == "Kimi K2.6", str(prov[0]["data"]["label"]))
    check("answer delivered", any(e["event"] == "message" for e in evs))
    check("no Anthropic call", anthropic_calls["n"] == 0)

    # ---------------------------------------------------------------- C
    print("\nC. VISION — image turns RUN on Kimi (the reason 080 exists)")
    saw: dict = {"neutral": None}

    async def _vision_ok(system, messages, tools=None, max_tokens=None):
        # The loop hands `complete` NEUTRAL messages — the OpenAI `image_url`
        # conversion happens INSIDE the real client (openai_compat), which is
        # monkeypatched out here. So capture the neutral turn and assert BOTH
        # layers below: the loop attached the image, and the shared translation
        # turns that into a wire image part.
        for m in messages:
            if m.get("role") == "user" and m.get("attachments"):
                saw["neutral"] = m
        return {"text": "I see the chart.", "tool_calls": [], "usage": {}}

    kimi_client.complete = _vision_ok
    evs = asyncio.run(drain(agent.run_chat("what is this?", "u1", attachments=IMG)))
    check("image turn NOT refused on kimi",
          not any(e["event"] == "error" and e["data"].get("code") == "vision_unavailable" for e in evs))
    check("answer delivered for the image turn", any(e["event"] == "message" for e in evs))
    check("loop ATTACHED the image (vision-capable branch fired)",
          saw["neutral"] is not None and saw["neutral"]["attachments"] == IMG)
    # …and the shared translation turns that neutral turn into a real wire image part.
    import openai_compat
    wire = openai_compat.to_openai_messages("SYS", [saw["neutral"]]) if saw["neutral"] else []
    parts = wire[1]["content"] if len(wire) > 1 else []
    check("→ becomes an OpenAI image_url part on the wire",
          isinstance(parts, list)
          and any(p.get("type") == "image_url"
                  and p["image_url"]["url"].startswith("data:image/png;base64,") for p in parts),
          str(parts)[:90])

    # contrast: the identical turn on deepseek IS refused
    os.environ["LLM_RAIL"] = "deepseek"
    os.environ["DEEPSEEK_API_KEY"] = "sk-ds-test"
    os.environ["USE_MOCK_DEEPSEEK"] = "0"
    evs_ds = asyncio.run(drain(agent.run_chat("what is this?", "u1", attachments=IMG)))
    check("CONTRAST: same image turn on deepseek → vision_unavailable",
          any(e["event"] == "error" and e["data"].get("code") == "vision_unavailable" for e in evs_ds))
    os.environ["LLM_RAIL"] = "kimi"

    # ---------------------------------------------------------------- D
    print("\nD. KIMI_VISION=0 refuses rather than silently dropping")
    os.environ["KIMI_VISION"] = "0"
    evs = asyncio.run(drain(agent.run_chat("what is this?", "u1", attachments=IMG)))
    check("vision_unavailable when pinned text-only",
          any(e["event"] == "error" and e["data"].get("code") == "vision_unavailable" for e in evs))
    os.environ["KIMI_VISION"] = "1"

    # ---------------------------------------------------------------- E
    print("\nE. Kimi usage-limit → real fallback to DeepSeek")

    async def _quota(system, messages, tools=None, max_tokens=None):
        raise kimi_client.KimiError("kimi request failed: 429 — insufficient_quota")

    async def _ds_ok(system, messages, tools=None, max_tokens=None):
        return {"text": "From DeepSeek.", "tool_calls": [], "usage": {}}

    kimi_client.complete = _quota
    deepseek_client.complete = _ds_ok
    os.environ["LLM_FALLBACK_ENABLED"] = "1"
    evs = asyncio.run(drain(agent.run_chat("hi", "u1")))
    prov = [e for e in evs if e["event"] == "provider"]
    check("two provider events (failover announced)", len(prov) == 2)
    check("fallback names deepseek + the kimi reason",
          len(prov) == 2 and prov[1]["data"]["provider"] == "deepseek"
          and prov[1]["data"]["fallback"] is True
          and prov[1]["data"]["reason"] == "kimi_billing", str(prov[-1]["data"].get("reason")))
    check("DeepSeek's answer delivered", any(e["event"] == "message" for e in evs))
    kimi_client.complete = _ok

    # ---------------------------------------------------------------- F
    print("\nF. briefing on the Kimi rail")
    check("mock gate is kimi-aware (real key → not mock)",
          briefing.briefing_mock_enabled() is False)

    async def _brief(system, messages, tools=None, max_tokens=None):
        return {"text": "Morning brief prose.", "tool_calls": [], "usage": {}}

    kimi_client.complete = _brief
    os.environ["USE_MOCK_BRIEFING"] = "0"
    snap = {"is_mock": False, "base_currency": "HKD", "as_of": "2026-07-01",
            "account_id": "U1", "nav": {"total": 100.0}, "holdings": []}
    out = asyncio.run(briefing.generate_briefing(snap, None))
    check("brief written by kimi", out["model"] == "kimi-k2.6")
    check("NOT flagged a fallback", out["fallback"] is False)
    check("no false 'usual model was unavailable' line",
          "the usual model was unavailable" not in out["text"])
    os.environ.pop("USE_MOCK_BRIEFING", None)

    # ---------------------------------------------------------------- G
    print("\nG. /healthz")
    SENTINEL = "sk-kimi-SENTINELVALUE"
    os.environ["KIMI_API_KEY"] = SENTINEL
    import main as main_mod
    payload = asyncio.run(main_mod.healthz())
    check("rail kimi", payload.get("rail") == "kimi")
    check("model kimi-k2.6", payload.get("model") == "kimi-k2.6")
    check("kimi_key_present is True bool", payload.get("kimi_key_present") is True)
    check("no key material leaked", "SENTINEL" not in json.dumps(payload))

    # ---------------------------------------------------------------- H
    print("\nH. per-rail token cap wiring")
    check("default cap 4096", llm_limits.max_output_tokens("kimi") == 4096)
    os.environ["KIMI_MAX_OUTPUT_TOKENS"] = "1234"
    check("KIMI_MAX_OUTPUT_TOKENS honoured", llm_limits.max_output_tokens("kimi") == 1234)
    check("provider_name() is the env prefix", kimi_client.provider_name() == "kimi")
    os.environ.pop("KIMI_MAX_OUTPUT_TOKENS", None)


def main() -> None:
    backup = tempfile.mkdtemp(prefix="080-backup-")
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
