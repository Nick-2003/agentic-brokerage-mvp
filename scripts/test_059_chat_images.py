#!/usr/bin/env python3
"""Offline guard for Proposal 059 — image/file attachments (vision input).

Covers the deterministic, network-free surface of 059:

  A. `agent._build_user_content` — the pure seam that turns (message, attachments)
     into the Anthropic user-turn `content`:
       - no attachments        → the plain string (unchanged pre-059 behaviour)
       - text + image(s)        → [text block, image block, …] in order
       - image-only (no text)   → [image block, …] with NO leading text block
       - each image → {"type":"image","source":{"type":"base64",media_type,data}}
  B. `main.ChatRequest` / `main.Attachment` validation (the trust boundary):
       - media_type allowlist; count cap; total-size cap
       - empty message allowed IFF ≥1 attachment; rejected otherwise
  C. `system.md` carries the "Interpreting images" section + the trust nuance
     (image-read numbers are NOT tool outputs / never a `sources` pill).

Self-contained: temp-applies the proposal's backend/{agent.py,main.py,
prompts/system.md} over the live files, imports, asserts, restores in a finally.
Anchored on backend/auth.py (NOT part of 059's mirror) so the repo-root walk
can't stop inside the proposal.

Run with the backend venv:
    backend/.venv/bin/python scripts/test_059_chat_images.py

Exit code 0 = all pass, 1 = a check failed.
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
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
PROP = os.path.join(REPO, ".proposed_changes", "059-chat-image-file-attachments")

# (live target, proposal source) for each file we temp-apply.
FILES = [
    (os.path.join(BACKEND, "agent.py"), os.path.join(PROP, "backend", "agent.py")),
    (os.path.join(BACKEND, "main.py"), os.path.join(PROP, "backend", "main.py")),
    (
        os.path.join(BACKEND, "prompts", "system.md"),
        os.path.join(PROP, "backend", "prompts", "system.md"),
    ),
]

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


def test_build_user_content() -> None:
    print("\n=== A. agent._build_user_content ===")
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    from agent import _build_user_content  # noqa: E402 — after temp-apply

    # no attachments → plain string
    out = _build_user_content("hello", None)
    check("no attachments → str passthrough", out == "hello", repr(out))
    check("empty attachment list → str passthrough", _build_user_content("hi", []) == "hi")

    # text + two images
    atts = [
        {"media_type": "image/jpeg", "data": "AAA1", "name": "a.jpg"},
        {"media_type": "image/png", "data": "BBB2", "name": "b.png"},
    ]
    out = _build_user_content("read these", atts)
    ok_list = isinstance(out, list) and len(out) == 3
    check("text + 2 images → 3 blocks", ok_list, f"n={len(out) if isinstance(out, list) else 'not list'}")
    if ok_list:
        check("block[0] is the text", out[0] == {"type": "text", "text": "read these"}, repr(out[0]))
        img = out[1]
        check("block[1] is an image block", img.get("type") == "image")
        src = img.get("source", {})
        check("image source is base64", src.get("type") == "base64", str(src.get("type")))
        check("media_type threaded", src.get("media_type") == "image/jpeg", str(src.get("media_type")))
        check("data threaded", src.get("data") == "AAA1", str(src.get("data")))
        check("order preserved (2nd image is png)", out[2]["source"]["media_type"] == "image/png")

    # image-only (no text) → NO leading text block
    out = _build_user_content("", atts)
    img_only_ok = isinstance(out, list) and len(out) == 2 and all(b["type"] == "image" for b in out)
    check("image-only → only image blocks, no text block", img_only_ok,
          f"types={[b.get('type') for b in out] if isinstance(out, list) else out}")


def test_request_models() -> None:
    print("\n=== B. main.ChatRequest / Attachment validation ===")
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    from pydantic import ValidationError  # noqa: E402
    from main import Attachment, ChatRequest  # noqa: E402 — after temp-apply

    def rejects(fn, label: str) -> None:
        try:
            fn()
            check(label, False, "expected ValidationError, got none")
        except ValidationError:
            check(label, True)

    # Attachment media_type allowlist
    rejects(lambda: Attachment(media_type="image/tiff", data="AA"),
            "Attachment rejects disallowed media_type")
    try:
        Attachment(media_type="image/png", data="AA")
        check("Attachment accepts image/png", True)
    except ValidationError as e:
        check("Attachment accepts image/png", False, str(e))

    # ChatRequest: text-only ok
    try:
        ChatRequest(message="hi")
        check("text-only message ok", True)
    except ValidationError as e:
        check("text-only message ok", False, str(e))

    # empty message + no attachments → rejected
    rejects(lambda: ChatRequest(message="  "), "empty message + no attachments rejected")

    # empty message + one attachment → ok (image-only turn)
    try:
        ChatRequest(message="", attachments=[Attachment(media_type="image/jpeg", data="AA")])
        check("image-only message ok", True)
    except ValidationError as e:
        check("image-only message ok", False, str(e))

    # too many attachments → rejected (cap is 4)
    five = [Attachment(media_type="image/jpeg", data="AA") for _ in range(5)]
    rejects(lambda: ChatRequest(message="x", attachments=five), "more than 4 attachments rejected")

    # total size over cap → rejected (3 × 5,000,000 = 15,000,000 > 14,000,000)
    big = [Attachment(media_type="image/jpeg", data="A" * 5_000_000) for _ in range(3)]
    rejects(lambda: ChatRequest(message="x", attachments=big), "total attachment size over cap rejected")


def test_system_prompt() -> None:
    print("\n=== C. system.md vision section + trust nuance ===")
    txt = open(os.path.join(BACKEND, "prompts", "system.md"), encoding="utf-8").read()
    check("has 'Interpreting images the user uploads' section",
          "Interpreting images the user uploads" in txt)
    check("says an image-read number is NOT a tool output",
          "NOT a tool output" in txt or "not a tool output" in txt)
    check("forbids an image-read number becoming a sources pill",
          "sources" in txt and "image-read" in txt)


def main() -> int:
    backups: list[tuple[str, str]] = []
    try:
        for live, prop in FILES:
            if not os.path.isfile(prop):
                print(f"missing proposal file: {prop}")
                return 1
            bak = live + ".059bak"
            shutil.copy2(live, bak)
            backups.append((live, bak))
            shutil.copy2(prop, live)

        test_build_user_content()
        test_request_models()
        test_system_prompt()
    finally:
        for live, bak in backups:
            shutil.copy2(bak, live)
            os.remove(bak)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
