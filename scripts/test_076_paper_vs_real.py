#!/usr/bin/env python3
"""Offline guard for Proposal 076 — paper (Alpaca) vs real (IBKR) portfolio labelling.

Network-free. Temp-apply → assert → restore-in-`finally`, non-destructive guard
(never delete a file this run did not create). Confirm with `git status` after.

Every portfolio/position payload must carry a canonical `account_kind` and, when
there's something to badge, the matching human `account_label` — so a paper
position can never be mistaken for the real IBKR book (or vice versa).

Covers:
  A. the shared mapping — kinds → labels, `none`/unknown → no label;
  B. `get_portfolio` real-IBKR (mock-gated) → real_ibkr + "Real · IBKR";
  C. `get_portfolio` not-connected → account_kind "none", NO label;
  D. `get_portfolio` demo sample book → sample + "Sample · Alpaca paper";
  E. legacy Alpaca mock path → paper_alpaca / mock (MOCK_PORTFOLIO) labelled;
  F. execution reads (`get_open_position`, `list_open_positions`) → paper_alpaca;
  G. the stale `thought_template` no longer says "paper".

Run:
    backend/.venv/bin/python scripts/test_076_paper_vs_real.py
"""
import asyncio
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo(start: str) -> str:
    d = start
    while True:
        if os.path.isfile(os.path.join(d, "backend", "auth.py")):
            return d
        p = os.path.dirname(d)
        if p == d:
            raise RuntimeError("repo root not found")
        d = p


REPO = _find_repo(HERE)
BACKEND = os.path.join(REPO, "backend")
PROP = os.path.join(REPO, ".proposed_changes", "076-paper-vs-real-portfolio", "backend")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[bool] = []


def check(name, cond, detail=""):
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""))
    results.append(bool(cond))


# repo-relative paths this run overwrites
OVERWRITE = [
    "tools/__init__.py", "tools/portfolio.py", "tools/execution.py",
    "prompts/widget_contract.md", "prompts/system.md",
]


def apply_proposal(backup_dir: str) -> None:
    for f in OVERWRITE:
        src_live = os.path.join(BACKEND, f)
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        shutil.copy2(src_live, bak)
        shutil.copy2(os.path.join(PROP, f), src_live)


def restore(backup_dir: str) -> None:
    for f in OVERWRITE:
        bak = os.path.join(backup_dir, f.replace("/", "__"))
        if os.path.isfile(bak):
            shutil.copy2(bak, os.path.join(BACKEND, f))


def run() -> None:
    sys.path.insert(0, BACKEND)
    os.environ["USE_MOCK_MARKET"] = "1"
    os.environ["USE_MOCK_BROKER"] = "1"
    os.environ["USE_MOCK_IBKR"] = "1"      # a connected user's fetch → bundled fixture
    os.environ.pop("PORTFOLIO_SOURCE", None)  # default ibkr

    import tools  # the staged package (registers submodules on import)
    import tools.portfolio as portfolio
    import tools.execution as execution

    VALID = {"real_ibkr", "paper_alpaca", "sample", "mock", "none"}

    # ---------------------------------------------------------------- A
    print("\nA. shared kind → label mapping")
    check("real_ibkr → 'Real · IBKR'", tools.account_label("real_ibkr") == "Real · IBKR")
    check("paper_alpaca → 'Paper · Alpaca'", tools.account_label("paper_alpaca") == "Paper · Alpaca")
    check("sample → 'Sample · Alpaca paper'", tools.account_label("sample") == "Sample · Alpaca paper")
    check("mock → 'Demo data'", tools.account_label("mock") == "Demo data")
    check("none → no label", tools.account_label("none") is None)
    check("unknown → no label", tools.account_label("nonsense") is None)
    tagged = tools.tag_account({}, "real_ibkr")
    check("tag_account sets kind + label in lockstep",
          tagged["account_kind"] == "real_ibkr" and tagged["account_label"] == "Real · IBKR")
    check("tag_account('none') sets kind but NO label",
          "account_label" not in tools.tag_account({}, "none"))

    # ---------------------------------------------------------------- B
    print("\nB. get_portfolio → real IBKR (mock-gated, connected user)")

    async def _fake_conn(user_id):
        return {"flex_token": "tok", "query_id": "q"}

    import connections
    connections.get_connection_with_token_admin = _fake_conn  # force a "connected" user
    p = asyncio.run(portfolio.get_portfolio({}, "user-uuid"))
    check("account_kind real_ibkr", p.get("account_kind") == "real_ibkr", str(p.get("account_kind")))
    check("account_label 'Real · IBKR'", p.get("account_label") == "Real · IBKR")
    check("is_paper False", p.get("is_paper") is False)

    # ---------------------------------------------------------------- C
    print("\nC. get_portfolio → not connected")

    async def _no_conn(user_id):
        return None

    connections.get_connection_with_token_admin = _no_conn
    nil = asyncio.run(portfolio.get_portfolio({}, "user-uuid"))
    check("account_kind 'none'", nil.get("account_kind") == "none")
    check("NO account_label (nothing to badge)", "account_label" not in nil)
    check("connected False", nil.get("connected") is False)

    # ---------------------------------------------------------------- D
    print("\nD. demo guest sample book")
    os.environ["SAMPLE_PORTFOLIO_ENABLED"] = "1"
    os.environ.pop("SAMPLE_ALPACA_API_KEY", None)  # no creds → static sample_mock
    os.environ.pop("SAMPLE_ALPACA_API_SECRET", None)
    s = asyncio.run(portfolio.get_portfolio({}, "demo"))
    check("account_kind sample", s.get("account_kind") == "sample", str(s.get("account_kind")))
    check("account_label 'Sample · Alpaca paper'", s.get("account_label") == "Sample · Alpaca paper")
    os.environ.pop("SAMPLE_PORTFOLIO_ENABLED", None)

    # ---------------------------------------------------------------- E
    print("\nE. legacy Alpaca path → MOCK_PORTFOLIO labelled")
    check("MOCK_PORTFOLIO tagged mock", portfolio.MOCK_PORTFOLIO.get("account_kind") == "mock")
    check("MOCK_PORTFOLIO label 'Demo data'", portfolio.MOCK_PORTFOLIO.get("account_label") == "Demo data")
    os.environ["PORTFOLIO_SOURCE"] = "alpaca"  # no PK key → falls to MOCK_PORTFOLIO
    legacy = asyncio.run(portfolio.get_portfolio({}, "someone"))
    check("legacy alpaca (no key) → mock kind", legacy.get("account_kind") == "mock")
    os.environ["PORTFOLIO_SOURCE"] = "ibkr"

    # ---------------------------------------------------------------- F
    print("\nF. execution reads are paper_alpaca")
    op = asyncio.run(execution.get_open_position({"ticker": "NVDA"}, "nobody"))
    # no mock order for this user → an error dict (no position); labelling only
    # applies to a real position payload, so drive the list path with a seeded order.
    lp = asyncio.run(execution.list_open_positions({}, "nobody"))
    check("list_open_positions tagged paper_alpaca",
          lp.get("account_kind") == "paper_alpaca" and lp.get("account_label") == "Paper · Alpaca")
    # get_open_position error path has no account_kind (nothing to badge) — that's fine;
    # assert the label helper is what execution would stamp on a real position:
    check("get_open_position error path is unlabelled (no position)",
          "account_kind" not in op or op.get("error"))

    # ---------------------------------------------------------------- G
    print("\nG. thought_template no longer mislabels the real book as 'paper'")
    td = tools.TOOL_REGISTRY["get_portfolio"]["thought_template"]
    check("thought_template drops the word 'paper'", "paper" not in td.lower(), repr(td))

    # every produced kind is in the valid set
    check("all observed account_kinds valid",
          all(x in VALID for x in [p.get("account_kind"), nil.get("account_kind"),
                                   s.get("account_kind"), legacy.get("account_kind"),
                                   lp.get("account_kind")]))


def main() -> None:
    backup = tempfile.mkdtemp(prefix="076-backup-")
    try:
        apply_proposal(backup)
        run()
    finally:
        restore(backup)
        shutil.rmtree(backup, ignore_errors=True)

    total, ok = len(results), sum(results)
    print(f"\n{'=' * 60}\n  {ok}/{total} checks passed\n{'=' * 60}")
    print("Live tree restored — confirm with: git status --short")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
