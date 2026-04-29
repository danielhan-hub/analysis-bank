"""Tests for the features module: registry CSV + retrieval math.

Scorer.py is not exercised here — it's a thin LLM driver and the contract
(asyncio.gather → drop top+bottom → average middle) is too dependent on the
agent's actual JSON output for unit-test value. Integration testing of the
scorer is covered by the backfill verification step.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from analysis_bank import feature_columns
from analysis_bank.features.registry import upsert_row, load_all
from analysis_bank.features.retrieval import nearest

from .conftest import fake_scores


# ---------------------------------------------------------------------------
# feature_columns
# ---------------------------------------------------------------------------


def test_feature_columns_has_76():
    assert len(feature_columns()) == 76


def test_feature_columns_all_snake_case():
    import re

    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for c in feature_columns():
        assert pattern.match(c), f"non-snake-case feature: {c}"


def test_feature_columns_does_not_include_analysis_id():
    assert "analysis_id" not in feature_columns()


# ---------------------------------------------------------------------------
# upsert_row + load_all
# ---------------------------------------------------------------------------


def test_upsert_creates_csv_with_header(tmp_bank):
    _, csv_path, _, _ = tmp_bank
    assert not csv_path.exists()
    upsert_row("a_20260101_aaa111", fake_scores(0))
    assert csv_path.exists()
    with csv_path.open() as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header[0] == "analysis_id"
    assert header[1:] == feature_columns()


def test_upsert_appends_new_row(tmp_bank):
    upsert_row("a_20260101_aaa111", fake_scores(0))
    upsert_row("a_20260102_bbb222", fake_scores(1))
    rows = load_all()
    assert set(rows.keys()) == {"a_20260101_aaa111", "a_20260102_bbb222"}


def test_upsert_replaces_existing_row(tmp_bank):
    """Re-scoring an analysis_id replaces the old row in place."""
    _, csv_path, _, _ = tmp_bank
    upsert_row("a_20260101_aaa111", fake_scores(0))
    upsert_row("a_20260102_bbb222", fake_scores(1))
    upsert_row("a_20260103_ccc333", fake_scores(2))

    # Re-score the middle one with a different vector
    new_scores = fake_scores(7)
    upsert_row("a_20260102_bbb222", new_scores)

    rows = load_all()
    assert len(rows) == 3
    assert rows["a_20260102_bbb222"] == new_scores

    # Row order should be preserved (no duplicates)
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        ids = [r["analysis_id"] for r in reader]
    assert ids == [
        "a_20260101_aaa111",
        "a_20260102_bbb222",
        "a_20260103_ccc333",
    ]


def test_load_all_skips_blank_rows(tmp_bank):
    _, csv_path, _, _ = tmp_bank
    upsert_row("a_20260101_aaa111", fake_scores(0))
    # Manually append a blank-id row
    with csv_path.open("a") as f:
        f.write("," + ",".join("0" for _ in feature_columns()) + "\n")
    rows = load_all()
    assert "" not in rows
    assert "a_20260101_aaa111" in rows


def test_load_all_returns_empty_when_csv_missing(tmp_bank):
    assert load_all() == {}


# ---------------------------------------------------------------------------
# retrieval.nearest
# ---------------------------------------------------------------------------


def _seed_corpus(n: int) -> None:
    """Seed the CSV with `n` synthetic procedures, each with a unique vector."""
    for i in range(n):
        upsert_row(f"a_20260101_proc{i:03d}", fake_scores(i))


def test_nearest_empty_corpus_returns_empty(tmp_bank):
    assert nearest(fake_scores(0)) == []


def test_nearest_finds_self_at_distance_zero(tmp_bank):
    """An exact-vector match should be the #1 result with distance 0."""
    _seed_corpus(10)
    target = fake_scores(3)
    matches = nearest(target)
    assert matches[0].analysis_id == "a_20260101_proc003"
    assert matches[0].euclidean_dist == pytest.approx(0.0)
    assert matches[0].cosine_sim == pytest.approx(1.0)


def test_nearest_caps_at_max_per_strategy_with_small_corpus(tmp_bank):
    """With 10 procs and percentile=0.10 (top 1), Euclidean returns ≥1.

    Capped at max_per_strategy=3 → that's the upper bound for the
    Euclidean strategy. Cosine adds up to 3 more → ≤ 6 deduped total.
    """
    _seed_corpus(10)
    target = fake_scores(0)
    matches = nearest(target, max_per_strategy=3, euclidean_percentile=0.10)
    assert 1 <= len(matches) <= 6


def test_nearest_cosine_threshold_filters(tmp_bank):
    """min_cosine=2.0 (impossible) means cosine strategy contributes nothing.

    Result count should be ≤ Euclidean cap.
    """
    _seed_corpus(20)
    target = fake_scores(0)
    matches = nearest(target, max_per_strategy=3, min_cosine=2.0)
    # Only Euclidean strategy contributes when cosine threshold is impossible
    assert len(matches) <= 3


def test_nearest_dedup_across_strategies(tmp_bank):
    """A match that wins both strategies should appear exactly once."""
    _seed_corpus(10)
    target = fake_scores(3)  # exact match to proc003
    matches = nearest(target, max_per_strategy=3)
    ids = [m.analysis_id for m in matches]
    assert len(ids) == len(set(ids))


def test_nearest_match_carries_readme_path(tmp_bank):
    _, _, procs, _ = tmp_bank
    _seed_corpus(5)
    target = fake_scores(0)
    matches = nearest(target)
    assert matches[0].path_to_readme == procs / matches[0].analysis_id / "README.md"


def test_nearest_results_sorted_by_euclidean_ascending(tmp_bank):
    _seed_corpus(15)
    target = fake_scores(7)
    matches = nearest(target)
    assert matches == sorted(matches, key=lambda m: m.euclidean_dist)
