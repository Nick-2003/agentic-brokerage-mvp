"""W5 — daily briefing scheduler (pivot track).

The job that ties the bridge together: once a day, for every opted-in connected
user, fetch holdings (W1) → write the narrative (W2) → deliver over WhatsApp (W3)
→ log the result (W4). It's pure orchestration over four already-verified pieces;
no new external integration.

A SYSTEM job, not an endpoint and not an agent tool (SECURITY threat 1 — the
LLM never sends). Run it from a cron / worker via `scripts/run_briefings.py`
(or `python -m scheduler`). Reads connections with the W4 service-key admin path.

Design guarantees:
  • **Per-user isolation** — one user's failure (IBKR down, Twilio reject, bad
    token) is caught + logged as a `failed` delivery; the run continues.
  • **Retries with backoff** — transient fetch/send errors retry per
    `BRIEFING_RETRY_DELAYS` (default `3,8` seconds; one + two retries).
  • **Cost ceiling** — at most `max_users` (`BRIEFING_MAX_USERS_PER_RUN`, default
    100) users per run; each user is exactly one Claude call, so this caps spend.
  • **dry-run** — build the brief (proves fetch+generate) but DON'T send or log,
    for safe pipeline checks. Combine with `USE_MOCK_*` for a zero-cost dry run.
  • **Skip, don't crash** — a connection whose token didn't decrypt (W4 flags it)
    is logged `skipped`, never fed to the pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import briefing
import connections
import published_briefs
import whatsapp

log = logging.getLogger(__name__)

# Retry backoff for a single user's build/send (seconds). Empty = no retry.
_RETRY_DELAYS = [int(x) for x in os.getenv("BRIEFING_RETRY_DELAYS", "3,8").split(",") if x.strip()]
# Cost ceiling: max users (≈ max Claude calls) per run.
_MAX_USERS = int(os.getenv("BRIEFING_MAX_USERS_PER_RUN", "100"))
# W6.3 — publish each brief to a web permalink and append the link to the message.
# Best-effort: a publish failure never blocks the send (we just send without a link).
_PUBLISH = os.getenv("PUBLISH_BRIEFS", "1") != "0"
# W6.5 — per-user cost-cap idempotency: don't re-send to a user who already got a
# brief within the last _MIN_RESEND_HOURS, so a cron misfire / retry / double-run
# can't double-bill (IBKR fetch + Claude call) or double-message them. The window
# sits between "a retry, minutes apart" (caught) and "the next daily run, ~24h
# later" (allowed) — 12h is a safe default for a once-daily cadence (incl. weekends,
# where the gap is larger still). Set BRIEFING_DEDUP=0 to disable; --force bypasses.
_DEDUP = os.getenv("BRIEFING_DEDUP", "1") != "0"
_MIN_RESEND_HOURS = float(os.getenv("BRIEFING_MIN_RESEND_HOURS", "12"))


async def _with_retries(factory, *, label: str):
    """Await `factory()`; on exception retry per `_RETRY_DELAYS`, then re-raise the last."""
    last: Exception | None = None
    for i in range(len(_RETRY_DELAYS) + 1):
        try:
            return await factory()
        except Exception as e:  # noqa: BLE001 — transient build/send errors
            last = e
            # Don't burn the backoff on a permanent failure (bad number, opted-out).
            if not getattr(e, "retryable", True):
                raise
            if i < len(_RETRY_DELAYS):
                log.info("retry %s in %ss after: %s", label, _RETRY_DELAYS[i], e)
                await asyncio.sleep(_RETRY_DELAYS[i])
    assert last is not None
    raise last


async def _safe_log(user_id: str, **kw: Any) -> None:
    """Write a delivery-log row; a logging failure must never abort the run."""
    try:
        await connections.log_delivery_admin(user_id, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("delivery-log write failed for %s: %s", user_id, e)


async def _process_one(c: dict, *, dry_run: bool) -> dict:
    """Build → (send → log) for one connection. Raises on unrecovered failure
    (the caller logs it as `failed`)."""
    uid, to = c["user_id"], c.get("whatsapp_number")
    brief = await _with_retries(
        lambda: briefing.build_briefing(c["flex_token"], c["flex_query_id"]),
        label=f"build:{uid}",
    )
    base = {"user_id": uid, "account_id": brief.get("account_id"), "as_of": brief.get("as_of")}
    if dry_run:
        return {**base, "status": "built", "chars": len(brief.get("text") or "")}

    # W6.3 — publish the brief to a web permalink (best-effort) + append the link
    # to the outgoing message. The STORED body is the original brief (no link line).
    text = brief.get("text") or ""
    permalink: str | None = None
    if _PUBLISH:
        try:
            pub = await published_briefs.publish_brief(
                uid, text, account_id=brief.get("account_id"), as_of=brief.get("as_of")
            )
            permalink = pub["permalink"]
        except Exception as e:  # noqa: BLE001 — never block the send on a publish failure
            log.warning("publish_brief failed for %s: %s", uid, e)
    send_brief = brief
    if permalink:
        send_brief = {**brief, "text": f"{text}\n\n📄 View this brief on the web: {permalink}"}

    send = await _with_retries(lambda: whatsapp.send_briefing(send_brief, to), label=f"send:{uid}")
    status = send.get("status") or "sent"
    await _safe_log(uid, status=status, account_id=brief.get("account_id"),
                    as_of=brief.get("as_of"), provider_id=send.get("sid"))
    return {**base, "status": status, "sid": send.get("sid"),
            "is_mock": send.get("is_mock"), "permalink": permalink}


async def _recently_delivered(user_id: str) -> bool:
    """W6.5 idempotency probe: did this user already get a brief within the resend
    window? **Fail-OPEN** — if the probe itself errors we let the send proceed (a
    missed brief is worse for the product than a rare duplicate) but log it."""
    since = (datetime.now(timezone.utc) - timedelta(hours=_MIN_RESEND_HOURS)).isoformat()
    try:
        return await connections.already_delivered_since_admin(user_id, since_iso=since)
    except Exception as e:  # noqa: BLE001 — never block a send on the dedup probe
        log.warning("dedup check failed for %s (sending anyway): %s", user_id, e)
        return False


async def run_daily_briefings(
    *, dry_run: bool = False, max_users: int | None = None, force: bool = False
) -> dict:
    """Run the daily briefing for every active opted-in connection.

    Returns a summary {total, capped, sent, built, skipped, failed, dry_run, results}.
    Never raises for a per-user failure (those are tallied + logged); only an
    inability to read the connection list propagates.

    `force=True` bypasses the W6.5 resend-window idempotency guard (for an
    intentional re-send / manual test).
    """
    conns = await connections.list_active_connections_admin()
    cap = max_users if max_users is not None else _MAX_USERS
    capped = len(conns) > cap
    conns = conns[:cap]

    summary = {"total": len(conns), "capped": capped, "dry_run": dry_run,
               "sent": 0, "built": 0, "skipped": 0, "failed": 0, "results": []}

    for c in conns:
        uid = c.get("user_id")
        # W4 flagged an undecryptable token (wrong/rotated key) → skip, don't crash.
        if not c.get("flex_token"):
            err = c.get("decrypt_error") or "no_token"
            if not dry_run:
                await _safe_log(uid, status="skipped", error=err)
            summary["skipped"] += 1
            summary["results"].append({"user_id": uid, "status": "skipped", "error": err})
            continue
        # W6.5 cost cap — already briefed within the resend window? Skip BEFORE
        # building, so a misfire/retry/double-run re-spends nothing (IBKR fetch +
        # Claude call) and doesn't double-message. Real runs only (dry-run is for
        # testing the build); --force bypasses the guard.
        if _DEDUP and not dry_run and not force and await _recently_delivered(uid):
            await _safe_log(uid, status="skipped", error="already_sent_recently")
            summary["skipped"] += 1
            summary["results"].append(
                {"user_id": uid, "status": "skipped", "error": "already_sent_recently"}
            )
            log.info("skip %s: already briefed within %sh", uid, _MIN_RESEND_HOURS)
            continue
        try:
            r = await _process_one(c, dry_run=dry_run)
            summary["built" if r["status"] == "built" else "sent"] += 1
            summary["results"].append(r)
        except Exception as e:  # noqa: BLE001 — isolate this user; keep going
            # Recipient opted out (texted STOP) → mirror it: flip opt_in off so
            # this is the last attempt, and count it as skipped (not a failure).
            if isinstance(e, whatsapp.WhatsAppError) and e.code == "whatsapp_recipient_opted_out":
                num = c.get("whatsapp_number")
                if num:
                    try:
                        await connections.set_opt_in_by_whatsapp(num, False)
                    except Exception as flip_err:  # noqa: BLE001
                        log.warning("opt-out flip failed for %s: %s", uid, flip_err)
                summary["skipped"] += 1
                summary["results"].append({"user_id": uid, "status": "opted_out"})
                if not dry_run:
                    await _safe_log(uid, status="skipped", error="recipient_opted_out")
                log.info("recipient opted out, opt_in flipped off: %s", uid)
                continue
            summary["failed"] += 1
            summary["results"].append({"user_id": uid, "status": "failed", "error": str(e)[:300]})
            if not dry_run:
                await _safe_log(uid, status="failed", error=str(e)[:300])
            log.warning("briefing failed for %s: %s", uid, e)

    log.info("daily briefings: %s", {k: summary[k] for k in
             ("total", "sent", "built", "skipped", "failed", "capped")})
    return summary


if __name__ == "__main__":  # `python -m scheduler` — convenience; scripts/run_briefings.py is richer
    import json
    print(json.dumps(asyncio.run(run_daily_briefings()), indent=2, default=str))
