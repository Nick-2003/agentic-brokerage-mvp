#!/usr/bin/env python3
"""Offline guard for Proposal 064 — safe handling of Anthropic API errors.

Reported: the chat showed `agent error: Error code: 400 - {… 'Your credit balance
is too low …'}` verbatim (out-of-credit account). 064 classifies provider errors
into a short, safe user message + stable `code`, logs the full detail server-side,
and never leaks billing state / request_id / model to the user.

Covers:
  A. the REAL credit-balance error → ("temporarily unavailable", provider_unavailable)
     and the user message leaks NONE of: credit/balance/400/request_id/invalid_request/model;
  B. rate-limit / auth (real SDK instances) → rate_limited / provider_unavailable;
  C. a generic exception → agent_error (still no raw leak);
  D. wiring: `_classify_agent_error` importable from agent; agent.py + main.py no
     longer emit the raw `agent error:`/`stream failed: {e}` strings.

Self-contained: temp-applies backend/{agent.py, main.py} over live, imports,
asserts, restores in a finally. Anchored on backend/auth.py.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_064_api_error_handling.py
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"could not locate repo root (backend/auth.py) above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes", "064-api-error-handling")
FILES = [
    (os.path.join(BACKEND, "agent.py"), os.path.join(PROP, "backend", "agent.py")),
    (os.path.join(BACKEND, "main.py"), os.path.join(PROP, "backend", "main.py")),
]

# The exact user-reported error string.
CREDIT_ERR = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API. Please go "
    "to Plans & Billing to upgrade or purchase credits.'}, 'request_id': 'req_011CcpE1P9'}"
)

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def run() -> None:
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    import agent  # noqa: E402 — after temp-apply

    print("\n=== A. the real credit-balance error is classified + never leaked ===")
    # BadRequestError is how the SDK delivers it; classification is substring-based
    # so a plain Exception with the same text is faithful.
    msg, code = agent._classify_agent_error(Exception(CREDIT_ERR))
    check("code is provider_unavailable", code == "provider_unavailable", code)
    check("message is the safe 'temporarily unavailable' copy", "temporarily unavailable" in msg.lower())
    leaks = ["credit", "balance", "400", "request_id", "req_", "invalid_request",
             "anthropic", "plans & billing", agent.MODEL.lower()]
    present = [w for w in leaks if w in msg.lower()]
    check("user message leaks nothing sensitive", not present, f"leaked: {present}")

    print("\n=== B. rate-limit / auth (real SDK error instances) ===")
    import httpx  # noqa: E402
    from anthropic import AuthenticationError, RateLimitError  # noqa: E402
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    rate = RateLimitError("rate limited", response=httpx.Response(429, request=req), body=None)
    _, rcode = agent._classify_agent_error(rate)
    check("RateLimitError → rate_limited", rcode == "rate_limited", rcode)
    auth = AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
    _, acode = agent._classify_agent_error(auth)
    check("AuthenticationError → provider_unavailable", acode == "provider_unavailable", acode)

    print("\n=== C. generic exception ===")
    gmsg, gcode = agent._classify_agent_error(ValueError("something odd at 0xdeadbeef"))
    check("unknown error → agent_error", gcode == "agent_error", gcode)
    check("generic message leaks no internals", "0xdeadbeef" not in gmsg)

    print("\n=== D. wiring: no raw error strings remain ===")
    a_src = open(os.path.join(BACKEND, "agent.py"), encoding="utf-8").read()
    m_src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
    check("agent.py no longer yields 'agent error: {e}'", 'f"agent error: {e}"' not in a_src)
    check("agent.py uses the classifier in its except", "_classify_agent_error(e)" in a_src)
    check("main.py no longer yields 'stream failed: {e}'", 'f"stream failed: {e}"' not in m_src)
    check("main.py uses the classifier", "_classify_agent_error(e)" in m_src)


def main() -> int:
    backups: list[tuple[str, str]] = []
    try:
        for live, prop in FILES:
            if not os.path.isfile(prop):
                print(f"missing proposal file: {prop}")
                return 1
            bak = live + ".064bak"
            shutil.copy2(live, bak)
            backups.append((live, bak))
            shutil.copy2(prop, live)
        run()
    finally:
        for live, bak in backups:
            shutil.copy2(bak, live)
            os.remove(bak)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
