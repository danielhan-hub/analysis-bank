"""Tests for the features module: registry + embedding persistence."""
from __future__ import annotations

import csv

import numpy as np
import pytest

from analysis_bank.features.embeddings import (
    EMBEDDINGS_FILENAME,
    compute_and_persist,
    load_corpus,
    load_procedure_vectors,
)
from analysis_bank.features.registry import (
    load_chart_eligibility,
    upsert_chart_eligible,
)

from .conftest import make_candidate


# ---------------------------------------------------------------------------
# Registry CSV
# ---------------------------------------------------------------------------


def test_upsert_creates_csv_with_header(tmp_bank):
    _, csv_path, _, _ = tmp_bank
    assert not csv_path.exists()
    upsert_chart_eligible("a_20260101_aaa111", True)
    assert csv_path.exists()
    with csv_path.open() as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == ["analysis_id", "chart_eligible"]


def test_upsert_records_chart_eligible(tmp_bank):
    upsert_chart_eligible("a_yes", True)
    upsert_chart_eligible("a_no", False)
    flags = load_chart_eligibility()
    assert flags == {"a_yes": True, "a_no": False}


def test_upsert_replaces_existing_row(tmp_bank):
    upsert_chart_eligible("a_id", False)
    upsert_chart_eligible("a_id", True)
    flags = load_chart_eligibility()
    assert flags == {"a_id": True}


def test_load_chart_eligibility_returns_empty_when_missing(tmp_bank):
    assert load_chart_eligibility() == {}


# ---------------------------------------------------------------------------
# Embedding persistence (encoder is stubbed by fake_encoder fixture)
# ---------------------------------------------------------------------------


def test_compute_and_persist_writes_npy(tmp_bank):
    _, _, procs, _ = tmp_bank
    cand = make_candidate(procs, name="a_20260101_aaa111")
    out = compute_and_persist(cand)
    assert out.exists()
    assert out.name == EMBEDDINGS_FILENAME
    arr = np.load(out)
    # 8 paraphrases + 1 summary
    assert arr.shape == (9, 8)
    assert arr.dtype == np.float32


def test_compute_and_persist_missing_questions_raises(tmp_bank):
    _, _, procs, _ = tmp_bank
    cand = make_candidate(procs, name="a_no_q", include_questions=False)
    with pytest.raises(FileNotFoundError, match="questions.json"):
        compute_and_persist(cand)


def test_load_procedure_vectors_returns_none_when_absent(tmp_bank):
    _, _, procs, _ = tmp_bank
    cand = make_candidate(procs, name="a_no_emb")
    assert load_procedure_vectors(cand) is None


def test_load_corpus_walks_procedures(tmp_bank):
    _, _, procs, _ = tmp_bank
    a = make_candidate(procs, name="a_one")
    b = make_candidate(procs, name="a_two")
    compute_and_persist(a)
    compute_and_persist(b)

    corpus = load_corpus(procs)
    assert set(corpus.keys()) == {"a_one", "a_two"}
    for matrix in corpus.values():
        assert matrix.shape == (9, 8)


def test_load_corpus_encodes_missing_on_demand(tmp_bank):
    """A procedure with questions.json but no embeddings.npy gets encoded."""
    _, _, procs, _ = tmp_bank
    cand = make_candidate(procs, name="a_no_cache")
    # Sanity: no cache yet
    assert not (cand / EMBEDDINGS_FILENAME).exists()
    corpus = load_corpus(procs, encode_missing=True)
    assert "a_no_cache" in corpus
    assert (cand / EMBEDDINGS_FILENAME).exists()


def test_load_corpus_skips_missing_when_encode_disabled(tmp_bank):
    _, _, procs, _ = tmp_bank
    make_candidate(procs, name="a_skip")
    corpus = load_corpus(procs, encode_missing=False)
    assert corpus == {}
