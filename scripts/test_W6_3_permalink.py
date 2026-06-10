"""Offline test for W6.3 — published-brief store + permalink endpoint. Fully
offline (stubbed Supabase admin client; no DB). Covers publish (token + payload),
get (hit / miss / EXPIRED), the public GET /api/brief/{token} (200 / 404), and the
scheduler publishing + appending the permalink to the sent body.

    backend/.venv/bin/python scripts/test_W6_3_permalink.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
sys.path.insert(0, str(_COLOCATED_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ["PUBLIC_BASE_URL"] = "https://briefs.example.com"

import published_briefs as pb  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond):  # noqa: ANN001
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}")


# --- fake admin Supabase client (records inserts; returns canned selects) -----
STORE: dict = {"inserts": [], "rows": []}


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self):
        self._filters = []
        self._op = None

    def insert(self, row):
        self._op = ("insert", row)
        STORE["inserts"].append(row)
        return self

    def select(self, cols="*"):
        self._op = ("select", cols)
        return self

    def eq(self, k, v):
        self._filters.append((k, v))
        return self

    def limit(self, n):
        return self

    async def execute(self):
        if self._op[0] == "insert":
            return _Result([self._op[1]])
        rows = STORE["rows"]
        for k, v in self._filters:
            rows = [r for r in rows if r.get(k) == v]
        return _Result(rows)


class _Client:
    def table(self, name):
        return _Table()


async def _fake_admin():
    return _Client()


pb._admin_client = _fake_admin  # type: ignore[assignment]


async def main() -> None:
    # ---- publish_brief ----
    out = await pb.publish_brief("user-1", "📉 *Your book* down -1%", account_id="U1", as_of="2026-06-08")
    check("publish: returns token", bool(out["token"]) and len(out["token"]) > 20)
    check("publish: permalink = base/b/token", out["permalink"] == f"https://briefs.example.com/b/{out['token']}")
    ins = STORE["inserts"][-1]
    check("publish: body stored", ins["body"] == "📉 *Your book* down -1%")
    check("publish: token + user + expiry stored",
          ins["token"] == out["token"] and ins["user_id"] == "user-1" and "expires_at" in ins)
    try:
        await pb.publish_brief("u", "   ")
        check("publish: empty body rejected", False)
    except ValueError:
        check("publish: empty body rejected", True)

    # ---- get_published_brief: hit ----
    tok = out["token"]
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    STORE["rows"] = [{"token": tok, "body": "📉 *Your book* down -1%", "account_id": "U1",
                      "as_of": "2026-06-08", "created_at": "2026-06-08T00:00:00+00:00",
                      "expires_at": future}]
    got = await pb.get_published_brief(tok)
    check("get: returns the brief text", got and got["text"] == "📉 *Your book* down -1%")
    check("get: carries account/as_of", got["account_id"] == "U1" and got["as_of"] == "2026-06-08")

    # ---- get: miss ----
    check("get: unknown token → None", await pb.get_published_brief("nope") is None)
    check("get: empty token → None", await pb.get_published_brief("") is None)

    # ---- get: EXPIRED → None (fail-closed) ----
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    STORE["rows"] = [{"token": tok, "body": "x", "expires_at": past}]
    check("get: expired → None", await pb.get_published_brief(tok) is None)

    # ---- public endpoint GET /api/brief/{token} ----
    import brief_api  # noqa: E402
    from fastapi import FastAPI  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    app = FastAPI()
    app.include_router(brief_api.router)
    client = TestClient(app)

    STORE["rows"] = [{"token": tok, "body": "hello brief", "account_id": "U1",
                      "as_of": "2026-06-08", "created_at": "t", "expires_at": future}]
    r = client.get(f"/api/brief/{tok}")
    check("endpoint: 200 + text", r.status_code == 200 and r.json()["text"] == "hello brief")
    STORE["rows"] = []
    r = client.get("/api/brief/missing")
    check("endpoint: missing → 404", r.status_code == 404)

    # ---- scheduler publishes + appends the permalink to the sent body ----
    import scheduler as sch  # noqa: E402

    sent = {}

    async def fake_list():
        return [{"user_id": "user-1", "whatsapp_number": "+1", "flex_token": "t", "flex_query_id": "Q"}]

    async def fake_build(token, qid):  # noqa: ANN001
        return {"text": "BRIEF BODY", "account_id": "U1", "as_of": "2026-06-08"}

    async def fake_publish(uid, body, **kw):  # noqa: ANN001
        return {"token": "TOK123", "permalink": "https://briefs.example.com/b/TOK123"}

    async def fake_send(brief, to):  # noqa: ANN001
        sent["body"] = brief["text"]
        sent["to"] = to
        return {"is_mock": False, "to": to, "sid": "SM1", "status": "queued"}

    async def fake_log(uid, **kw):  # noqa: ANN001
        return None

    sch.connections.list_active_connections_admin = fake_list   # type: ignore[assignment]
    sch.connections.log_delivery_admin = fake_log               # type: ignore[assignment]
    sch.briefing.build_briefing = fake_build                    # type: ignore[assignment]
    sch.published_briefs.publish_brief = fake_publish           # type: ignore[assignment]
    sch.whatsapp.send_briefing = fake_send                      # type: ignore[assignment]
    sch._RETRY_DELAYS = []
    sch._PUBLISH = True

    s = await sch.run_daily_briefings(max_users=1)
    check("scheduler: sent ok", s["sent"] == 1)
    check("scheduler: result carries permalink", s["results"][0]["permalink"] == "https://briefs.example.com/b/TOK123")
    check("scheduler: sent body keeps the brief", sent["body"].startswith("BRIEF BODY"))
    check("scheduler: sent body appends the permalink", "https://briefs.example.com/b/TOK123" in sent["body"])

    # publish failure must NOT block the send (graceful)
    async def boom_publish(uid, body, **kw):  # noqa: ANN001
        raise RuntimeError("supabase down")

    sch.published_briefs.publish_brief = boom_publish  # type: ignore[assignment]
    sent.clear()
    s = await sch.run_daily_briefings(max_users=1)
    check("scheduler: publish failure still sends", s["sent"] == 1 and sent["body"] == "BRIEF BODY")
    check("scheduler: no permalink on publish failure", s["results"][0]["permalink"] is None)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
