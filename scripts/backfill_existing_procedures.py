#!/usr/bin/env python3
"""Backfill embeddings + chart_eligible rows for every promoted procedure.

Idempotent maintenance script. For each ``procedures/<analysis_id>/`` it:

  1. Ensures ``questions.json`` exists (fails noisily if missing — fix
     by hand or re-run promotion).
  2. Encodes the paraphrases via BGE and writes ``embeddings.npy`` if
     the file is absent or older than ``questions.json``.
  3. Upserts ``analysis_features.csv`` with the current chart_eligible
     bit derived from disk (``chart.py`` present == True).

After running it also rebuilds ``keyword_matrix.csv`` so Stage B
matches the post-backfill bank.

Usage:
    python scripts/backfill_existing_procedures.py [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from analysis_bank import PROCEDURES_DIR, upsert_chart_eligible
from analysis_bank.features.embeddings import (
    EMBEDDINGS_FILENAME,
    compute_and_persist,
)


def existing_procedure_dirs() -> list[Path]:
    if not PROCEDURES_DIR.exists():
        return []
    return sorted(
        d for d in PROCEDURES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def needs_reembed(proc_dir: Path) -> bool:
    qjson = proc_dir / "questions.json"
    emb = proc_dir / EMBEDDINGS_FILENAME
    if not emb.exists():
        return True
    return qjson.stat().st_mtime > emb.stat().st_mtime


def rebuild_keyword_matrix() -> None:
    from analysis_bank.features.keyword_index import (
        DEFAULT_KEYWORDS_PATH,
        DEFAULT_MATRIX_PATH,
    )
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_keyword_matrix import build_matrix  # type: ignore
    n_rows, n_cats = build_matrix(
        PROCEDURES_DIR, DEFAULT_KEYWORDS_PATH, DEFAULT_MATRIX_PATH
    )
    print(f"Rebuilt keyword_matrix.csv ({n_rows} rows × {n_cats} categories)")


def main(dry_run: bool, force: bool) -> int:
    procs = existing_procedure_dirs()
    if not procs:
        print("No procedures to backfill.", file=sys.stderr)
        return 1

    print(f"Backfilling {len(procs)} procedure(s) at {PROCEDURES_DIR}")
    for proc in procs:
        qjson = proc / "questions.json"
        if not qjson.exists():
            print(f"  ! {proc.name}: missing questions.json — skipping")
            continue

        chart_eligible = (proc / "chart.py").exists()
        if dry_run:
            action = "re-encode" if (force or needs_reembed(proc)) else "(cached)"
            print(f"  [dry-run] {proc.name}: chart_eligible={chart_eligible} ({action})")
            continue

        if force or needs_reembed(proc):
            compute_and_persist(proc)
            print(f"  embedded {proc.name}")
        upsert_chart_eligible(proc.name, chart_eligible)

    if not dry_run:
        rebuild_keyword_matrix()

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without writing anything.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-encode every procedure even if embeddings.npy is current.",
    )
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run, force=args.force))
