#!/usr/bin/env python3
"""Read-only live SnapTrade portfolio verification with redacted output."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from broker_provider import PortfolioRequest, portfolio_providers  # noqa: E402
import broker_connections  # noqa: E402
import tools  # noqa: E402, F401  # registers the applied SnapTrade provider

ACK = "I_UNDERSTAND_READ_ONLY_LIVE_CALLS"


async def main() -> None:
    if os.getenv("SNAPTRADE_LIVE_VERIFY") != ACK:
        raise SystemExit(f"set SNAPTRADE_LIVE_VERIFY={ACK} to permit read-only live calls")
    user_id = os.getenv("SNAPTRADE_VERIFY_APP_USER_ID", "").strip()
    if not user_id:
        raise SystemExit("missing SNAPTRADE_VERIFY_APP_USER_ID")

    context = await broker_connections.get_selected_broker_context_admin(user_id)
    if not context or context.get("provider") != "snaptrade":
        raise SystemExit("the test app user has no selected active SnapTrade account")
    forbidden = {
        str(context.get("external_user_id") or ""),
        str(context.get("user_secret") or ""),
        str(context.get("external_connection_id") or ""),
        str(context.get("external_account_id") or ""),
    }
    forbidden.discard("")

    snapshot = await portfolio_providers.get_snapshot(
        "snaptrade", PortfolioRequest(user_id=user_id)
    )
    rendered = json.dumps(snapshot, default=str)
    assert not any(secret in rendered for secret in forbidden), "provider identifiers leaked"
    assert snapshot.get("connected") is True, snapshot.get("error", "not connected")
    assert snapshot.get("read_only") is True
    assert snapshot.get("is_mock") is False
    assert snapshot.get("provider") == "snaptrade"

    # Deliberately omit user/account IDs, holdings symbols, values, and provider errors.
    summary = {
        "ok": True,
        "provider": snapshot["provider"],
        "data_source": snapshot.get("data_source"),
        "connected": snapshot["connected"],
        "read_only": snapshot["read_only"],
        "is_paper": snapshot.get("is_paper"),
        "base_currency_present": bool(snapshot.get("base_currency")),
        "equity_present": snapshot.get("total_equity") is not None,
        "positions_count": len(snapshot.get("positions") or []),
        "freshness_present": bool(snapshot.get("as_of")),
        "normalization_warning_count": len(snapshot.get("normalization_warnings") or []),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
