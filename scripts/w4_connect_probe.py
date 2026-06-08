"""Live probe for W4 — the admin/crypto side (acceptance criterion #5 + the loop close).

Runs the SYSTEM read the W5 cron will use: `list_active_connections_admin()` (service
key → decrypts each Flex token in-process), and reports whether the decrypted token
round-tripped intact. With `--fetch`, it feeds the decrypted token straight into the
W1 Flex client to prove the stored→encrypted→decrypted token actually fetches IBKR.

Needs (in backend/.env): SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_KEY,
FLEX_TOKEN_ENC_KEY, and at least one row in ibkr_connections (create it via
POST /api/ibkr/connect — see the W4 README verification steps).

    backend/.venv/bin/python scripts/w4_connect_probe.py          # list + decrypt check
    backend/.venv/bin/python scripts/w4_connect_probe.py --fetch  # + real IBKR fetch via the stored token
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "db.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_backend / ".env")
    except Exception:
        pass
sys.path.insert(0, str(_COLOCATED_BACKEND))

import connections as conn  # noqa: E402


async def main() -> None:
    do_fetch = "--fetch" in sys.argv[1:]
    print(f"connect_storage_configured: {conn.connect_storage_configured()}")
    try:
        rows = await conn.list_active_connections_admin()
    except Exception as e:  # noqa: BLE001
        print(f"\nadmin read failed: {type(e).__name__}: {e}")
        print("→ check SUPABASE_SERVICE_KEY, FLEX_TOKEN_ENC_KEY, and that schema_waitlist.sql ran.")
        sys.exit(1)

    print(f"\nactive opted-in connections: {len(rows)}")
    env_token = os.getenv("IBKR_FLEX_TOKEN")  # to confirm an exact round-trip (W1's token)
    for r in rows:
        tok = r.get("flex_token")
        match = " (== IBKR_FLEX_TOKEN ✓)" if env_token and tok == env_token else ""
        state = (
            f"decrypted len={len(tok)}{match}" if tok
            else f"DECRYPT FAILED ({r.get('decrypt_error')})"
        )
        print(f"  user={r['user_id']}  query_id={r.get('flex_query_id')}  "
              f"whatsapp={r.get('whatsapp_number')}  token: {state}")
        # Confirm the token is never the ciphertext column leaking through.
        assert "flex_token_encrypted" not in r, "ciphertext leaked into the admin record!"

    if do_fetch and rows and rows[0].get("flex_token"):
        import ibkr_flex  # noqa: PLC0415

        r = rows[0]
        print(f"\n--fetch: pulling IBKR via the STORED token for {r['user_id']}…")
        try:
            snap = await ibkr_flex.fetch_flex_statement(r["flex_token"], r["flex_query_id"])
            print(f"  ✓ stored token works: account={snap['account_id']} base={snap['base_currency']} "
                  f"holdings={len(snap['positions'])}")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ fetch failed: {type(e).__name__}: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
