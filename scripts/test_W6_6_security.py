"""Offline test for W6.6 — the SecurityMiddleware (rate limit + security headers).
Fully offline: a tiny FastAPI app with the middleware + FastAPI's TestClient.

Covers: headers on every response, per-IP fixed-window 429 (with Retry-After),
/healthz + Twilio webhooks + OPTIONS preflight all exempt, per-IP isolation via
X-Forwarded-For, fail-open on an internal error, and RATE_LIMIT_ENABLED=0.

    backend/.venv/bin/python .proposed_changes/W6.6-waitlist-security/scripts/test_W6_6_security.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"          # W6.6 proposal copies
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

import security  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}")


def _app() -> TestClient:
    app = FastAPI()

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/thing")
    def thing():
        return {"ok": True}

    @app.post("/api/waitlist")
    def waitlist():
        return {"ok": True}

    @app.post("/api/twilio/inbound")
    def inbound():
        return {"ok": True}

    app.add_middleware(security.SecurityMiddleware)
    return TestClient(app)


def _reset(**env):
    security._hits.clear()
    for k in ("RATE_LIMIT_ENABLED", "RATE_LIMIT_WINDOW_SECONDS", "RATE_LIMIT_MAX_REQUESTS"):
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v


def main() -> None:
    # ---- 1. security headers on every response ----
    _reset()
    c = _app()
    r = c.get("/api/thing")
    h = r.headers
    check("headers: nosniff", h.get("x-content-type-options") == "nosniff")
    check("headers: frame DENY", h.get("x-frame-options") == "DENY")
    check("headers: referrer-policy", h.get("referrer-policy") == "no-referrer")
    check("headers: permissions-policy", "camera=()" in (h.get("permissions-policy") or ""))
    check("headers: HSTS", "max-age=" in (h.get("strict-transport-security") or ""))
    check("headers: present on /healthz too", c.get("/healthz").headers.get("x-frame-options") == "DENY")

    # ---- 2. rate limit: 3 ok, 4th → 429 ----
    _reset(RATE_LIMIT_MAX_REQUESTS="3", RATE_LIMIT_WINDOW_SECONDS="100")
    c = _app()
    codes = [c.get("/api/thing").status_code for _ in range(4)]
    check("ratelimit: first 3 ok", codes[:3] == [200, 200, 200])
    check("ratelimit: 4th is 429", codes[3] == 429)
    r = c.get("/api/thing")
    check("ratelimit: 429 carries Retry-After", r.headers.get("retry-after") == "100")
    check("ratelimit: 429 carries security headers", r.headers.get("x-frame-options") == "DENY")
    check("ratelimit: POST /api/waitlist also guarded",
          c.post("/api/waitlist").status_code == 429)

    # ---- 3. exemptions: /healthz + Twilio webhooks never limited ----
    _reset(RATE_LIMIT_MAX_REQUESTS="2", RATE_LIMIT_WINDOW_SECONDS="100")
    c = _app()
    check("exempt: /healthz never 429", all(c.get("/healthz").status_code == 200 for _ in range(10)))
    check("exempt: /api/twilio/* never 429",
          all(c.post("/api/twilio/inbound").status_code == 200 for _ in range(10)))

    # ---- 4. OPTIONS preflight not counted ----
    _reset(RATE_LIMIT_MAX_REQUESTS="2", RATE_LIMIT_WINDOW_SECONDS="100")
    c = _app()
    for _ in range(5):
        c.options("/api/thing")  # not counted
    after = [c.get("/api/thing").status_code for _ in range(3)]
    check("OPTIONS not counted: 2 ok then 429", after == [200, 200, 429])

    # ---- 5. per-IP isolation via X-Forwarded-For ----
    _reset(RATE_LIMIT_MAX_REQUESTS="1", RATE_LIMIT_WINDOW_SECONDS="100")
    c = _app()
    a1 = c.get("/api/thing", headers={"x-forwarded-for": "1.1.1.1"}).status_code
    a2 = c.get("/api/thing", headers={"x-forwarded-for": "1.1.1.1"}).status_code
    b1 = c.get("/api/thing", headers={"x-forwarded-for": "2.2.2.2"}).status_code
    check("per-IP: A 1st ok, A 2nd 429, B 1st ok", (a1, a2, b1) == (200, 429, 200))

    # ---- 6. fail-open on internal error ----
    _reset(RATE_LIMIT_MAX_REQUESTS="1", RATE_LIMIT_WINDOW_SECONDS="100")
    c = _app()
    orig = security._over_limit
    security._over_limit = lambda ip: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore
    try:
        codes = [c.get("/api/thing").status_code for _ in range(5)]
        check("fail-open: served despite limiter error", codes == [200] * 5)
    finally:
        security._over_limit = orig  # type: ignore

    # ---- 7. disabled → no limiting ----
    _reset(RATE_LIMIT_ENABLED="0", RATE_LIMIT_MAX_REQUESTS="1")
    c = _app()
    check("disabled: no 429", all(c.get("/api/thing").status_code == 200 for _ in range(10)))
    check("disabled: headers still applied", c.get("/api/thing").headers.get("x-frame-options") == "DENY")

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
