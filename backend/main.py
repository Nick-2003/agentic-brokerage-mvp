"""FastAPI entrypoint.

Exposes:
    GET  /healthz        — liveness check
    POST /api/chat       — Server-Sent Events stream of the agent loop
                           Body: {"message": "user text"}
                           Identity: derived server-side from the Supabase JWT
                           in the `Authorization: Bearer` header (P4.1). When
                           REQUIRE_AUTH is off and no token is sent, falls back
                           to the "demo" user so local mock demos keep working.

Auth (P4.1): see backend/auth.py. The old client-supplied `user_id` body field
is gone — identity now comes from the verified token, never the request body.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# Load .env before importing anything that reads env vars
load_dotenv()

# Import agent AFTER load_dotenv so module-level env reads work
from agent import MODEL, run_agent  # noqa: E402
from auth import auth_configured, require_auth, resolve_user_id  # noqa: E402
from tools import TOOL_REGISTRY  # noqa: E402

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
    }


# ---------------------------------------------------------------------------
# Chat — SSE stream
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    # No `user_id` here — identity is derived from the JWT (see auth.resolve_user_id).
    # Any client-supplied user_id is silently ignored (Pydantic drops extra fields).


@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    user_id: str = Depends(resolve_user_id),
) -> EventSourceResponse:
    """Stream agent output as SSE.

    `user_id` is resolved by the auth dependency (verified JWT, or "demo" when
    REQUIRE_AUTH is off and no token is sent). An invalid token or a missing
    token under REQUIRE_AUTH raises 401 before this body runs.

    Each event is JSON in `data:` per the SSE spec. The `event:` field uses
    the names declared in agent.run_agent (thought, tool_call, tool_result,
    widget, message, error, done).
    """

    async def event_stream():
        try:
            async for ev in run_agent(req.message, user_id):
                yield {
                    "event": ev["event"],
                    "data": json.dumps(ev["data"], ensure_ascii=False),
                }
        except Exception as e:
            yield {
                "event": "error",
                "data": json.dumps({"message": f"stream failed: {e}"}),
            }

    return EventSourceResponse(event_stream())


# ---------------------------------------------------------------------------
# Dev entrypoint — `uv run python -m backend.main` also works
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
