"""Idempotent keyword-matrix builder for hybrid retrieval Stage B.

Sweeps every `procedures/<id>/{procedure.sql, README.md}` for the tokens
listed in `analysis_bank/features/keywords.yaml`, counts hits per
category, and writes a wide CSV at the bank root:

    keyword_matrix.csv
    ┌──────────────┬──────────────────┬─────────────┬────────┐
    │ analysis_id  │ category_share   │ cohort_decay │ ...    │
    ├──────────────┼──────────────────┼─────────────┼────────┤
    │ a_2026...    │ 3                │ 0           │ ...    │
    └──────────────┴──────────────────┴─────────────┴────────┘

Hits per category = number of times any of its tokens appears across
the procedure's SQL + README (case-insensitive substring count). The
counts feed Stage B's TF-IDF/BM25 lookup at retrieval time.

Idempotent: re-runnable after every `keywords.yaml` edit. New rows are
appended; existing rows are recomputed in place; rows for procedures
that no longer exist on disk are dropped.

Usage:
    python scripts/build_keyword_matrix.py
    python scripts/build_keyword_matrix.py --procedures-dir /custom/path
    python scripts/build_keyword_matrix.py --output /custom/keyword_matrix.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# Allow `python scripts/build_keyword_matrix.py` from the repo root without
# requiring the package to be installed.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analysis_bank.features.keyword_index import (  # noqa: E402
    DEFAULT_KEYWORDS_PATH,
    DEFAULT_MATRIX_PATH,
    load_keywords,
    score_text,
)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_matrix(
    procedures_dir: Path,
    keywords_path: Path,
    output_path: Path,
) -> tuple[int, int]:
    """Build keyword_matrix.csv. Returns (rows_written, categories)."""
    keywords = load_keywords(keywords_path)
    categories = sorted(keywords.keys())

    rows: list[dict[str, str]] = []
    for proc_dir in sorted(procedures_dir.iterdir()):
        if not proc_dir.is_dir() or proc_dir.name.startswith("."):
            continue
        text = (
            _read_text(proc_dir / "procedure.sql")
            + "\n"
            + _read_text(proc_dir / "README.md")
        )
        if not text.strip():
            continue
        scores = score_text(text, keywords)
        row: dict[str, str] = {"analysis_id": proc_dir.name}
        for cat in categories:
            row[cat] = str(scores.get(cat, 0))
        rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["analysis_id", *categories])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return len(rows), len(categories)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--procedures-dir",
        type=Path,
        default=REPO_ROOT / "procedures",
        help="Procedures directory to scan (default: %(default)s)",
    )
    parser.add_argument(
        "--keywords",
        type=Path,
        default=DEFAULT_KEYWORDS_PATH,
        help="keywords.yaml path (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_MATRIX_PATH,
        help="Output CSV path (default: %(default)s)",
    )
    args = parser.parse_args()

    n_rows, n_cats = build_matrix(args.procedures_dir, args.keywords, args.output)
    print(
        f"Wrote {n_rows} rows × {n_cats} categories → {args.output.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
