#!/usr/bin/env python3
"""Why didn't the turn fall over to DeepSeek? (072 — diagnostic, read-only)

The failover decision is an AND of five conditions inside `agent._should_failover`.
If ANY one is false the turn dies with the primary's error and NOTHING is logged
about why — that silence is the whole problem this script solves.

It evaluates each gate using the REAL functions (not a re-implementation, which
could drift), against the REAL environment of whatever service you run it on,
and names the single blocking gate.

Run it ON the service that's failing — env vars are per-service on Railway and
the web and cron services do NOT share them:

    # Railway → service → Shell/exec
    python scripts/diagnose_fallback.py

    # optionally prove the key actually works (makes ONE tiny paid API call):
    python scripts/diagnose_fallback.py --ping

Read-only: touches no files and changes no state.
"""
from __future__ import annotations

import argparse
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
sys.path.insert(0, os.path.join(REPO, "backend"))

# ⚠️ Snapshot the PROCESS environment BEFORE importing anything from backend/.
# `main.py` calls load_dotenv(), which injects `backend/.env` into os.environ —
# so a var that is MISSING on the Railway service can appear present here purely
# because a local .env supplied it. That would send you hunting the wrong problem.
# Everything in section [1] reports this snapshot; section [2] uses the live
# functions (post-dotenv) and any divergence between the two is called out.
ENV_AT_START = dict(os.environ)

OK, BAD, WARN = "\033[92m✓\033[0m", "\033[91m✗\033[0m", "\033[93m!\033[0m"

# The exact live error from the failing app, so classification is tested against
# the real string rather than a paraphrase.
REAL_ERROR = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API. Please go "
    "to Plans & Billing to upgrade or purchase credits.'}}"
)


def mask(v: str) -> str:
    if not v:
        return "(empty)"
    return f"{v[:6]}…{v[-4:]} (len {len(v)})" if len(v) > 12 else f"(set, len {len(v)})"


def raw(name: str) -> str:
    """Value as the SERVICE provided it, plus a flag when a local .env filled it in."""
    v0 = ENV_AT_START.get(name)
    v1 = os.getenv(name)
    if v0 is None and v1 is not None:
        return f"(UNSET on service) → {v1!r} injected by backend/.env  {WARN}"
    if v0 is not None and v1 != v0:
        return f"{v0!r} (service) → overridden to {v1!r} by backend/.env  {WARN}"
    return "(UNSET)" if v0 is None else repr(v0)


def dotenv_masking() -> list[str]:
    """Vars the local .env supplied that the service did not — the exact set that
    makes a local run look healthy while production stays broken."""
    keys = ("LLM_RAIL", "LLM_FALLBACK_ENABLED", "LLM_FAILOVER_ON", "USE_MOCK_DEEPSEEK",
            "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "OPENAI_API_KEY", "WIDGET_VALIDATOR_MODE")
    return [k for k in keys if ENV_AT_START.get(k) is None and os.getenv(k) is not None]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true", help="make one real DeepSeek call")
    args = ap.parse_args()

    import agent
    import deepseek_client

    print("\n" + "=" * 68)
    print("  FALLBACK DIAGNOSTIC")
    print("=" * 68)

    # --- 0. is the deployed code even capable of failing over? --------------
    print("\n[0] Deployed code")
    has_runchat = hasattr(agent, "run_chat")
    has_rail = hasattr(agent, "_rail")
    print(f"  {OK if has_runchat else BAD} agent.run_chat exists (069 failover entry point)")
    print(f"  {OK if has_rail else WARN} agent._rail exists (071 rail select){'' if has_rail else ' — pre-071 build'}")
    try:
        import main as _main  # noqa: F401
        src = open(os.path.join(REPO, "backend", "main.py")).read()
        wired = "run_chat(" in src
        print(f"  {OK if wired else BAD} main.py calls run_chat "
              f"{'' if wired else '← FAILOVER CAN NEVER FIRE: main.py bypasses it'}")
    except Exception as e:  # noqa: BLE001
        print(f"  {WARN} could not import main.py: {e}")

    if not has_runchat:
        print("\n  VERDICT: this build predates 069. Deploy the current main branch.")
        return

    # --- 1. the five gates ---------------------------------------------------
    print("\n[1] Raw environment (as this service sees it)")
    for k in ("LLM_RAIL", "LLM_FALLBACK_ENABLED", "LLM_FAILOVER_ON",
              "USE_MOCK_DEEPSEEK", "DEEPSEEK_MODEL", "WIDGET_VALIDATOR_MODE"):
        print(f"      {k:<24} = {raw(k)}")
    print(f"      {'DEEPSEEK_API_KEY':<24} = {mask(os.getenv('DEEPSEEK_API_KEY', ''))}")
    masked = dotenv_masking()
    if masked:
        print(f"\n  {WARN} backend/.env supplied {masked} — the SERVICE did not.")
        print("      On Railway there is normally no .env file, so these would be MISSING")
        print("      in production even though the gates below pass here. This is the #1")
        print("      way a local run says 'healthy' while the deployed app stays broken.")

    print("\n[2] Gate-by-gate (all five must pass)")
    blockers: list[str] = []

    # Gate A — master switch
    g_flag = deepseek_client.fallback_enabled()
    print(f"  {OK if g_flag else BAD} A. LLM_FALLBACK_ENABLED == \"1\"")
    if not g_flag:
        v = os.getenv("LLM_FALLBACK_ENABLED")
        if v is None:
            blockers.append("LLM_FALLBACK_ENABLED is UNSET on this service → set it to 1")
        else:
            blockers.append(
                f"LLM_FALLBACK_ENABLED is {v!r}. The check is an EXACT string match on "
                '"1" — "true"/"TRUE"/"yes"/" 1" all evaluate as OFF. Set it to exactly: 1'
            )

    # Gate B — provider usable
    g_avail = deepseek_client.deepseek_available()
    print(f"  {OK if g_avail else BAD} B. DeepSeek usable (key present, mock off)")
    if not g_avail:
        if os.getenv("USE_MOCK_DEEPSEEK") == "1":
            blockers.append('USE_MOCK_DEEPSEEK is "1" → set it to 0 on THIS service')
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            blockers.append("DEEPSEEK_API_KEY is unset on THIS service (Railway services "
                            "do NOT share variables — web and cron are separate)")
        elif key.endswith("REPLACE"):
            blockers.append("DEEPSEEK_API_KEY is still the .env.example placeholder "
                            "(ends with 'REPLACE')")

    # Gate C — turn eligibility
    g_noimg = deepseek_client.can_fall_back(None)
    print(f"  {OK if g_noimg else BAD} C. turn is eligible (no image attachments)")
    print("       note: a turn WITH an attached image never falls back by design "
          "(DeepSeek has no vision) — retry without the image to test.")

    # Gate D — error classifies as a usage limit
    reason = agent._failover_reason(Exception(REAL_ERROR))
    print(f"  {OK if reason else BAD} D. the live error classifies as a failover reason "
          f"→ {reason!r}")
    if not reason:
        blockers.append("the primary's error text does not match any usage-limit marker "
                        "— check agent._BILLING_MARKERS against the real message")

    # Gate E — that reason is enabled
    reasons = agent._failover_reasons()
    g_reason = bool(reason) and reason in reasons
    print(f"  {OK if g_reason else BAD} E. reason enabled by LLM_FAILOVER_ON → {sorted(reasons)}")
    if reason and not g_reason:
        blockers.append(f"LLM_FAILOVER_ON={sorted(reasons)} excludes {reason!r} "
                        "— default is billing,rate_limit,overloaded")

    # --- 3. the real decision ------------------------------------------------
    verdict = agent._should_failover(Exception(REAL_ERROR), None)
    print("\n[3] Actual _should_failover() verdict on the live error")
    print(f"      → {verdict!r}  "
          f"{'(WOULD fail over)' if verdict else '(would NOT fail over — turn dies with the primary error)'}")

    if hasattr(agent, "_rail"):
        r = agent._rail()
        print(f"\n[4] Primary rail = {r!r}")
        if r == "openai":
            import openai_client
            print(f"      openai_available: {openai_client.openai_available()} "
                  f"(model {openai_client.model()})")
            if not openai_client.openai_available():
                print(f"      {WARN} OPENAI_API_KEY missing / USE_MOCK_OPENAI=1 → the rail "
                      "returns MOCK text instead of erroring. You'd see a placeholder "
                      "reply, not a failover.")

    # --- 4. optional live ping ----------------------------------------------
    if args.ping:
        print("\n[5] Live DeepSeek ping (one real call)")
        if not g_avail:
            print(f"      {BAD} skipped — gate B failed, the call would return mock text")
        else:
            async def _go():
                return await deepseek_client.complete(
                    "Reply with the single word: ok",
                    [{"role": "user", "content": "ping"}], max_tokens=16,
                )
            try:
                out = asyncio.run(_go())
                txt = (out.get("text") or "").strip()
                if txt.startswith("[mock"):
                    print(f"      {BAD} returned MOCK text — the real path is not active")
                else:
                    print(f"      {OK} real reply: {txt[:80]!r}")
            except Exception as e:  # noqa: BLE001
                print(f"      {BAD} DeepSeek call FAILED: {e}")
                print("      → the key/base-url/model is wrong, or the account has no credit.")
                print("      NOTE: a failing DeepSeek would ALSO surface as 'no failover' to "
                      "the user, because the restarted turn errors too.")

    # --- verdict -------------------------------------------------------------
    print("\n" + "=" * 68)
    if verdict:
        print("  Gates all pass. If the app still shows the primary's error:")
        print("   • the running container predates these vars — REDEPLOY (Railway does")
        print("     restart on a variable change, but confirm the deploy actually rolled)")
        print("   • you're looking at the wrong service (web vs cron)")
        print("   • DeepSeek itself is failing → re-run with --ping")
        print("   • the failing turn carried an IMAGE attachment (never falls back)")
    else:
        print("  BLOCKED. Fix these, in order:\n")
        for i, b in enumerate(blockers or ["(see the failed gate above)"], 1):
            print(f"   {i}. {b}")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    main()
