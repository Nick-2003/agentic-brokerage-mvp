"""Offline test for W4 — connect/waitlist storage + Flex-token encryption.

Fully offline: a fake Supabase client (no DB), an ephemeral Fernet key (no real
secret). Proves encryption-at-rest, the token never leaving via user reads, the
RLS-vs-service-key split (by which client builder each function uses), the cron
read decrypting + filtering, and crypto round-trip / fail-closed.

    backend/.venv/bin/python proposed_changes/W4-storage-connect/scripts/test_W4_connections.py
    # (or, once applied:  backend/.venv/bin/python scripts/test_W4_connections.py)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"   # connections.py, token_crypto.py
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))            # db.py (config + _client_for_user)
sys.path.insert(0, str(_COLOCATED_BACKEND))

import token_crypto as tc  # noqa: E402

os.environ["FLEX_TOKEN_ENC_KEY"] = tc.generate_key()  # ephemeral key for the run
# Dummy Supabase config so the real config readers pass (clients are all stubbed).
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

import connections as conn  # noqa: E402

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


# --- a minimal chainable fake Supabase client -------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, name, store):
        self.name, self.store, self._filters, self._op = name, store, [], None

    def upsert(self, row, on_conflict=None):
        self._op = ("upsert", row)
        self.store.setdefault("upserts", []).append((self.name, row))
        return self

    def insert(self, row):
        self._op = ("insert", row)
        self.store.setdefault("inserts", []).append((self.name, row))
        return self

    def update(self, row):
        self._op = ("update", row)
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
        op = self._op[0]
        if op == "select":
            rows = list(self.store.get("rows", {}).get(self.name, []))
            for k, v in self._filters:
                rows = [r for r in rows if r.get(k) == v]
            self.store.setdefault("select_filters", []).append((self.name, list(self._filters)))
            return _Result(rows)
        if op == "upsert":
            return _Result([{**self._op[1], "created_at": "t", "updated_at": "t"}])
        if op == "update":
            base = (self.store.get("rows", {}).get(self.name) or [{}])[0]
            return _Result([{**base, **self._op[1]}])
        if op == "insert":
            return _Result([dict(self._op[1])])
        return _Result([])


class _FakeClient:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _FakeTable(name, self.store)


async def main() -> None:
    PLAINTEXT = "987654321098765432"  # a Flex-token-shaped string

    # ---- token_crypto ----
    ct = tc.encrypt_token(PLAINTEXT)
    check("crypto: configured", tc.token_crypto_configured() is True)
    check("crypto: ciphertext != plaintext", ct != PLAINTEXT and PLAINTEXT not in ct)
    check("crypto: round-trip", tc.decrypt_token(ct) == PLAINTEXT)
    try:
        tc.decrypt_token("not-a-valid-fernet-token")
        check("crypto: bad ciphertext raises", False)
    except tc.TokenCryptoError as e:
        check("crypto: bad ciphertext → decrypt_failed", e.code == "token_crypto_decrypt_failed")
    _k = os.environ.pop("FLEX_TOKEN_ENC_KEY")
    check("crypto: no key → not configured", tc.token_crypto_configured() is False)
    try:
        tc.encrypt_token(PLAINTEXT)
        check("crypto: no key encrypt raises", False)
    except tc.TokenCryptoError as e:
        check("crypto: no key → no_key", e.code == "token_crypto_no_key")
    os.environ["FLEX_TOKEN_ENC_KEY"] = _k  # restore

    # ---- wire fakes ----
    store: dict = {"rows": {}}
    user_client = _FakeClient(store)
    admin_client = _FakeClient(store)

    async def _fake_user(jwt):  # noqa: ANN001
        store["last_user_jwt"] = jwt
        return user_client

    async def _fake_admin():
        store["admin_used"] = True
        return admin_client

    async def _fake_acreate(url, key):  # noqa: ANN001 — waitlist uses this directly
        store["waitlist_key"] = key
        return _FakeClient(store)

    conn._client_for_user = _fake_user      # type: ignore[assignment]
    conn._admin_client = _fake_admin        # type: ignore[assignment]
    conn.acreate_client = _fake_acreate     # type: ignore[assignment]

    # ---- upsert_my_connection: encryption-at-rest + token never returned ----
    view = await conn.upsert_my_connection(
        "user-jwt", "user-uuid-1",
        flex_token=PLAINTEXT, flex_query_id="Q123", whatsapp_number="+85291234567",
    )
    stored = dict(store["upserts"][-1][1])
    check("upsert: used the user-JWT client (RLS)", store.get("last_user_jwt") == "user-jwt")
    check("upsert: store table is ibkr_connections", store["upserts"][-1][0] == "ibkr_connections")
    check("upsert: plaintext token NOT stored", "flex_token" not in stored)
    check("upsert: ciphertext stored", stored["flex_token_encrypted"] != PLAINTEXT)
    check("upsert: ciphertext decrypts to plaintext",
          tc.decrypt_token(stored["flex_token_encrypted"]) == PLAINTEXT)
    check("upsert: returned view has NO token field",
          "flex_token" not in view and "flex_token_encrypted" not in view)
    check("upsert: returned view has whatsapp_number", view.get("whatsapp_number") == "+85291234567")
    check("upsert: status active", view.get("status") == "active")

    # ---- get_my_connection: token-free public view ----
    store["rows"]["ibkr_connections"] = [
        {"user_id": "user-uuid-1", "flex_query_id": "Q123", "whatsapp_number": "+85291234567",
         "opt_in": True, "status": "active", "created_at": "t", "updated_at": "t"}
    ]
    got = await conn.get_my_connection("user-jwt")
    check("get: returns the row", got is not None and got["user_id"] == "user-uuid-1")
    check("get: no token leaked", "flex_token_encrypted" not in got and "flex_token" not in got)

    # ---- list_active_connections_admin: service key, decrypts, filters opt-in ----
    store["rows"]["ibkr_connections"] = [
        {"user_id": "A", "flex_token_encrypted": tc.encrypt_token("tokA"), "flex_query_id": "QA",
         "whatsapp_number": "+1", "opt_in": True, "status": "active"},
        {"user_id": "B", "flex_token_encrypted": tc.encrypt_token("tokB"), "flex_query_id": "QB",
         "whatsapp_number": "+2", "opt_in": False, "status": "active"},   # opted out
        {"user_id": "C", "flex_token_encrypted": "garbage-ciphertext", "flex_query_id": "QC",
         "whatsapp_number": "+3", "opt_in": True, "status": "active"},    # undecryptable
    ]
    store.pop("admin_used", None)
    active = await conn.list_active_connections_admin()
    check("admin: used the SERVICE-KEY client", store.get("admin_used") is True)
    filters = dict(store["select_filters"][-1][1])
    check("admin: filtered opt_in=True", filters.get("opt_in") is True)
    check("admin: filtered status=active", filters.get("status") == "active")
    by_user = {r["user_id"]: r for r in active}
    check("admin: opted-out user B excluded", "B" not in by_user)
    check("admin: user A token decrypted", by_user.get("A", {}).get("flex_token") == "tokA")
    check("admin: user A query id passed", by_user.get("A", {}).get("flex_query_id") == "QA")
    check("admin: undecryptable C kept w/ null token + error",
          by_user.get("C", {}).get("flex_token") is None
          and by_user.get("C", {}).get("decrypt_error") == "token_crypto_decrypt_failed")
    check("admin: token ciphertext not exposed in records",
          all("flex_token_encrypted" not in r for r in active))

    # ---- log_delivery_admin: metadata only, no brief body ----
    await conn.log_delivery_admin("user-uuid-1", status="queued", account_id="U19883362",
                                  as_of="2026-06-05", provider_id="SM_x")
    logged = dict(store["inserts"][-1][1])
    check("log: table briefing_deliveries", store["inserts"][-1][0] == "briefing_deliveries")
    check("log: status recorded", logged.get("status") == "queued")
    check("log: account/as_of recorded", logged.get("account_id") == "U19883362" and logged.get("as_of") == "2026-06-05")
    check("log: NO brief body fields", not any(k in logged for k in ("text", "body", "brief")))

    # ---- waitlist: anon insert, lowercased ----
    ok = await conn.add_waitlist_signup("Test@Example.COM", source="landing")
    wl = dict(store["inserts"][-1][1])
    check("waitlist: returns True", ok is True)
    check("waitlist: email lowercased", wl.get("email") == "test@example.com")
    check("waitlist: used anon key (not service)", store.get("waitlist_key") != os.getenv("SUPABASE_SERVICE_KEY"))


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{_PASS}/{_PASS + _FAIL} passed")
    sys.exit(1 if _FAIL else 0)
