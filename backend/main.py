"""FastAPI entrypoint.

Exposes:
    GET  /healthz                       — liveness + config diagnostics
    POST /api/chat                      — Server-Sent Events stream of the agent loop
                                          Body: {"message": "user text",
                                                 "conversation_id"?: "uuid"}
                                          Identity: derived server-side from the Supabase JWT
                                          in the `Authorization: Bearer` header (P4.1).
                                          When REQUIRE_AUTH is off and no token is sent,
                                          falls back to "demo" — local mock demo path.
    GET  /api/conversations             — list this user's conversations (RLS-filtered)
    GET  /api/conversations/{id}        — fetch the messages in a conversation
    GET  /api/portfolio                 — total equity + today's P&L for the Hero header (P5/028)

P4.1 (012/015): identity comes from the verified JWT, never the request body.
P4.2 (016):     when authenticated, each turn is persisted to Supabase under the
                user's JWT (so RLS enforces). Demo mode persists nothing.
P4.4 (017):     each turn is wrapped in a Langfuse trace (`observability.trace_chat`)
                so the agent loop, tool calls, token usage, and latencies show up
                per-user in the Langfuse dashboard. No-op when unconfigured.
P4.3 (025):     each turn recalls this user's Mem0 memories (scoped by the
                authenticated `user_id`) and injects them into the system prompt;
                after the turn it stores the new salient facts. No-op when
                unconfigured. Scope is ALWAYS `auth.user_id` — never client input.
046:            conversation memory — for a persisted conversation, the prior
                turns are loaded (RLS-scoped) and seeded into the agent loop so
                it remembers earlier messages in the same chat. Distinct from
                P4.3 (cross-conversation user *facts*); this is in-thread
                history. Demo mode (no persistence) stays historyless.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# Load .env before importing anything that reads env vars
load_dotenv()

# Import agent AFTER load_dotenv so module-level env reads work
import brief_api  # noqa: E402  (W6.3 — public brief permalink GET /api/brief/{token})
import chart_api  # noqa: E402  (044 — public chart-data GET /api/chart-data for in-app charts)
import connections  # noqa: E402  (W4 — connect/waitlist storage diagnostics)
import db  # noqa: E402
import email_api  # noqa: E402  (038 — public email-unsubscribe endpoint)
import email_delivery  # noqa: E402  (038 — email channel diagnostics)
import email_unsubscribe  # noqa: E402  (038 — unsubscribe-token config diagnostics)
import memory  # noqa: E402
import observability  # noqa: E402
import security  # noqa: E402  (W6.6 — rate limit + security headers)
import token_budget  # noqa: E402  (P5 / 034 — per-user daily LLM token budget)
import waitlist_api  # noqa: E402  (W4 — IBKR connect + waitlist router)
import webhooks  # noqa: E402  (W6 — Twilio inbound STOP/START webhook)
from agent import MODEL, run_agent  # noqa: E402
from auth import AuthCtx, auth_configured, require_auth, resolve_auth  # noqa: E402
from tools import TOOL_REGISTRY  # noqa: E402
from tools.portfolio import get_portfolio  # noqa: E402

app = FastAPI(
    title="Agentic Brokerage MVP",
    description="Backend for the prompt-first brokerage prototype",
    version="0.1.0",
)

# CORS — frontend on localhost:3000 in dev. Production origins set via env.
# allow_headers=["*"] echoes the requested headers on preflight (incl.
# Authorization) even with allow_credentials=True, so the Bearer token works.
_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# W6.6 — rate limit (per-IP, guards /api/* except the Twilio webhooks + /healthz)
# + security headers. Pure-ASGI so it doesn't buffer/break the /api/chat SSE stream.
# Added after CORS → outermost: rejects abusive requests before the app, and adds
# headers on the way out without clobbering CORS's. No-ops with RATE_LIMIT_ENABLED=0.
app.add_middleware(security.SecurityMiddleware)

# W4 (pivot) — IBKR connect + waitlist routes (POST /api/ibkr/connect, etc.).
app.include_router(waitlist_api.router)
# W6 (pivot) — Twilio inbound STOP/START webhook (POST /api/twilio/inbound).
app.include_router(webhooks.router)
# W6.3 (pivot) — public brief permalink (GET /api/brief/{token}).
app.include_router(brief_api.router)
# 044 (chat) — public chart-data for the in-app lightweight-charts ta_chart.
app.include_router(chart_api.router)
# 038 (pivot) — public email unsubscribe (GET/POST /api/email/unsubscribe).
app.include_router(email_api.router)


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness probe — returns config diagnostics so we can spot mis-configured deploys."""
    return {
        "ok": True,
        "model": MODEL,
        "tools_registered": list(TOOL_REGISTRY.keys()),
        "alpaca_configured": bool(
            os.getenv("ALPACA_API_KEY", "").startswith("PK")
            and not os.getenv("ALPACA_API_KEY", "").endswith("REPLACE")
        ),
        "anthropic_key_present": bool(
            os.getenv("ANTHROPIC_API_KEY", "").startswith("sk-ant-")
            and not os.getenv("ANTHROPIC_API_KEY", "").endswith("REPLACE")
        ),
        # P4.1 auth diagnostics — spot a deploy that requires auth but has no secret.
        "require_auth": require_auth(),
        "auth_configured": auth_configured(),
        # P4.2 persistence diagnostics.
        "persistence_configured": db.persistence_configured(),
        # P4.4 observability diagnostics.
        "langfuse_configured": observability.langfuse_configured(),
        # P4.3 memory diagnostics.
        "memory_configured": memory.memory_configured(),
        # W4 connect/waitlist diagnostics (Supabase + Flex-token encryption ready).
        "connect_storage_configured": connections.connect_storage_configured(),
        # 038 email-channel diagnostics: real send needs BOTH Resend creds AND an
        # unsubscribe secret (no unsubscribe → no send). False here ⇒ email logs only.
        "email_configured": email_delivery.email_configured() and email_unsubscribe.configured(),
    }


# ---------------------------------------------------------------------------
# Chat — SSE stream
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    # P4.2: optional — when omitted, a new conversation is created and its id
    # is announced via the `conversation` SSE event. The frontend echoes it
    # back on the next turn to continue the thread.
    conversation_id: str | None = Field(default=None, max_length=64)
    # No `user_id` here — identity is derived from the JWT (see auth.resolve_auth).
    # Any client-supplied user_id is silently ignored (Pydantic drops extra fields).


def _title_from(message: str) -> str:
    """Use the first 60 chars of the opening message as the conversation title."""
    return message.strip()[:60] or None  # type: ignore[return-value]


@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    auth: AuthCtx = Depends(resolve_auth),
) -> EventSourceResponse:
    """Stream agent output as SSE; persist authenticated turns to Supabase;
    record one Langfuse trace per turn.

    SSE events: thought, tool_call, tool_result, widget, message, error, done.
    P4.2 adds: `conversation` (emitted once at the start with {id, title?}).
    Persistence and observability are both best-effort — a Supabase or Langfuse
    hiccup never breaks the user-facing stream.
    """

    # ---- pre-stream: ensure a conversation, load prior turns, write the user message ----
    conversation_id: str | None = None
    conversation_title: str | None = None
    # P046 — conversation memory: the prior turns of THIS conversation, as an
    # Anthropic-ready history list to seed run_agent with. Empty for a new
    # conversation and in demo mode (no token/persistence) → no behaviour change.
    agent_history: list[dict[str, str]] = []
    if auth.token and db.persistence_configured():
        try:
            conv = await db.get_or_create_conversation(
                auth.token,
                auth.user_id,
                conversation_id=req.conversation_id,
                title=_title_from(req.message) if not req.conversation_id else None,
            )
            if conv:
                conversation_id = conv["id"]
                conversation_title = conv.get("title")
                # P046: load the thread BEFORE writing the current message, so
                # history excludes it. RLS-scoped; bounded by db.to_agent_history;
                # best-effort — a hiccup just means this turn runs historyless.
                try:
                    prior = await db.get_conversation_messages(auth.token, conversation_id)
                    agent_history = db.to_agent_history(prior)
                except Exception:
                    agent_history = []
                await db.add_message(
                    auth.token,
                    conversation_id,
                    auth.user_id,
                    role="user",
                    content=req.message,
                )
        except Exception:
            # Don't break the stream on a DB hiccup; treat as if not persistable.
            conversation_id = None
            agent_history = []

    async def event_stream():
        # Open the Langfuse trace for this turn. When unconfigured this yields
        # a no-op tracer and is a zero-cost passthrough.
        async with observability.trace_chat(
            user_id=auth.user_id,
            conversation_id=conversation_id,
            message=req.message,
        ) as tracer:
            # Surface the conversation id ASAP so the frontend can capture it
            # before any agent error/timeout.
            if conversation_id:
                yield {
                    "event": "conversation",
                    "data": json.dumps(
                        {"id": conversation_id, "title": conversation_title},
                        ensure_ascii=False,
                    ),
                }

            text_acc: list[str] = []
            widget_acc: list[dict[str, Any]] = []

            # ---- P4.3: recall this user's memories (scoped by the trusted
            # `auth.user_id` — the SAME id the trace is tagged with) and inject
            # them into the agent's system prompt for this turn. No-op + ""
            # when Mem0 is unconfigured; failure-tolerant (a Mem0 hiccup injects
            # nothing and never breaks the turn).
            mem = memory.get_memory()
            try:
                memory_context = await mem.recall(user_id=auth.user_id, query=req.message)
            except Exception:
                memory_context = ""

            # ---- P5 (034): per-user daily token budget. Check BEFORE running the
            # agent — an already-capped user is refused this turn instead of
            # spending more. In-memory + fail-open (no-op when CHAT_DAILY_TOKEN_BUDGET
            # is unset; over_budget() never raises). Complements W6.6's per-IP rate
            # limit (frequency) with a per-user daily *cost* ceiling.
            if token_budget.over_budget(auth.user_id):
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": "Daily usage limit reached — please try again tomorrow.",
                         "code": "daily_token_budget_exceeded"}
                    ),
                }
                yield {"event": "done", "data": json.dumps({"elapsed_ms": 0, "iterations": 0})}
                return  # skip the agent, persistence, and memory store for this turn

            turn_input_tokens = 0
            turn_output_tokens = 0
            try:
                async for ev in run_agent(
                    req.message,
                    auth.user_id,
                    tracer=tracer,
                    memory_context=memory_context,
                    history=agent_history,
                ):
                    if ev["event"] == "widget":
                        widget_acc.append(ev["data"])
                    elif ev["event"] == "message":
                        text_acc.append(ev["data"].get("text", ""))
                    elif ev["event"] == "done":
                        turn_input_tokens = int(ev["data"].get("input_tokens") or 0)
                        turn_output_tokens = int(ev["data"].get("output_tokens") or 0)
                    yield {
                        "event": ev["event"],
                        "data": json.dumps(ev["data"], ensure_ascii=False),
                    }
            except Exception as e:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": f"stream failed: {e}"}),
                }

            # ---- P5 (034): record this turn's tokens against the daily budget
            # (in-memory; no-op when disabled). Best-effort — never breaks the stream.
            try:
                token_budget.record(auth.user_id, turn_input_tokens, turn_output_tokens)
            except Exception:
                pass

            # ---- post-stream: persist the assistant turn (best effort) ----
            if conversation_id and auth.token and (text_acc or widget_acc):
                try:
                    await db.add_message(
                        auth.token,
                        conversation_id,
                        auth.user_id,
                        role="assistant",
                        content="\n\n".join(t for t in text_acc if t) or None,
                        widgets=widget_acc or None,
                    )
                except Exception:
                    pass  # don't fail the stream on a DB hiccup

            # ---- P4.3: store the new salient facts from this exchange ----
            # Runs AFTER the agent's `done` event has already reached the client,
            # so Mem0's extraction latency is invisible to the user (same place
            # the persistence write sits). Scoped by `auth.user_id`; no-op when
            # Mem0 is unconfigured; never raises into the stream. We pass the
            # user message (always) plus the assistant's markdown text if any —
            # widgets are skipped (their JSON is noise for fact extraction; the
            # durable user facts live in the message). NB: unlike persistence,
            # this is NOT gated on `auth.token` — Mem0 is keyed by `user_id`, so
            # demo turns store under the shared "demo" bucket (local only;
            # production is REQUIRE_AUTH=1, so every user is a distinct UUID).
            try:
                await mem.remember(
                    user_id=auth.user_id,
                    user_message=req.message,
                    assistant_text="\n\n".join(t for t in text_acc if t) or None,
                )
            except Exception:
                pass  # memory is best-effort; never break the stream

    return EventSourceResponse(event_stream())


# ---------------------------------------------------------------------------
# Conversations — P4.2
# ---------------------------------------------------------------------------


@app.get("/api/conversations")
async def list_conversations_endpoint(
    auth: AuthCtx = Depends(resolve_auth),
) -> dict[str, Any]:
    """List this user's conversations, most-recently-active first.

    Demo mode → empty list (no persistence). RLS guarantees this only ever
    returns rows owned by the authenticated user.
    """
    if not auth.token or not db.persistence_configured():
        return {"conversations": []}
    return {"conversations": await db.list_conversations(auth.token)}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation_endpoint(
    conversation_id: str,
    auth: AuthCtx = Depends(resolve_auth),
) -> dict[str, Any]:
    """Return the ordered messages of a conversation owned by this user.

    A foreign or non-existent id returns an empty list under RLS — surfaced as
    a 404 so the frontend can distinguish "no such conversation" from "empty
    response shape." Demo mode also 404s.
    """
    if not auth.token or not db.persistence_configured():
        raise HTTPException(status_code=404, detail="not_found")
    msgs = await db.get_conversation_messages(auth.token, conversation_id)
    if not msgs:
        raise HTTPException(status_code=404, detail="not_found")
    return {"conversation_id": conversation_id, "messages": msgs}


# ---------------------------------------------------------------------------
# Portfolio summary — P5/028 (live Hero header)
# ---------------------------------------------------------------------------


@app.get("/api/portfolio")
async def portfolio_endpoint(
    auth: AuthCtx = Depends(resolve_auth),
) -> dict[str, Any]:
    """Lightweight portfolio summary for the frontend Hero header.

    Returns only the header fields (total equity + today's P&L) — NOT the full
    positions list — so the home screen header doesn't over-expose holdings on a
    non-chat endpoint. `get_portfolio` is the single source of truth and is
    itself mock-gated, so demo mode (no token) returns the mock total.
    """
    p = await get_portfolio({}, auth.user_id)
    return {
        "total_equity": p.get("total_equity"),
        "day_pnl": p.get("day_pnl"),
        "day_pnl_pct": p.get("day_pnl_pct"),
        "currency": p.get("currency", "$"),
        "is_mock": p.get("is_mock", True),
    }


# ---------------------------------------------------------------------------
# Dev entrypoint — `uv run python -m backend.main` also works
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
