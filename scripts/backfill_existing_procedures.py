#!/usr/bin/env python3
"""Backfill the analysis_features.csv from the existing 11 procedures.

For each ``procedures/NN_<name>/``:
  1. Mint a new analysis_id ``a_<YYYYMMDD>_<6hex>``
  2. Invoke the 5-jury scorer over ``README.md`` + ``procedure.sql``
  3. Append the row to ``analysis_features.csv``
  4. ``git mv`` the folder to ``procedures/<analysis_id>/``

Then delete ``INDEX.md``.

This is a one-shot migration. It expects to be run from the analysis_bank
repo root with the live ``ads_ms_env`` Python (claude-agent-sdk available).

Usage:
    python scripts/backfill_existing_procedures.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import subprocess
import sys
from datetime import date
from pathlib import Path

from analysis_bank import (
    FEATURES_CSV_PATH,
    PROCEDURES_DIR,
    feature_columns,
    score,
    upsert_row,
)


REPO_ROOT = PROCEDURES_DIR.parent
INDEX_PATH = REPO_ROOT / "INDEX.md"


def mint_analysis_id() -> str:
    return f"a_{date.today():%Y%m%d}_{secrets.token_hex(3)}"


def existing_procedure_dirs() -> list[Path]:
    """Return procedures/NN_<name>/ folders, sorted by NN."""
    if not PROCEDURES_DIR.exists():
        return []
    return sorted(
        d for d in PROCEDURES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


async def score_one(proc_dir: Path) -> dict[str, int]:
    readme = (proc_dir / "README.md").read_text(encoding="utf-8")
    sql = (proc_dir / "procedure.sql").read_text(encoding="utf-8")
    print(f"  scoring {proc_dir.name} (5-jury)...", flush=True)
    return await score(readme, sql)


def git_mv(src: Path, dst: Path) -> None:
    """Use git mv so history follows; fall back to plain rename if not in git."""
    try:
        subprocess.run(
            ["git", "mv", str(src), str(dst)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        src.rename(dst)


def git_rm(p: Path) -> None:
    try:
        subprocess.run(
            ["git", "rm", str(p)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        if p.exists():
            p.unlink()


async def main(dry_run: bool = False) -> int:
    procs = existing_procedure_dirs()
    if not procs:
        print("No procedures to backfill.", file=sys.stderr)
        return 1

    print(f"Backfilling {len(procs)} procedure(s)...")
    print(f"  feature dictionary: {len(feature_columns())} features")
    print(f"  CSV: {FEATURES_CSV_PATH}")
    print(f"  procedures: {PROCEDURES_DIR}\n")

    summary: list[tuple[str, str, int]] = []  # (old_name, new_id, n_scores)

    for old_dir in procs:
        analysis_id = mint_analysis_id()
        if dry_run:
            print(f"  [dry-run] {old_dir.name}  ->  {analysis_id}")
            summary.append((old_dir.name, analysis_id, 0))
            continue

        scores = await score_one(old_dir)
        upsert_row(analysis_id, scores)
        new_dir = PROCEDURES_DIR / analysis_id
        git_mv(old_dir, new_dir)
        print(f"    {old_dir.name}  ->  {analysis_id}  ({len(scores)}/76 features)")
        summary.append((old_dir.name, analysis_id, len(scores)))

    # Delete INDEX.md (the routing index is dead — retrieval is now feature-vector)
    if INDEX_PATH.exists():
        if dry_run:
            print(f"\n  [dry-run] would delete {INDEX_PATH}")
        else:
            git_rm(INDEX_PATH)
            print(f"\nDeleted {INDEX_PATH}")

    print("\n=== Summary ===")
    print(f"{'old name':<35} {'new analysis_id':<25} {'features':>10}")
    print("-" * 73)
    for old, new, n in summary:
        print(f"{old:<35} {new:<25} {n:>10}")
    print(f"\nTotal: {len(summary)} procedures backfilled.")
    if dry_run:
        print("(dry-run — no files changed)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without scoring or renaming anything.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
