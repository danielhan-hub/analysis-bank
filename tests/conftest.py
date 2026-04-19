"""Shared fixtures for the analysis_bank test suite.

These tests cover the synchronous code paths of AnalysisBankReceiver
(submit, apply, discard, classify, guards) and the REVISE/REJECT verdict
file lifecycle. The async ``evaluate()`` LLM call is exercised by patching
out ``_evaluate_candidate`` with a fake that returns a canned verdict
string — we never drive the SDK in tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import analysis_bank.receiver as rcv_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_baseline_index(n_lines: int = 50) -> str:
    """A reasonable INDEX.md with N lines."""
    rows = "\n".join([f"| {i:02d} | proc_{i} | desc {i} |" for i in range(1, 11)])
    body_lines = [f"line content {i}" for i in range(n_lines)]
    return (
        "# Analysis Bank — INDEX\n\n"
        "## Routing Table\n"
        "| NN | Name | Description |\n"
        "|----|------|-------------|\n"
        f"{rows}\n\n"
        + "\n".join(body_lines)
    )


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
    proc_subfolder: str,
    proposed_text: str | None = None,
    baseline_text: str | None = None,
    include_readme: bool = True,
    include_sql: bool = True,
    proc_content: str | None = None,
    extra_files: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Build a candidate folder under ``candidates_dir``.

    Returns ``(candidate_dir, procedure_subfolder_dir)``.
    """
    cand = candidates_dir / name
    cand.mkdir(parents=True, exist_ok=True)
    (cand / "INDEX_BASELINE.md").write_text(baseline_text or make_baseline_index(60))
    (cand / "INDEX_PROPOSED.md").write_text(proposed_text or make_baseline_index(70))
    proc = cand / proc_subfolder
    proc.mkdir()
    if include_sql:
        (proc / "procedure.sql").write_text(proc_content or make_fake_proc_sql())
    if include_readme:
        (proc / "README.md").write_text("# Test proc\n")
    for fname, content in (extra_files or {}).items():
        (proc / fname).write_text(content)
    return cand, proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_bank(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Create a temp 'bank' with INDEX.md, procedures/, candidates/.

    Returns ``(tmp_root, index_path, procedures_dir, candidates_dir)``.
    Patches the module-level constants in ``analysis_bank.receiver`` to
    point at the temp bank, and restores them on teardown so tests are
    isolated.
    """
    procs_dir = tmp_path / "procedures"
    cands_dir = tmp_path / "candidates"
    index = tmp_path / "INDEX.md"
    procs_dir.mkdir()
    cands_dir.mkdir()
    index.write_text(make_baseline_index(60))

    # Save original module constants so we can restore them post-test.
    original = (rcv_mod.INDEX_PATH, rcv_mod.PROCEDURES_DIR, rcv_mod.CANDIDATES_DIR)
    rcv_mod.INDEX_PATH = index
    rcv_mod.PROCEDURES_DIR = procs_dir
    rcv_mod.CANDIDATES_DIR = cands_dir
    try:
        yield tmp_path, index, procs_dir, cands_dir
    finally:
        rcv_mod.INDEX_PATH, rcv_mod.PROCEDURES_DIR, rcv_mod.CANDIDATES_DIR = original


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
