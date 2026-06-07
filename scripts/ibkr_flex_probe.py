"""Live probe for the IBKR Flex connector (W1) — the first-real-run tool.

Runs the real 2-step flow with the token + query ID in backend/.env, dumps the
raw statement XML to /tmp for inspection, and prints the parsed snapshot. Use it
to reconcile parse_flex_statement()'s tag/attr mappings + the fixture against the
real statement (the known unknown — same pattern as fmp_probe.py for FMP fields).

    IBKR_FLEX_TOKEN=... IBKR_FLEX_QUERY_ID=... backend/.venv/bin/python scripts/ibkr_flex_probe.py
    # (or just set them in backend/.env and run the script)
"""

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(HERE, os.pardir, "backend")
sys.path.insert(0, _BACKEND)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_BACKEND, ".env"))
except Exception:
    pass

import ibkr_flex as ib  # noqa: E402

_RAW_DUMP = "/tmp/ibkr_flex_raw.xml"


async def main() -> None:
    tok = os.getenv("IBKR_FLEX_TOKEN")
    qid = os.getenv("IBKR_FLEX_QUERY_ID")
    if not tok or not qid or tok.endswith("REPLACE") or qid.endswith("REPLACE"):
        print(
            "Set IBKR_FLEX_TOKEN + IBKR_FLEX_QUERY_ID in backend/.env first "
            "(Account Mgmt → Reporting → Flex Web Service)."
        )
        sys.exit(1)

    print("1) SendRequest …")
    ref, url = await ib._send_request(tok, qid)
    print(f"   ReferenceCode: {ref}")
    print(f"   GetStatement URL: {url}")

    print("2) GetStatement (polls on 1019 'in progress') …")
    raw = await ib._get_statement(url, tok, ref)
    with open(_RAW_DUMP, "w") as fh:
        fh.write(raw)
    print(f"   raw XML -> {_RAW_DUMP}  ({len(raw)} bytes)")

    print("3) parse_flex_statement():")
    snap = ib.parse_flex_statement(raw)
    print(json.dumps(snap, indent=2, default=str))

    # Quick sanity callouts so gaps are obvious without reading the whole dump.
    if not snap["positions"]:
        print("\n⚠ no positions parsed — open /tmp/ibkr_flex_raw.xml and check the OpenPosition tag/attrs")
    elif not any("day_pnl" in p for p in snap["positions"]):
        print("\n⚠ positions parsed but no day_pnl — check MTMPerformanceSummaryUnderlying mtmPnl attr")
    if not snap["nav"]:
        print("\n⚠ no NAV parsed — check EquitySummary*/ChangeInNAV tags")


if __name__ == "__main__":
    asyncio.run(main())
