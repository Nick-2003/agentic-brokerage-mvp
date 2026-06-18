"""MCP client adapter.

Hosts long-lived stdio MCP `ClientSession`s for external MCP servers we proxy
through our existing tool registry. Today: TradingView (chart manipulation).
Tomorrow: TrueNorth (research data).

Design choices (see .proposed_changes/002-tradingview-mcp/README.md §1, §4.1):

1. **Anthropic SDK stays the primary agent transport.** This module is a client
   that backend tools call into — not a replacement for the agent loop. Mirrors
   the pattern set by docs/TRUENORTH_MCP_INTEGRATION.md §3.

2. **Lazy init.** First chart-related agent turn spawns the subprocess and
   performs the MCP handshake. Subsequent turns reuse the same session. Avoids
   paying ~1-2s of node-boot latency per request.

3. **Single asyncio.Lock per server session.** Chrome DevTools Protocol is
   single-controller: two simultaneous `chart_set_symbol` calls against one
   TradingView Desktop instance would clobber each other's state. Lock
   serialises every `tv_call`. No timeout — chart turns are ~2-3s and we'd
   rather queue than abandon mid-mutation.

4. **No silent fall-through.** Real-path failures surface as
   `tradingview_mcp_unreachable` / `tradingview_mcp_call_failed` errors so the
   operator knows real is broken vs not configured. Same discipline as
   `alpaca_fetch_failed` in execution.py.

5. **Crash recovery.** Detect dead subprocess on read EOF → close session → next
   call lazily re-spawns. Limit consecutive re-spawn failures so we don't spin.

Env vars (backend/.env / .env.example):
    USE_MOCK_TA              = "1" forces mock path even when MCP is configured
    TRADINGVIEW_MCP_COMMAND  = "node"
    TRADINGVIEW_MCP_ARGS     = "/abs/path/to/tradingview-mcp/src/server.js"
                               (single absolute path — passed verbatim, NOT
                                split on whitespace, so paths with spaces /
                                en-dashes / quotes work without escaping)
    TRADINGVIEW_MCP_CDP_PORT = "9222"  (passed to the server via env)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

# The official Python MCP SDK. Install via pyproject.toml main deps.
# from mcp import ClientSession                       # type: ignore[import-untyped]
# from mcp.client.stdio import StdioServerParameters  # type: ignore[import-untyped]
# from mcp.client.stdio import stdio_client           # type: ignore[import-untyped]
#
# Imports are deferred to _ensure_session() so the rest of the backend doesn't
# fail to boot if `mcp` isn't installed yet (e.g. fresh clone before uv sync).


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors — surfaced to the agent through tool returns. Mirrors the
# `{"error": "alpaca_fetch_failed", "message": str(e)}` shape that execution.py
# uses.
# ---------------------------------------------------------------------------


class MCPClientError(Exception):
    """Base error for the MCP client adapter."""

    code = "mcp_client_error"


class MCPUnreachable(MCPClientError):
    """MCP server / subprocess unreachable (down, refused, crashed)."""

    code = "tradingview_mcp_unreachable"


class MCPCallFailed(MCPClientError):
    """Server reachable but the specific tool call failed (MCP-level error)."""

    code = "tradingview_mcp_call_failed"


class MCPCDPRefused(MCPClientError):
    """TV Desktop CDP port refused — Desktop probably not running with --remote-debugging-port."""

    code = "tradingview_cdp_refused"


# ---------------------------------------------------------------------------
# Session config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ServerConfig:
    """Configuration for one MCP server we host as a subprocess."""

    name: str                  # logical name, e.g. "tradingview"
    command: str               # executable (e.g. "node")
    args: list[str]            # CLI args (e.g. ["/path/to/server.js"])
    env: dict[str, str]        # extra env vars to pass to the subprocess
    err_unreachable: type[MCPClientError]  # error class to raise on unreachable


def _tradingview_config() -> _ServerConfig | None:
    """Build TradingView server config from env, or return None if not configured."""
    command = os.getenv("TRADINGVIEW_MCP_COMMAND")
    args = os.getenv("TRADINGVIEW_MCP_ARGS")
    if not command or not args:
        return None
    cdp_port = os.getenv("TRADINGVIEW_MCP_CDP_PORT", "9222")
    # Pass TRADINGVIEW_MCP_ARGS as a SINGLE argument — do NOT split on whitespace.
    # The previous implementation did `args.split()`, which broke any path with
    # spaces (e.g. macOS "Documents – SNG058/Work/…"): Node would receive only
    # the first whitespace-delimited fragment and crash with
    # `MODULE_NOT_FOUND: Cannot find module '/Users/.../Documents'`. Proposal 021.
    #
    # If a multi-arg invocation is ever needed, introduce a separate
    # `TRADINGVIEW_MCP_EXTRA_ARGS` (parsed with `shlex.split` so quoting works)
    # rather than overloading this var.
    return _ServerConfig(
        name="tradingview",
        command=command,
        args=[args],
        env={"TV_CDP_PORT": cdp_port},
        err_unreachable=MCPUnreachable,
    )


# ---------------------------------------------------------------------------
# Session manager — one instance per server name, shared across all callers.
# ---------------------------------------------------------------------------


class _ServerSession:
    """One long-lived stdio MCP session against a single subprocess.

    Thread-safety: every `call()` acquires `self._lock` (one per session).
    CDP is single-controller so we serialise every chart action — see the
    module docstring.
    """

    # Maximum consecutive re-spawn failures before surfacing unreachable
    _MAX_RESPAWNS = 3
    # Backoff between re-spawn attempts (exponential, capped)
    _BACKOFF_BASE_S = 0.5
    _BACKOFF_MAX_S = 4.0

    def __init__(self, config: _ServerConfig) -> None:
        self.config = config
        self._session: Any | None = None   # mcp.ClientSession
        self._stack: Any | None = None     # AsyncExitStack for stdio_client + ClientSession
        self._lock = asyncio.Lock()
        self._respawn_failures = 0

    async def _ensure_session(self) -> Any:
        """Spawn the subprocess and perform the MCP handshake if needed."""
        if self._session is not None:
            return self._session

        # Deferred import — don't fail backend boot if `mcp` package missing.
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as e:
            raise MCPUnreachable(
                f"`mcp` package not installed. Run `uv sync` in backend/. ({e})"
            ) from e

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env={**os.environ, **self.config.env},
        )

        backoff = self._BACKOFF_BASE_S
        for attempt in range(self._MAX_RESPAWNS):
            try:
                stack = AsyncExitStack()
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
                self._stack = stack
                self._respawn_failures = 0
                log.info("MCP session up: %s (attempt %d)", self.config.name, attempt + 1)
                return session
            except FileNotFoundError as e:
                # `command` not on PATH — permanent failure, don't retry
                raise self.config.err_unreachable(
                    f"{self.config.name} MCP command not found: {self.config.command} ({e})"
                ) from e
            except Exception as e:
                log.warning(
                    "MCP %s spawn attempt %d/%d failed: %s",
                    self.config.name, attempt + 1, self._MAX_RESPAWNS, e,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._BACKOFF_MAX_S)

        self._respawn_failures += 1
        raise self.config.err_unreachable(
            f"{self.config.name} MCP unreachable after {self._MAX_RESPAWNS} attempts"
        )

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool. Serialised via the per-session lock.

        Returns the tool's result dict (whatever the MCP server emits).
        Raises MCPUnreachable / MCPCallFailed on failure.
        """
        wait_start = time.monotonic()
        async with self._lock:
            wait_ms = int((time.monotonic() - wait_start) * 1000)
            if wait_ms > 200:
                # Cheap observability for queue depth. Set DEBUG to see all.
                log.info("MCP %s lock wait: %d ms (tool=%s)", self.config.name, wait_ms, tool)

            session = await self._ensure_session()
            try:
                result = await session.call_tool(tool, args)
            except Exception as e:
                # Connection died mid-call — close the session and surface
                # the error. Next call lazily re-spawns.
                await self._close_locked()
                raise MCPCallFailed(f"{self.config.name}.{tool} failed: {e}") from e

            # The MCP SDK returns a CallToolResult with `.content` (list of blocks).
            # For tools that return JSON, the first text block is the payload.
            return _extract_call_result(result)

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as e:
                log.warning("MCP %s close error: %s", self.config.name, e)
            self._stack = None
            self._session = None

    async def health(self) -> bool:
        """Probe — returns True iff the session can be ensured. Cheap."""
        try:
            await self._ensure_session()
            return True
        except MCPClientError:
            return False


def _extract_call_result(result: Any) -> dict[str, Any]:
    """Pull a JSON-ish dict out of an MCP CallToolResult.

    MCP results are a list of content blocks; tools that return structured data
    typically put a single JSON-stringified text block first. We try JSON-parse
    first, fall back to returning the raw text under a `text` key.
    """
    import json

    content = getattr(result, "content", None) or []
    if not content:
        return {}
    block = content[0]
    text = getattr(block, "text", None)
    if text is None:
        # Could be a non-text block (e.g. image). Return a minimal envelope.
        return {"_raw": str(block)}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}
    except json.JSONDecodeError:
        return {"text": text}


# ---------------------------------------------------------------------------
# Singletons — one session per logical server.
# ---------------------------------------------------------------------------


_sessions: dict[str, _ServerSession] = {}


def _get_or_create(name: str) -> _ServerSession:
    if name in _sessions:
        return _sessions[name]
    if name == "tradingview":
        cfg = _tradingview_config()
        if cfg is None:
            raise MCPUnreachable(
                "TradingView MCP not configured. Set TRADINGVIEW_MCP_COMMAND and "
                "TRADINGVIEW_MCP_ARGS in backend/.env. See README.md "
                "'Talk-to-your-charts' for setup."
            )
        sess = _ServerSession(cfg)
    else:
        raise MCPClientError(f"Unknown MCP server: {name}")
    _sessions[name] = sess
    return sess


# ---------------------------------------------------------------------------
# Public API — what `backend/tools/technicals.py` actually imports.
# ---------------------------------------------------------------------------


async def tv_call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a TradingView MCP tool. See module docstring for error semantics."""
    return await _get_or_create("tradingview").call(tool, args)


async def tv_health() -> bool:
    """Cheap reachability probe for the TradingView MCP server."""
    try:
        return await _get_or_create("tradingview").health()
    except MCPClientError:
        return False


async def tv_close() -> None:
    """Tear down the TradingView session. Idempotent."""
    if "tradingview" in _sessions:
        await _sessions["tradingview"].close()


async def close_all() -> None:
    """Tear down every session. Register this with FastAPI's shutdown event
    (in `main.py`) so Ctrl-C doesn't leave zombie subprocesses."""
    for sess in list(_sessions.values()):
        await sess.close()


@asynccontextmanager
async def tv_session():
    """Async context manager handing back the raw session for advanced callers.

    Most tools should use `tv_call()`; this is for the rare case where you want
    to keep the lock for multiple consecutive calls (e.g. mutate chart + read
    state without another caller interleaving).
    """
    sess = _get_or_create("tradingview")
    async with sess._lock:  # noqa: SLF001 — intentional access for the contract above
        yield await sess._ensure_session()  # noqa: SLF001
