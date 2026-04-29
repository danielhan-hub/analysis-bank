"""CSV registry of feature scores keyed by analysis_id.

Header is derived from feature_dict.md (the canonical source of feature names),
so adding/removing a feature only requires editing the dict — never the CSV
header by hand.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable

from analysis_bank.paths import FEATURE_DICT_PATH, FEATURES_CSV_PATH


_ID_COLUMN = "analysis_id"


def _csv_path() -> Path:
    """Resolve the CSV path at call time so test patches take effect.

    Tests monkeypatch the module-level ``FEATURES_CSV_PATH`` attribute to
    redirect writes to a tmp path; default-arg binding would freeze the path
    at function-def time and bypass the patch.
    """
    import analysis_bank.features.registry as _self
    return _self.FEATURES_CSV_PATH


def feature_columns() -> list[str]:
    """Parse feature_dict.md and return the 76 feature names in order.

    The dict is a markdown table; the first column is the feature name.
    Skips the header rows, the separator row, and any non-table lines.
    """
    text = FEATURE_DICT_PATH.read_text()
    columns: list[str] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if not first or first.lower() == "feature":
            continue
        # Skip the markdown table separator row (e.g. "---")
        if re.fullmatch(r"-+", first):
            continue
        # Feature names are snake_case identifiers
        if not re.fullmatch(r"[a-z][a-z0-9_]*", first):
            continue
        columns.append(first)
    return columns


def _ensure_csv_with_header(path: Path | None = None) -> None:
    p = path or _csv_path()
    if p.exists() and p.stat().st_size > 0:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([_ID_COLUMN, *feature_columns()])


def upsert_row(
    analysis_id: str,
    scores: dict[str, int],
    path: Path | None = None,
) -> None:
    """Insert or replace the row for analysis_id in the CSV.

    Replace-in-place: if a row with this analysis_id already exists, it is
    overwritten; the row order of other entries is preserved. Missing scores
    are written as empty strings (which `load_all` will skip).
    """
    p = path or _csv_path()
    _ensure_csv_with_header(p)
    cols = feature_columns()

    # Read existing rows
    with p.open(newline="") as f:
        reader = csv.DictReader(f)
        existing = list(reader)
        header = reader.fieldnames or [_ID_COLUMN, *cols]

    new_row = {_ID_COLUMN: analysis_id}
    for c in cols:
        if c in scores:
            new_row[c] = str(int(scores[c]))
        else:
            new_row[c] = ""

    replaced = False
    for i, row in enumerate(existing):
        if row.get(_ID_COLUMN) == analysis_id:
            existing[i] = new_row
            replaced = True
            break
    if not replaced:
        existing.append(new_row)

    with p.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in existing:
            writer.writerow(row)


def load_all(path: Path | None = None) -> dict[str, dict[str, int]]:
    """Return {analysis_id: {feature: int}} for all rows in the CSV.

    Empty/non-numeric cells are skipped (the feature is omitted from that
    row's dict, not defaulted to 0). Returns an empty dict if the CSV is
    missing or has only a header.
    """
    p = path or _csv_path()
    if not p.exists():
        return {}
    out: dict[str, dict[str, int]] = {}
    cols = feature_columns()
    with p.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            aid = row.get(_ID_COLUMN, "").strip()
            if not aid:
                continue
            scores: dict[str, int] = {}
            for c in cols:
                v = (row.get(c) or "").strip()
                if not v:
                    continue
                try:
                    scores[c] = int(v)
                except ValueError:
                    continue
            out[aid] = scores
    return out


def all_analysis_ids(path: Path | None = None) -> Iterable[str]:
    return load_all(path).keys()
