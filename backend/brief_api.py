"""Public brief-permalink endpoint (W6.3).

`GET /api/brief/{token}` — serves a published brief by its capability token. No
auth (the token IS the capability; the recipient opens it on their phone without
signing in), but the token is unguessable + the row expires, and a miss/expiry is
a flat 404 (no enumeration signal). The frontend `/b/[token]` page renders the JSON.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import published_briefs

router = APIRouter(tags=["brief"])


@router.get("/api/brief/{token}")
async def get_brief(token: str) -> dict:
    brief = await published_briefs.get_published_brief(token)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found or expired")
    return brief  # {text, account_id, as_of, generated_at}
