#!/usr/bin/env python3
"""Vertex-Claude go/no-go probe (068, phase 0).

The Model Garden console reports a useless "Sorry, the service is not available at
the moment" for *every* failure mode. This calls the Vertex API directly and prints
the REAL status code + message, which is what actually distinguishes the causes:

    200  → it works. The console's Enable button was a red herring.
    404  → model not served in that region (try another region / model id).
    403  → not entitled: model not enabled for the project, OR the billing account
           is a free trial (third-party publishers like Anthropic aren't covered),
           OR Anthropic-on-Vertex isn't offered for your billing country.
           READ THE MESSAGE — it usually says which.
    429  → quota. You're through; just request a quota bump.
    401  → credentials/ADC problem, not a Vertex problem.

Scans several candidate regions so you don't have to guess which one serves the
model. Nothing is asserted about which regions are valid — the probe finds out.

Prereqs (the SDK's Vertex client needs google-auth, which is NOT installed yet):

    backend/.venv/bin/pip install "anthropic[vertex]"

Auth — either works:
  • Application Default Credentials:  gcloud auth application-default login
  • Or a service-account JSON inline:  export GCP_SA_JSON="$(cat sa.json)"

Usage:
    VERTEX_PROJECT_ID=quant-strategy-loop-data \
    backend/.venv/bin/python scripts/vertex_probe.py --model 'claude-opus-4-5@20251101'

    # don't know the exact model id? try a few:
    backend/.venv/bin/python scripts/vertex_probe.py \
        --model 'claude-opus-4-5@20251101' --model 'claude-sonnet-4@20250514'

Read-only. Each attempt is a 1-token request (negligible cost, and it only bills at
all if the call actually succeeds).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Vertex serves Claude only in some regions, and the set changes. These are just
# candidates to try — the probe reports what's actually true for YOUR project.
CANDIDATE_REGIONS = [
    "us-east5",
    "us-central1",
    "europe-west1",
    "europe-west4",
    "asia-southeast1",
    "global",
]

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def _credentials():
    """Service-account JSON from GCP_SA_JSON, else Application Default Credentials."""
    sa = os.getenv("GCP_SA_JSON")
    if not sa:
        return None  # let the SDK fall back to ADC
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(
        json.loads(sa), scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )


def _explain(status: int | None, msg: str) -> str:
    low = msg.lower()
    if status == 404:
        return "model not served in this region (or bad model id)"
    if status == 403:
        if "billing" in low or "trial" in low:
            return "NOT ENTITLED — looks billing/trial related. Upgrade to a full paid account."
        if "location" in low or "region" in low or "country" in low or "territor" in low:
            return "NOT ENTITLED — geographic restriction on Anthropic-on-Vertex."
        return "NOT ENTITLED — model not enabled for the project, or trial billing, or geo-restricted."
    if status == 429:
        return "QUOTA — you are entitled; just request a quota increase."
    if status == 401:
        return "credentials/ADC problem, not a Vertex entitlement problem"
    return "see message above"


async def probe_one(project: str, region: str, model: str) -> tuple[bool, str]:
    from anthropic import AsyncAnthropicVertex

    client = AsyncAnthropicVertex(
        project_id=project, region=region, credentials=_credentials()
    )
    try:
        await client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return True, "OK"
    except Exception as e:  # noqa: BLE001 — we want to SEE every failure shape
        status = getattr(e, "status_code", None)
        body = getattr(e, "body", None)
        msg = ""
        if isinstance(body, dict):
            err = body.get("error")
            msg = (err.get("message") if isinstance(err, dict) else str(err)) or ""
        msg = (msg or str(e)).strip()
        label = f"HTTP {status}" if status else type(e).__name__
        return False, f"{label}: {msg[:220]}\n        → {_explain(status, msg)}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.getenv("VERTEX_PROJECT_ID"))
    ap.add_argument("--model", action="append", default=[],
                    help="repeatable; Vertex ids usually carry an @version suffix")
    ap.add_argument("--region", action="append", default=[],
                    help="repeatable; defaults to a candidate scan")
    args = ap.parse_args()

    if not args.project:
        print("Set VERTEX_PROJECT_ID or pass --project"); return 2
    models = args.model or ([os.getenv("VERTEX_MODEL")] if os.getenv("VERTEX_MODEL") else [])
    if not models:
        print("Pass --model (e.g. 'claude-opus-4-5@20251101') or set VERTEX_MODEL"); return 2

    try:
        import anthropic  # noqa: F401
        import google.auth  # noqa: F401
    except ModuleNotFoundError as e:
        print(f"missing dep ({e.name}). Install:  backend/.venv/bin/pip install 'anthropic[vertex]'")
        return 2

    regions = args.region or CANDIDATE_REGIONS
    print(f"project={args.project}\n")
    any_ok = False
    for model in models:
        print(f"model: {model}")
        for region in regions:
            ok, detail = await probe_one(args.project, region, model)
            if ok:
                any_ok = True
                print(f"  {GREEN}[OK]{RESET}   {region:<18} → WORKS. Use this region.")
            else:
                color = YELLOW if detail.startswith("HTTP 404") else RED
                print(f"  {color}[fail]{RESET} {region:<18} {detail}")
        print()

    if any_ok:
        print(f"{GREEN}Vertex is usable.{RESET} Set VERTEX_PROJECT_ID / CLOUD_ML_REGION / VERTEX_MODEL "
              "to a combination marked OK, then wire the rail (068).")
        return 0
    print(f"{RED}No region/model worked.{RESET} If every failure is 403 with an entitlement or "
          "geographic message, Vertex is a dead end for this account — fall back to DeepSeek.\n"
          "If they're all 404, the model id is wrong: copy the exact id from Model Garden.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
