"""CSV registry that maps each promoted procedure to its chart_eligible flag.

Pre-RAG this file held the 76-feature Olympics rubric. The semantic
pipeline replaces all of that — the only durable signal the registry now
carries is the chart-contract bit that retrieval needs to gate REUSE
candidates under existing_analysis_only=True. Embeddings live next to
each procedure on disk; keyword counts live in keyword_matrix.csv.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from analysis_bank.paths import FEATURES_CSV_PATH


_ID_COLUMN = "analysis_id"
_CHART_ELIGIBLE_COLUMN = "chart_eligible"
_HEADER = [_ID_COLUMN, _CHART_ELIGIBLE_COLUMN]


def _csv_path() -> Path:
    """Resolve the CSV path at call time so test patches take effect."""
    import analysis_bank.features.registry as _self
    return _self.FEATURES_CSV_PATH


def _ensure_csv_with_header(path: Path | None = None) -> None:
    p = path or _csv_path()
    if p.exists() and p.stat().st_size > 0:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)


def upsert_chart_eligible(
    analysis_id: str,
    chart_eligible: bool,
    path: Path | None = None,
) -> None:
    """Insert or replace one row in the registry CSV.

    chart_eligible drives retrieval's REUSE filter: only procedures that
    ship a real callable chart.py are eligible for REUSE under
    existing_analysis_only=True. The receiver's chart contract gate is
    the source of truth; this file just persists the bit so retrieval
    doesn't re-stat the bundle.
    """
    p = path or _csv_path()
    _ensure_csv_with_header(p)

    with p.open(newline="") as f:
        reader = csv.DictReader(f)
        existing = list(reader)

    new_row = {
        _ID_COLUMN: analysis_id,
        _CHART_ELIGIBLE_COLUMN: "true" if chart_eligible else "false",
    }

    replaced = False
    for i, row in enumerate(existing):
        if row.get(_ID_COLUMN) == analysis_id:
            existing[i] = new_row
            replaced = True
            break
    if not replaced:
        existing.append(new_row)

    with p.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_HEADER)
        writer.writeheader()
        for row in existing:
            writer.writerow(row)


def load_chart_eligibility(path: Path | None = None) -> dict[str, bool]:
    """Return {analysis_id: chart_eligible_bool} for every row in the CSV."""
    p = path or _csv_path()
    if not p.exists():
        return {}
    out: dict[str, bool] = {}
    with p.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            aid = (row.get(_ID_COLUMN) or "").strip()
            if not aid:
                continue
            v = (row.get(_CHART_ELIGIBLE_COLUMN) or "").strip().lower()
            out[aid] = v in ("true", "1", "yes", "y", "t")
    return out


def all_analysis_ids(path: Path | None = None) -> Iterable[str]:
    return load_chart_eligibility(path).keys()
