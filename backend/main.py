"""FastAPI entrypoint.

Exposes:
    GET  /healthz        — liveness check
    POST /api/chat       — Server-Sent Events stream of the agent loop
                           Body: {"message": "user text", "user_id": "optional"}

For Phase 1 there is no auth. Auth (Supabase JWT) is wired in Phase 7.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# Load .env before importing anything that reads env vars
load_dotenv()

# Import agent AFTER load_dotenv so module-level env reads work
from agent import MODEL, run_agent  # noqa: E402
from tools import TOOL_REGISTRY  # noqa: E402

app = FastAPI(
    title="Agentic Brokerage MVP",
    description="Backend for the prompt-first brokerage prototype",
    version="0.1.0",
)

# CORS — frontend on localhost:3000 in dev. Production origins set via env.
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
    }


# ---------------------------------------------------------------------------
# Chat — SSE stream
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    user_id: str = Field("demo", min_length=1, max_length=128)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    """Stream agent output as SSE.

    Each event is JSON in `data:` per the SSE spec. The `event:` field uses
    the names declared in agent.run_agent (thought, tool_call, tool_result,
    widget, message, error, done).
    """

    async def event_stream():
        try:
            async for ev in run_agent(req.message, req.user_id):
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
