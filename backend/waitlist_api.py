"""Waitlist + IBKR connect HTTP surface (W4 — pivot track).

A small FastAPI `APIRouter` mounted by main.py (`app.include_router(...)`). All
user-facing — identity comes from the JWT (`resolve_auth`), and writes go through
the user's token so RLS enforces "you own your row" (see connections.py). The
**system** read (the W5 cron) is NOT here — it's a service-key function, never an
endpoint.

Routes:
  POST /api/ibkr/connect      store/replace the caller's IBKR connection (auth'd)
  GET  /api/ibkr/connection   the caller's connection, token-free (auth'd)
  POST /api/ibkr/opt-in       flip the caller's opt-in (in-app unsubscribe) (auth'd)
  POST /api/waitlist          join the email waitlist (no auth — pre-signup)
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import connections
import token_crypto
from auth import AuthCtx, resolve_auth

router = APIRouter(tags=["waitlist"])

_E164 = re.compile(r"^\+\d{6,15}$")
# Light email check — avoids the email-validator dep that pydantic's EmailStr pulls.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConnectRequest(BaseModel):
    flex_token: str = Field(..., min_length=8, max_length=512)
    flex_query_id: str = Field(..., min_length=1, max_length=64)
    whatsapp_number: str = Field(..., max_length=24)  # E.164, e.g. +85291234567
    opt_in: bool = True
    email_opt_in: bool = False  # 037 — separate consent from WhatsApp; default off


class OptInRequest(BaseModel):
    opt_in: bool


class WaitlistRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    source: str | None = Field(default=None, max_length=64)


def _require_user(auth: AuthCtx) -> str:
    """The connect surface needs a real signed-in user (a JWT) — RLS keys on it.
    Demo mode (no token) can't own a per-user row, so reject rather than write a
    phantom-owned connection."""
    if auth.token is None:
        raise HTTPException(status_code=401, detail="sign in to manage your IBKR connection")
    return auth.token


@router.post("/api/ibkr/connect")
async def connect_ibkr(req: ConnectRequest, auth: AuthCtx = Depends(resolve_auth)) -> dict:
    user_jwt = _require_user(auth)
    if not _E164.match(req.whatsapp_number.strip()):
        raise HTTPException(status_code=422, detail="whatsapp_number must be E.164 (e.g. +85291234567)")
    if not token_crypto.token_crypto_configured():
        # Don't store an unencryptable token — fail loud (operator misconfig).
        raise HTTPException(status_code=503, detail="token encryption not configured")
    row = await connections.upsert_my_connection(
        user_jwt,
        auth.user_id,
        flex_token=req.flex_token.strip(),
        flex_query_id=req.flex_query_id.strip(),
        whatsapp_number=req.whatsapp_number.strip(),
        opt_in=req.opt_in,
        email_opt_in=req.email_opt_in,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="could not store connection")
    return {"ok": True, "connection": row}  # row is token-free (public view)


@router.get("/api/ibkr/connection")
async def get_ibkr_connection(auth: AuthCtx = Depends(resolve_auth)) -> dict:
    user_jwt = _require_user(auth)
    return {"connection": await connections.get_my_connection(user_jwt)}


@router.post("/api/ibkr/opt-in")
async def set_ibkr_opt_in(req: OptInRequest, auth: AuthCtx = Depends(resolve_auth)) -> dict:
    user_jwt = _require_user(auth)
    row = await connections.set_opt_in(user_jwt, auth.user_id, req.opt_in)
    if row is None:
        raise HTTPException(status_code=404, detail="no connection to update")
    return {"ok": True, "connection": row}


@router.post("/api/waitlist")
async def join_waitlist(req: WaitlistRequest) -> dict:
    # No auth — this precedes signup. RLS allows insert-only (no harvest).
    if not _EMAIL.match(req.email.strip()):
        raise HTTPException(status_code=422, detail="invalid email")
    ok = await connections.add_waitlist_signup(req.email, source=req.source)
    return {"ok": ok}
