#!/usr/bin/env python3
"""Offline security/contract checks for proposal 086."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
LIVE_BACKEND = ROOT.parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(1, str(LIVE_BACKEND))


def main() -> None:
    os.environ["BROKER_SECRET_ENC_KEY"] = Fernet.generate_key().decode()
    crypto = importlib.import_module("broker_secret_crypto")
    connections = importlib.import_module("broker_connections")

    plaintext = "snaptrade-user-secret"
    ciphertext = crypto.encrypt_broker_secret(plaintext)
    assert ciphertext.startswith("fernet-v1:")
    assert plaintext not in ciphertext
    assert crypto.decrypt_broker_secret(ciphertext) == plaintext
    assert crypto.broker_secret_crypto_configured()

    original_key = os.environ["BROKER_SECRET_ENC_KEY"]
    os.environ["BROKER_SECRET_ENC_KEY"] = Fernet.generate_key().decode()
    try:
        crypto.decrypt_broker_secret(ciphertext)
    except crypto.BrokerSecretCryptoError as exc:
        assert exc.code == "broker_secret_decrypt_failed"
    else:
        raise AssertionError("wrong key must fail closed")
    os.environ["BROKER_SECRET_ENC_KEY"] = original_key

    raw_connection = {
        "id": "connection-a",
        "provider": "snaptrade",
        "external_user_id": "must-not-reach-browser",
        "external_connection_id": "must-not-reach-browser",
        "encrypted_user_secret": ciphertext,
        "status": "active",
    }
    public_connection = connections._public_connection(raw_connection)
    assert public_connection == {
        "id": "connection-a",
        "provider": "snaptrade",
        "status": "active",
    }

    raw_account = {
        "id": "account-a",
        "connection_id": "connection-a",
        "external_account_id": "must-not-reach-browser",
        "masked_name": "IBKR ••1234",
        "base_currency": "USD",
        "is_selected": False,
        "status": "active",
    }
    assert "external_account_id" not in connections._public_account(raw_account)

    rows = connections._account_rows(
        user_id="user-a",
        connection_id="connection-a",
        accounts=[
            {
                "external_account_id": "external-account-a",
                "masked_name": "IBKR ••1234",
                "base_currency": "usd",
                "is_selected": True,
            }
        ],
    )
    assert rows[0]["user_id"] == "user-a"
    assert rows[0]["base_currency"] == "USD"
    assert "is_selected" not in rows[0], "provider refresh must preserve selection"

    try:
        connections._account_rows(
            user_id="user-a",
            connection_id="connection-a",
            accounts=[{"external_account_id": "a", "base_currency": "$"}],
        )
    except connections.BrokerConnectionStateError as exc:
        assert exc.code == "broker_account_invalid"
    else:
        raise AssertionError("non-ISO currency must fail")

    schema = (ROOT / "backend/db/schema_broker_connections.sql").read_text()
    assert schema.count("enable row level security") == 2
    assert "(select auth.uid()) = user_id" in schema
    assert "with check ((select auth.uid()) = user_id)" in schema
    assert "foreign key (connection_id, user_id)" in schema
    assert "broker_accounts_one_selected_per_user" in schema
    assert "security invoker" in schema
    assert "security definer" not in schema
    assert "revoke all on table public.broker_connections from anon" in schema
    assert "grant execute on function public.select_my_broker_account" in schema

    print("086 essential connection state: 24 assertions passed")


if __name__ == "__main__":
    main()
