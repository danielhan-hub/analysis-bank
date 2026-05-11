"""Shared fixtures for the analysis_bank test suite.

These tests cover the synchronous code paths of AnalysisBankReceiver
(submit, evaluate, discard) and the features module (registry CSV,
embedding persistence, retrieval). The async ``evaluate()`` LLM call
is exercised by patching out ``_evaluate_one`` with a fake — we never
drive the real SDK in tests. The dense encoder is patched by
``fake_encoder`` so torch never has to load.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import analysis_bank.receiver as rcv_mod
import analysis_bank.features.embeddings as emb_mod
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
    include_chart_skipped: bool = True,
    include_questions: bool = True,
    proc_content: str | None = None,
) -> Path:
    """Build a candidate folder under ``candidates_dir``."""
    cand = candidates_dir / name
    cand.mkdir(parents=True, exist_ok=True)
    if include_sql:
        (cand / "procedure.sql").write_text(proc_content or make_fake_proc_sql())
    if include_readme:
        (cand / "README.md").write_text(f"# {name}\n\nTest procedure.\n")
    if include_chart_skipped:
        (cand / "chart_skipped.md").write_text(
            "single scalar — no chart needed for this test fixture."
        )
    if include_questions:
        (cand / "questions.json").write_text(
            json.dumps(
                {
                    "summary": f"Test fixture summary for {name}.",
                    "questions": [
                        f"test paraphrase {i} for {name}" for i in range(1, 9)
                    ],
                },
                indent=2,
            )
        )
    return cand


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
        rcv_mod.PROCEDURES_INDEX_PATH,
        reg_mod.FEATURES_CSV_PATH,
        ret_mod.PROCEDURES_DIR,
        emb_mod.PROCEDURES_DIR,
    )
    rcv_mod.PROCEDURES_DIR = procs_dir
    rcv_mod.CANDIDATES_DIR = cands_dir
    rcv_mod.PROCEDURES_INDEX_PATH = procs_dir / "_index.md"
    reg_mod.FEATURES_CSV_PATH = csv_path
    ret_mod.PROCEDURES_DIR = procs_dir
    emb_mod.PROCEDURES_DIR = procs_dir
    try:
        yield tmp_path, csv_path, procs_dir, cands_dir
    finally:
        (
            rcv_mod.PROCEDURES_DIR,
            rcv_mod.CANDIDATES_DIR,
            rcv_mod.PROCEDURES_INDEX_PATH,
            reg_mod.FEATURES_CSV_PATH,
            ret_mod.PROCEDURES_DIR,
            emb_mod.PROCEDURES_DIR,
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


@pytest.fixture(autouse=True)
def fake_encoder(monkeypatch):
    """Patch :func:`embeddings.encode` so tests never load BGE/torch.

    Returns a deterministic-but-distinct vector per input string by
    hashing it into 8-d float32. That's enough for the persistence
    contract tests (shape, file present, distinct rows) and avoids
    the multi-second SentenceTransformer download.
    """
    def _fake_encode(lines):
        if not lines:
            return np.zeros((0, 0), dtype=np.float32)
        out = np.zeros((len(lines), 8), dtype=np.float32)
        for i, line in enumerate(lines):
            seed = abs(hash(line)) % (2**31)
            rng = np.random.default_rng(seed)
            v = rng.standard_normal(8).astype(np.float32)
            n = float(np.linalg.norm(v))
            if n > 0:
                v /= n
            out[i] = v
        return out

    monkeypatch.setattr(emb_mod, "encode", _fake_encode)
    yield


@pytest.fixture(autouse=True)
def stub_keyword_matrix(monkeypatch):
    """No-op the receiver's keyword-matrix rebuild in tests.

    The rebuild walks the bank for SQL/README content; tests use minimal
    stub bundles where a real rebuild produces an empty matrix anyway.
    Keeping it stubbed isolates merge tests from the script-loading path.
    """
    monkeypatch.setattr(
        rcv_mod.AnalysisBankReceiver, "_rebuild_keyword_matrix",
        staticmethod(lambda: None),
    )
    yield
