"""Live probe for the W2 briefing generator — the first-real-run tool.

Builds a full briefing end-to-end and prints it exactly as it would be sent over
WhatsApp. Honours the same env switches as the app:

  • USE_MOCK_IBKR=1      → use the bundled Flex fixture (default in .env.example)
                          unset + real IBKR_FLEX_TOKEN/QUERY_ID → pull a live statement
  • USE_MOCK_BRIEFING=1  → render the deterministic template (no LLM, no spend)
                          unset + ANTHROPIC_API_KEY set → write the brief with Claude
  • USE_MOCK_MARKET=1    → deterministic macro/news context

Examples:
    # fully offline, no spend — fixture + template:
    USE_MOCK_IBKR=1 USE_MOCK_BRIEFING=1 USE_MOCK_MARKET=1 \\
        backend/.venv/bin/python scripts/briefing_probe.py

    # real Claude narrative over the mock fixture (cheap, tests the prompt):
    USE_MOCK_IBKR=1 backend/.venv/bin/python scripts/briefing_probe.py

    # the real thing — live IBKR statement + Claude (set the Flex creds in backend/.env):
    backend/.venv/bin/python scripts/briefing_probe.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# Co-located backend (proposal copy pre-apply, repo copy post-apply) wins for
# `import briefing`; the repo backend supplies ibkr_flex + tools + .env.
_COLOCATED_BACKEND = _HERE.parents[1] / "backend"
_repo_backend = next(
    (up / "backend" for up in _HERE.parents if (up / "backend" / "ibkr_flex.py").exists()),
    None,
)
if _repo_backend is not None:
    sys.path.insert(0, str(_repo_backend))
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_backend / ".env")
    except Exception:
        pass
sys.path.insert(0, str(_COLOCATED_BACKEND))

import briefing as br  # noqa: E402


async def main() -> None:
    print("Modes: "
          f"IBKR={'mock' if os.getenv('USE_MOCK_IBKR') == '1' else 'live'}  "
          f"market={'mock' if os.getenv('USE_MOCK_MARKET') == '1' else 'live'}  "
          f"briefing={'mock(template)' if br.briefing_mock_enabled() else 'live(Claude)'}")
    try:
        out = await br.build_briefing()
    except br.BriefingError as e:
        print(f"\nBriefing generation failed [{e.code}]: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 — includes IBKRFlexError on a bad live token
        print(f"\nFailed: {type(e).__name__}: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("FACTS (computed — the numbers the brief must copy):")
    print(json.dumps(out["facts"], ensure_ascii=False, indent=2))
    print("=" * 60)
    print(f"account={out['account_id']}  as_of={out['as_of']}  "
          f"base={out['base_currency']}  model={out['model']}  is_mock={out['is_mock']}")
    print("-" * 60)
    print("WHATSAPP MESSAGE BODY:\n")
    print(out["text"])
    print("\n" + "-" * 60)
    print(f"length: {len(out['text'])} chars (budget {out['facts']['max_chars']})")


if __name__ == "__main__":
    asyncio.run(main())
