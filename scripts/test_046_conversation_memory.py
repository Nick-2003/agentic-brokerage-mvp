#!/usr/bin/env python3
"""Offline unit test for Proposal 046 — conversation memory.

Two halves, both offline (no Supabase, no Anthropic network):
  1. db.to_agent_history(...) — the pure rows→history converter: alternation
     safety, merging, widget-only placeholder, empty-skip, bounding.
  2. agent.run_agent(history=...) — seeds the Claude `messages` array with the
     history then the current user turn (Anthropic client stubbed).

Self-contained: temp-applies the proposal's db.py / main.py / agent.py over the
live files, imports, runs, then restores all three in a finally block.

Run with the backend venv:
    backend/.venv/bin/python .proposed_changes/046-chat-conversation-memory/scripts/test_046_conversation_memory.py

Exit code 0 = all pass, 1 = a check failed.
"""
import asyncio
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    """Walk up to the real repo root. Anchored on backend/auth.py — which is NOT
    part of this proposal's mirror tree (the mirror only carries backend/db.py,
    main.py, agent.py), so the search can't stop early inside the proposal."""
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError(f"could not locate repo root (backend/auth.py) above {start}")
        d = parent


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes", "046-chat-conversation-memory", "backend")
FILES = ("db.py", "main.py", "agent.py")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def _u(text):       return {"role": "user", "content": text, "widgets": None}
def _a(text=None, widgets=None): return {"role": "assistant", "content": text, "widgets": widgets}


def test_to_agent_history(db):
    print("\n=== db.to_agent_history — pure converter ===")
    th = db.to_agent_history

    check("empty rows → []", th([]) == [])

    out = th([_u("hi"), _a("hello")])
    check("simple alternating preserved",
          out == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
          str(out))

    out = th([_u("a"), _a("b"), _u("c")])
    check("trailing dangling user dropped",
          out == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
          str(out))

    out = th([_a("x"), _u("a"), _a("b")])
    check("leading assistant dropped",
          out == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
          str(out))

    out = th([_u("a"), _u("a2"), _a("b")])
    check("consecutive same-role merged",
          out == [{"role": "user", "content": "a\n\na2"}, {"role": "assistant", "content": "b"}],
          str(out))

    out = th([_u("show aapl"), _a(text=None, widgets=[{"type": "research_card"}])])
    check("widget-only assistant → placeholder (no JSON replay)",
          out[1]["content"] == "[Assistant showed a research_card widget]", str(out))

    out = th([_u("a"), _a(text="   "), _u("c"), _a("d")])
    check("empty assistant skipped then users merge",
          out == [{"role": "user", "content": "a\n\nc"}, {"role": "assistant", "content": "d"}],
          str(out))

    # message-count bound: keep most recent, still starts with user.
    rows = [_u("u1"), _a("a1"), _u("u2"), _a("a2"), _u("u3"), _a("a3")]
    out = th(rows, max_messages=2)
    check("max_messages keeps most recent pair",
          out == [{"role": "user", "content": "u3"}, {"role": "assistant", "content": "a3"}],
          str(out))
    check("history starts with user after bound", (not out) or out[0]["role"] == "user")

    # char bound: tiny budget keeps only the most recent, then trims to start-with-user.
    out = th([_u("x" * 50), _a("y" * 50), _u("z" * 50), _a("w" * 5)], max_chars=10)
    check("max_chars keeps ≥1 most-recent (trimmed to valid start)",
          out == [] or out[0]["role"] == "user", str(out))

    check("max_messages<=0 disables (→[])", th(rows, max_messages=0) == [])
    check("max_chars<=0 disables (→[])", th(rows, max_chars=0) == [])

    # env-driven defaults
    os.environ["CHAT_HISTORY_MAX_MESSAGES"] = "2"
    out = th(rows)
    check("env CHAT_HISTORY_MAX_MESSAGES respected", len(out) == 2, str(out))
    os.environ.pop("CHAT_HISTORY_MAX_MESSAGES")


# --- stubbed Anthropic client for run_agent -------------------------------

class _FakeStream:
    def __init__(self, msg): self._msg = msg
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get_final_message(self): return self._msg


class _FakeMessages:
    def __init__(self, rec): self.rec = rec
    def stream(self, **kwargs):
        # Record a SNAPSHOT (copy) of the messages the agent built for the first
        # Anthropic call — run_agent mutates the same list afterwards (appends the
        # assistant turn), so we must copy to capture the call-time state.
        self.rec.setdefault("messages", list(kwargs.get("messages") or []))
        block = types.SimpleNamespace(type="text", text="ok")  # terminal, no tool_use
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        return _FakeStream(types.SimpleNamespace(content=[block], usage=usage))


class _FakeClient:
    def __init__(self, rec): self.messages = _FakeMessages(rec)


async def _drive(agent, history):
    rec: dict = {}
    agent._client = _FakeClient(rec)  # _get_client() returns this; no network
    async for _ in agent.run_agent("current question", "demo", history=history):
        pass
    return rec["messages"]


def test_run_agent_seeding(agent, db):
    print("\n=== agent.run_agent — seeds history then the current user turn ===")

    msgs = asyncio.run(_drive(agent, None))
    check("history=None → single current user message (no regression)",
          msgs == [{"role": "user", "content": "current question"}], str(msgs))

    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    msgs = asyncio.run(_drive(agent, hist))
    check("history seeded BEFORE current user turn",
          msgs == hist + [{"role": "user", "content": "current question"}], str(msgs))

    # caller's history list isn't mutated by the in-loop appends
    hist2 = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    _ = asyncio.run(_drive(agent, hist2))
    check("caller history list not mutated", len(hist2) == 2, str(hist2))

    # end-to-end: db.to_agent_history output is directly usable as history
    built = db.to_agent_history([_u("earlier q"), _a("earlier a")])
    msgs = asyncio.run(_drive(agent, built))
    check("to_agent_history output usable as run_agent history",
          msgs == built + [{"role": "user", "content": "current question"}], str(msgs))


def main() -> int:
    backups: dict[str, bytes] = {}
    try:
        for f in FILES:
            live = os.path.join(BACKEND, f)
            prop = os.path.join(PROP, f)
            if os.path.isfile(prop) and os.path.abspath(prop) != os.path.abspath(live):
                with open(live, "rb") as fh:
                    backups[live] = fh.read()
                shutil.copyfile(prop, live)
        sys.path.insert(0, BACKEND)
        import db  # noqa: E402
        import agent  # noqa: E402
        test_to_agent_history(db)
        test_run_agent_seeding(agent, db)
    finally:
        for live, data in backups.items():
            with open(live, "wb") as fh:
                fh.write(data)  # restore — never leave the live files modified

    total, passed = len(results), sum(results)
    print(f"\n{'='*48}\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
