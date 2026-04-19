"""Synchronous-path tests for AnalysisBankReceiver.

Covers classify, _next_available_nn, submit, _require_candidate_files,
apply (drift / truncation / smoke / ADD / MODIFY paths), discard,
discard_all, _build_prompt, and the line-count floor edge case.

The async LLM-driven evaluate() path is covered in test_feedback_loop.py.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from analysis_bank.receiver import AnalysisBankReceiver
from analysis_bank.smoke import SmokeTestError

from .conftest import make_baseline_index, make_candidate, make_fake_proc_sql


# ---------------------------------------------------------------------------
# _classify_proc_subfolder
# ---------------------------------------------------------------------------


def test_classify_modify():
    kind, nn = AnalysisBankReceiver._classify_proc_subfolder(Path("12_seasonal_trend"))
    assert kind == "modify" and nn == 12


def test_classify_add():
    kind, nn = AnalysisBankReceiver._classify_proc_subfolder(Path("seasonal_trend"))
    assert kind == "add" and nn is None


def test_classify_double_digit():
    kind, nn = AnalysisBankReceiver._classify_proc_subfolder(Path("100_big_one"))
    assert kind == "modify" and nn == 100


def test_classify_no_underscore_is_add():
    """`12foo` should be add, not modify (no underscore separator)."""
    kind, nn = AnalysisBankReceiver._classify_proc_subfolder(Path("12foo"))
    assert kind == "add" and nn is None


# ---------------------------------------------------------------------------
# _next_available_nn
# ---------------------------------------------------------------------------


def test_next_nn_empty(tmp_bank):
    assert AnalysisBankReceiver._next_available_nn() == 1


def test_next_nn_with_existing(tmp_bank):
    _, _, procs, _ = tmp_bank
    (procs / "01_alpha").mkdir()
    (procs / "04_delta").mkdir()
    (procs / "12_lima").mkdir()
    (procs / "not_a_procedure").mkdir()  # ignored — no NN_ prefix
    assert AnalysisBankReceiver._next_available_nn() == 13


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


def test_submit_happy_path(tmp_bank, src_dir):
    rcvr = AnalysisBankReceiver()
    cand_src, _ = make_candidate(src_dir, name="my_cand", proc_subfolder="cool_proc")
    target = rcvr.submit(cand_src)
    assert target.exists()
    assert (target / "INDEX_BASELINE.md").exists()
    assert (target / "cool_proc" / "procedure.sql").exists()


def test_submit_collision_refuses(tmp_bank, src_dir):
    rcvr = AnalysisBankReceiver()
    cand_src, _ = make_candidate(src_dir, name="dup_cand", proc_subfolder="cool_proc")
    rcvr.submit(cand_src)
    with pytest.raises(FileExistsError, match="dup_cand"):
        rcvr.submit(cand_src)


def test_submit_with_namespace_name(tmp_bank, src_dir):
    rcvr = AnalysisBankReceiver()
    cand_src, _ = make_candidate(src_dir, name="my_cand", proc_subfolder="cool_proc")
    target = rcvr.submit(cand_src, name="panera__my_cand")
    assert target.name == "panera__my_cand"
    assert (target / "cool_proc" / "procedure.sql").exists()


def test_submit_missing_source(tmp_bank):
    rcvr = AnalysisBankReceiver()
    with pytest.raises(FileNotFoundError):
        rcvr.submit("/nonexistent/path/foo")


def test_submit_malformed_source_no_proc(tmp_bank, src_dir):
    """Source missing procedure subfolder — submit should refuse before copying."""
    _, _, _, cands = tmp_bank
    rcvr = AnalysisBankReceiver()
    bad = src_dir / "bad_cand"
    bad.mkdir()
    (bad / "INDEX_BASELINE.md").write_text("x")
    (bad / "INDEX_PROPOSED.md").write_text("x")
    # No procedure subfolder
    with pytest.raises(RuntimeError, match="exactly one procedure"):
        rcvr.submit(bad)
    # Confirm no folder leaked into candidates/
    assert not (cands / "bad_cand").exists()


# ---------------------------------------------------------------------------
# _require_candidate_files
# ---------------------------------------------------------------------------


def test_require_files_missing_baseline(tmp_path):
    cand = tmp_path / "cand"
    cand.mkdir()
    (cand / "INDEX_PROPOSED.md").write_text("x")
    (cand / "foo").mkdir()
    (cand / "foo" / "procedure.sql").write_text("x")
    (cand / "foo" / "README.md").write_text("x")
    with pytest.raises(FileNotFoundError, match="INDEX_BASELINE.md"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_require_files_missing_sql(tmp_path):
    cand = tmp_path / "cand2"
    cand.mkdir()
    (cand / "INDEX_BASELINE.md").write_text("x")
    (cand / "INDEX_PROPOSED.md").write_text("x")
    (cand / "foo").mkdir()
    (cand / "foo" / "README.md").write_text("x")
    with pytest.raises(FileNotFoundError, match="procedure.sql"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_require_files_two_proc_subdirs(tmp_path):
    cand = tmp_path / "cand3"
    cand.mkdir()
    (cand / "INDEX_BASELINE.md").write_text("x")
    (cand / "INDEX_PROPOSED.md").write_text("x")
    (cand / "foo").mkdir()
    (cand / "bar").mkdir()
    with pytest.raises(RuntimeError, match="exactly one"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_require_files_ignores_hidden(tmp_path):
    """Hidden dirs (.DS_Store, .git) shouldn't count as procedure subfolders."""
    cand = tmp_path / "cand4"
    cand.mkdir()
    (cand / "INDEX_BASELINE.md").write_text("x")
    (cand / "INDEX_PROPOSED.md").write_text("x")
    (cand / ".DS_Store_dir").mkdir()
    (cand / "foo").mkdir()
    (cand / "foo" / "procedure.sql").write_text("x")
    (cand / "foo" / "README.md").write_text("x")
    proc_dir = AnalysisBankReceiver._require_candidate_files(cand)
    assert proc_dir.name == "foo"


# ---------------------------------------------------------------------------
# apply: guards
# ---------------------------------------------------------------------------


def test_apply_drift_refuses(tmp_bank):
    _, index, _, cands = tmp_bank
    cand, _ = make_candidate(
        cands,
        name="drift_cand",
        proc_subfolder="new_thing",
        baseline_text="OLD BASELINE\n" + make_baseline_index(50),
        proposed_text="OLD BASELINE\n" + make_baseline_index(60),
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"), \
         pytest.raises(RuntimeError, match=r"(?i)drift"):
        rcvr.apply("drift_cand")
    # Candidate must NOT be deleted; live INDEX must NOT be modified.
    assert cand.exists()
    assert "OLD BASELINE" not in index.read_text()


def test_apply_truncation_refuses(tmp_bank):
    _, index, _, cands = tmp_bank
    baseline = make_baseline_index(100)
    index.write_text(baseline)
    cand, _ = make_candidate(
        cands,
        name="trunc_cand",
        proc_subfolder="new_thing",
        baseline_text=baseline,
        proposed_text="line1\nline2\nline3\nline4\nline5",
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"), \
         pytest.raises(RuntimeError, match="less than"):
        rcvr.apply("trunc_cand")
    assert index.read_text() == baseline
    assert cand.exists()


def test_apply_smoke_fail_aborts(tmp_bank):
    _, index, _, cands = tmp_bank
    baseline = make_baseline_index(60)
    index.write_text(baseline)
    cand, _ = make_candidate(
        cands,
        name="smoke_fail_cand",
        proc_subfolder="new_thing",
        baseline_text=baseline,
        proposed_text=baseline + "\n{{NN}} line for new proc",
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure",
               side_effect=SmokeTestError("snowflake said no")), \
         pytest.raises(RuntimeError, match=r"(?i)smoke test failed"):
        rcvr.apply("smoke_fail_cand")
    assert cand.exists()
    assert index.read_text() == baseline


# ---------------------------------------------------------------------------
# apply: ADD path
# ---------------------------------------------------------------------------


def test_apply_add_happy_path(tmp_bank):
    _, index, procs, cands = tmp_bank
    baseline = make_baseline_index(60)
    index.write_text(baseline)
    proposed = baseline + "\n| {{NN}} | new_one | does new thing |\nrouting: {{NN}}"
    cand, _ = make_candidate(
        cands,
        name="add_cand",
        proc_subfolder="new_one",  # no NN prefix => ADD
        baseline_text=baseline,
        proposed_text=proposed,
    )
    # Pre-seed procs with 01_alpha, 02_beta so next NN = 3
    (procs / "01_alpha").mkdir()
    (procs / "02_beta").mkdir()
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"):
        installed = rcvr.apply("add_cand")
    assert installed.name == "3_new_one"
    assert (installed / "procedure.sql").exists()
    new_index = index.read_text()
    assert "{{NN}}" not in new_index
    assert "| 3 | new_one |" in new_index
    assert not cand.exists()  # candidate deleted


def test_apply_add_missing_placeholder_refuses(tmp_bank):
    """ADD path: PROPOSED must contain {{NN}} or refuse."""
    _, index, _, cands = tmp_bank
    baseline = make_baseline_index(60)
    index.write_text(baseline)
    proposed = baseline + "\nextra line with no placeholder\nanother line\n"
    cand, _ = make_candidate(
        cands,
        name="add_no_placeholder",
        proc_subfolder="thing",
        baseline_text=baseline,
        proposed_text=proposed,
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"), \
         pytest.raises(RuntimeError, match=r"\{\{NN\}\}"):
        rcvr.apply("add_no_placeholder")
    assert cand.exists()
    assert index.read_text() == baseline


def test_apply_add_substring_safety(tmp_bank):
    """Plain text 'TBD' or 'NN' in prose must NOT be confused with the placeholder."""
    _, index, _, cands = tmp_bank
    baseline = make_baseline_index(60)
    index.write_text(baseline)
    proposed = (
        baseline
        + "\n| {{NN}} | new_one | NN-based scoring; criteria TBD pending review |"
    )
    make_candidate(
        cands,
        name="substring_cand",
        proc_subfolder="new_one",
        baseline_text=baseline,
        proposed_text=proposed,
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"):
        installed = rcvr.apply("substring_cand")
    assert installed.name == "1_new_one"
    new_index = index.read_text()
    assert "| 1 | new_one |" in new_index
    # Natural-English NN and TBD survive untouched
    assert "NN-based scoring" in new_index
    assert "criteria TBD pending review" in new_index


# ---------------------------------------------------------------------------
# apply: MODIFY path
# ---------------------------------------------------------------------------


def test_apply_modify_happy_path(tmp_bank):
    _, index, procs, cands = tmp_bank
    baseline = make_baseline_index(60)
    index.write_text(baseline)
    existing = procs / "12_seasonal_trend"
    existing.mkdir()
    (existing / "procedure.sql").write_text("-- old\n")
    (existing / "README.md").write_text("# old\n")

    proposed = baseline + "\nupdated row for 12\n"
    cand, _ = make_candidate(
        cands,
        name="mod_cand",
        proc_subfolder="12_seasonal_trend",
        baseline_text=baseline,
        proposed_text=proposed,
        proc_content="-- NEW VERSION\n" + make_fake_proc_sql("seasonal_trend"),
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"):
        installed = rcvr.apply("mod_cand")
    assert installed.name == "12_seasonal_trend"
    assert "NEW VERSION" in (installed / "procedure.sql").read_text()
    assert index.read_text() == proposed
    assert not cand.exists()


def test_apply_modify_unknown_nn_refuses(tmp_bank):
    """MODIFY path: NN must correspond to an existing procedure folder."""
    _, index, _, cands = tmp_bank
    baseline = make_baseline_index(60)
    index.write_text(baseline)
    cand, _ = make_candidate(
        cands,
        name="ghost_mod",
        proc_subfolder="99_ghost",
        baseline_text=baseline,
        proposed_text=baseline + "\nupdated\n",
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"), \
         pytest.raises(RuntimeError, match=r"(99|ghost)"):
        rcvr.apply("ghost_mod")
    assert cand.exists()


# ---------------------------------------------------------------------------
# discard / discard_all
# ---------------------------------------------------------------------------


def test_discard_happy(tmp_bank):
    _, _, _, cands = tmp_bank
    rcvr = AnalysisBankReceiver()
    cand, _ = make_candidate(cands, name="bye", proc_subfolder="x")
    assert cand.exists()
    rcvr.discard("bye")
    assert not cand.exists()


def test_discard_missing(tmp_bank):
    rcvr = AnalysisBankReceiver()
    with pytest.raises(FileNotFoundError):
        rcvr.discard("nonexistent")


def test_discard_all_with_candidates(tmp_bank):
    _, _, _, cands = tmp_bank
    rcvr = AnalysisBankReceiver()
    make_candidate(cands, name="a", proc_subfolder="x")
    make_candidate(cands, name="b", proc_subfolder="y")
    make_candidate(cands, name="c", proc_subfolder="z")
    n = rcvr.discard_all()
    assert n == 3
    assert list(cands.iterdir()) == []


def test_discard_all_empty(tmp_bank):
    rcvr = AnalysisBankReceiver()
    assert rcvr.discard_all() == 0


# ---------------------------------------------------------------------------
# _build_prompt: stays slim
# ---------------------------------------------------------------------------


def test_build_prompt_includes_paths(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, _ = make_candidate(cands, name="cand", proc_subfolder="thing")
    rcvr = AnalysisBankReceiver()
    prompt = rcvr._build_prompt(cand)
    assert "BASELINE INDEX" in prompt
    assert "PROPOSED INDEX" in prompt
    assert "LIVE INDEX" in prompt
    assert "VERDICT" in prompt
    # Should NOT re-explain three-way diff (system prompt does)
    assert "BASELINE → PROPOSED" not in prompt
    # No prior verdict file present → no Prior Round block
    assert "Prior Round Feedback" not in prompt
    assert "Prior Round Rejection" not in prompt
    assert len(prompt) < 1200


# ---------------------------------------------------------------------------
# floor edge case
# ---------------------------------------------------------------------------


def test_apply_at_floor_passes(tmp_bank):
    """PROPOSED at exactly 80% of baseline should NOT be refused."""
    _, index, _, cands = tmp_bank
    baseline_lines = "\n".join([f"line {i}" for i in range(100)])
    proposed_lines = "\n".join([f"line {i}" for i in range(81)]) + "\n{{NN}}"
    index.write_text(baseline_lines)
    make_candidate(
        cands,
        name="at_floor",
        proc_subfolder="thing",
        baseline_text=baseline_lines,
        proposed_text=proposed_lines,
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"):
        installed = rcvr.apply("at_floor")
    assert installed.exists()
