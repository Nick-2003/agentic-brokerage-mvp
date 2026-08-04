#!/usr/bin/env python3
"""Validate sanitised SnapTrade fixtures and, after apply, the live adapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TEST_FILE = Path(__file__).resolve()
IN_PROPOSAL = ".proposed_changes" in TEST_FILE.parts
ROOT = TEST_FILE.parents[3] if IN_PROPOSAL else TEST_FILE.parents[1]
PROPOSAL = ROOT / ".proposed_changes/093-snaptrade-fixtures-isolation-live-verification"
FIXTURES = (
    PROPOSAL / "backend/tests/fixtures/snaptrade"
    if IN_PROPOSAL
    else ROOT / "backend/tests/fixtures/snaptrade"
)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


details = load("account_details_usd.json")
paper = load("account_details_paper.json")
balances = load("balances_multi_currency.json")
positions = load("positions_mixed.json")
empty = load("empty_account.json")

# Current documented wire shapes, not the older nested currency/symbol variants.
assert details["balance"]["total"]["currency"] == "USD"
assert details["sync_status"]["holdings"]["last_successful_sync"].endswith("Z")
assert positions["results"][0]["instrument"]["symbol"] == "AAPL"
assert positions["results"][0]["cost_basis"] == "100"
assert balances[0]["currency"]["code"] == "USD"
assert paper["is_paper"] is True
assert empty["positions"]["results"] == []

banned_keys = {
    "userid",
    "usersecret",
    "clientid",
    "consumerkey",
    "accesstoken",
    "refreshtoken",
    "email",
}
for path in FIXTURES.glob("*.json"):
    body = json.loads(path.read_text())
    for key, value in walk(body):
        assert key.replace("_", "").lower() not in banned_keys, f"sensitive key in {path.name}: {key}"
        if key in {"id", "brokerage_authorization"} and isinstance(value, str):
            assert value.startswith("00000000-"), f"non-fixture UUID in {path.name}"
    rendered = path.read_text().lower()
    assert "@" not in rendered
    assert "secret" not in rendered

rls_path = (
    PROPOSAL / "supabase/tests/database/093_broker_connections_rls.test.sql"
    if IN_PROPOSAL
    else ROOT / "supabase/tests/database/093_broker_connections_rls.test.sql"
)
rls_test = rls_path.read_text()
assert "select plan(22)" in rls_test
assert rls_test.strip().startswith("begin;") and rls_test.strip().endswith("rollback;")
assert rls_test.count("set local role authenticated") == 2
assert rls_test.count("set local request.jwt.claim.sub") == 2
assert "set local role anon" in rls_test
assert "security definer" not in rls_test.lower()
assert "service_role" not in rls_test.lower()

if IN_PROPOSAL:
    api_patch = (PROPOSAL / "backend/snaptrade_api.py.patch").read_text()
    provider_patch = (PROPOSAL / "backend/snaptrade_provider.py.patch").read_text()
    assert '("balance", "total", "currency")' in api_patch
    assert '("sync_status", "holdings", "last_successful_sync")' in provider_patch
    assert '"paper_snaptrade" if is_paper' in provider_patch
else:
    sys.path.insert(0, str(ROOT / "backend"))
    import snaptrade_api  # noqa: E402
    from snaptrade_provider import normalise_snaptrade_portfolio  # noqa: E402

    context = {
        "account_id": "local-fixture-account",
        "base_currency": "USD",
    }
    assert snaptrade_api._account_currency(details, details) == "USD"
    result = normalise_snaptrade_portfolio(
        context=context,
        details=details,
        balances=balances,
        positions=positions["results"],
    )
    assert result["total_equity"] == 10000.0
    assert result["cash"] == 1000.0
    assert result["buying_power"] == 2000.0
    assert result["as_of"] == "2026-08-03T02:00:00.000Z"
    assert result["positions"][0]["ticker"] == "AAPL"
    assert result["positions"][0]["market_value"] == 1500.0
    assert result["positions"][1]["market_value"] is None
    assert result["is_paper"] is False

    paper_result = normalise_snaptrade_portfolio(
        context=context,
        details=paper,
        balances=balances[:1],
        positions=[],
    )
    assert paper_result["is_paper"] is True
    assert paper_result["account_kind"] == "paper_snaptrade"
    assert paper_result["account_label"] == "Paper · SnapTrade"

print("093 snaptrade fixtures: PASS")
