"""Shared fixtures for the analysis_bank test suite.

These tests cover the synchronous code paths of AnalysisBankReceiver
(submit, evaluate, discard) and the features module (registry CSV, retrieval).
The async ``evaluate()`` LLM call is exercised by patching out
``_evaluate_one`` with a fake — we never drive the real SDK in tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import analysis_bank.receiver as rcv_mod
import analysis_bank.features.registry as reg_mod
import analysis_bank.features.retrieval as ret_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fake_proc_sql(name: str = "fake_proc") -> str:
    """A minimal procedure with a SAMPLE CALL header so smoke could run."""
    return (
        f"-- USE ROLE FOO;\n"
        f"-- USE WAREHOUSE BAR;\n"
        f"-- SAMPLE CALL: CALL {name}(1, '2025-01-01');\n"
        f"CREATE OR REPLACE PROCEDURE {name}(:v_id INT, :v_d DATE) "
        f"RETURNS TABLE() LANGUAGE SQL AS $$ SELECT 1 $$;\n"
    )


def make_candidate(
    candidates_dir: Path,
    *,
    name: str,
    include_readme: bool = True,
    include_sql: bool = True,
    proc_content: str | None = None,
) -> Path:
    """Build a candidate folder under ``candidates_dir``.

    The new shape: candidate_dir/<analysis_id>/{procedure.sql, README.md} —
    no nested procedure subfolder, no INDEX_BASELINE/INDEX_PROPOSED.

    Returns the candidate dir.
    """
    cand = candidates_dir / name
    cand.mkdir(parents=True, exist_ok=True)
    if include_sql:
        (cand / "procedure.sql").write_text(proc_content or make_fake_proc_sql())
    if include_readme:
        (cand / "README.md").write_text(f"# {name}\n\nTest procedure.\n")
    return cand


def fake_scores(seed: int = 0) -> dict[str, int]:
    """Return a deterministic 76-feature score dict for testing."""
    from analysis_bank import feature_columns

    cols = feature_columns()
    # Cycle integers in [-5, 5] offset by seed so different seeds give
    # different vectors but every feature gets a value.
    return {c: ((i + seed) % 11) - 5 for i, c in enumerate(cols)}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_bank(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create a temp 'bank' with procedures/, candidates/, analysis_features.csv.

    Returns ``(tmp_root, features_csv, procedures_dir, candidates_dir)``.
    Patches the module-level constants in ``analysis_bank.receiver``,
    ``analysis_bank.features.registry``, and ``analysis_bank.features.retrieval``
    to point at the temp bank, and restores them on teardown so tests are
    isolated.
    """
    procs_dir = tmp_path / "procedures"
    cands_dir = tmp_path / "candidates"
    csv_path = tmp_path / "analysis_features.csv"
    procs_dir.mkdir()
    cands_dir.mkdir()

    original = (
        rcv_mod.PROCEDURES_DIR,
        rcv_mod.CANDIDATES_DIR,
        reg_mod.FEATURES_CSV_PATH,
        ret_mod.PROCEDURES_DIR,
    )
    rcv_mod.PROCEDURES_DIR = procs_dir
    rcv_mod.CANDIDATES_DIR = cands_dir
    reg_mod.FEATURES_CSV_PATH = csv_path
    ret_mod.PROCEDURES_DIR = procs_dir
    try:
        yield tmp_path, csv_path, procs_dir, cands_dir
    finally:
        (
            rcv_mod.PROCEDURES_DIR,
            rcv_mod.CANDIDATES_DIR,
            reg_mod.FEATURES_CSV_PATH,
            ret_mod.PROCEDURES_DIR,
        ) = original


@pytest.fixture
def candidate_factory(tmp_bank):
    """Convenience: make_candidate bound to the test's candidates dir."""
    _, _, _, cands = tmp_bank

    def _factory(**kwargs):
        return make_candidate(cands, **kwargs)

    return _factory


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    """Scratch directory outside the bank for staging source candidates."""
    out = tmp_path / "_outside_bank"
    out.mkdir()
    return out


@pytest.fixture
def fake_scorer(monkeypatch):
    """Patch the receiver's ``score`` import to return canned scores.

    Returns a list that the test can mutate to control which scores are
    returned on each call (FIFO; if exhausted, the last entry is reused).
    """
    queue: list[dict[str, int]] = [fake_scores(0)]

    async def _fake_score(readme_text: str, sql_text: str) -> dict[str, int]:
        if not queue:
            raise RuntimeError("fake_scorer queue is empty")
        return queue[0] if len(queue) == 1 else queue.pop(0)

    monkeypatch.setattr(rcv_mod, "score", _fake_score)
    return queue
