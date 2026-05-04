"""Tests for the simplified single-shot AnalysisBankReceiver.

Covers:
- submit() shape requirements + collision refusal
- _require_candidate_files (procedure.sql + README.md only)
- _parse_verdict (ACCEPT / REJECT / no-verdict-as-REJECT)
- evaluate(): smoke-fail auto-REJECT path; ACCEPT auto-merge path; REJECT
  leaves candidate in candidates/ for inspection
- discard / discard_all

The async LLM-driven inspector and 5-jury scorer are mocked at the receiver
module boundary — we never drive the real SDK.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import analysis_bank.receiver as rcv_mod
from analysis_bank.receiver import AnalysisBankReceiver, ReceiverVerdict
from analysis_bank.smoke import SmokeTestError

from .conftest import make_candidate, make_fake_proc_sql


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


def test_submit_happy_path(tmp_bank, src_dir):
    rcvr = AnalysisBankReceiver()
    cand_src = make_candidate(src_dir, name="a_20260424_abc123")
    target = rcvr.submit(cand_src)
    assert target.exists()
    assert (target / "procedure.sql").exists()
    assert (target / "README.md").exists()


def test_submit_collision_refuses(tmp_bank, src_dir):
    rcvr = AnalysisBankReceiver()
    cand_src = make_candidate(src_dir, name="dup_cand")
    rcvr.submit(cand_src)
    with pytest.raises(FileExistsError, match="dup_cand"):
        rcvr.submit(cand_src)


def test_submit_with_namespace_name(tmp_bank, src_dir):
    rcvr = AnalysisBankReceiver()
    cand_src = make_candidate(src_dir, name="my_cand")
    target = rcvr.submit(cand_src, name="panera__my_cand")
    assert target.name == "panera__my_cand"
    assert (target / "procedure.sql").exists()


def test_submit_missing_source(tmp_bank):
    rcvr = AnalysisBankReceiver()
    with pytest.raises(FileNotFoundError):
        rcvr.submit("/nonexistent/path/foo")


def test_submit_malformed_source_no_sql(tmp_bank, src_dir):
    """Source missing procedure.sql — submit should refuse before copying."""
    _, _, _, cands = tmp_bank
    rcvr = AnalysisBankReceiver()
    bad = src_dir / "bad_cand"
    bad.mkdir()
    (bad / "README.md").write_text("# bad")
    with pytest.raises(FileNotFoundError, match="procedure.sql"):
        rcvr.submit(bad)
    assert not (cands / "bad_cand").exists()


# ---------------------------------------------------------------------------
# _require_candidate_files
# ---------------------------------------------------------------------------


def test_require_files_missing_sql(tmp_path):
    cand = tmp_path / "cand"
    cand.mkdir()
    (cand / "README.md").write_text("x")
    with pytest.raises(FileNotFoundError, match="procedure.sql"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_require_files_missing_readme(tmp_path):
    cand = tmp_path / "cand2"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    with pytest.raises(FileNotFoundError, match="README.md"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_require_files_happy(tmp_path):
    cand = tmp_path / "cand3"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    # Should not raise
    AnalysisBankReceiver._require_candidate_files(cand)


# ---------------------------------------------------------------------------
# _validate_chart_py (optional file)
# ---------------------------------------------------------------------------


def _good_chart_py() -> str:
    return (
        "def render_chart(v_account_id, v_start_date, *, "
        "figsize=(8, 5), output_path='chart.png'):\n"
        "    return None\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    render_chart(v_account_id=1, v_start_date='2026-01-01')\n"
    )


def test_chart_py_optional_when_absent(tmp_path):
    """No chart.py present -> validation passes with just SQL + README."""
    cand = tmp_path / "no_chart"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    # Must not raise — chart.py is optional
    AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_smoke_pass(tmp_path):
    """Valid chart.py with required positional args + clean import passes."""
    cand = tmp_path / "good_chart"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    (cand / "chart.py").write_text(_good_chart_py())
    AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_hardcoded_account_id_rejected(tmp_path):
    """A chart.py that hardcodes account_id is rejected."""
    cand = tmp_path / "hard_id"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    (cand / "chart.py").write_text(
        "def render_chart(*, output_path='chart.png'):\n"
        "    account_id = 12345\n"
        "    return account_id\n"
    )
    with pytest.raises(ValueError, match="hardcodes entity/account"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_csv_read_rejected(tmp_path):
    """A chart.py that reads from CSV is rejected — must use iq.query."""
    cand = tmp_path / "csv_read"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    (cand / "chart.py").write_text(
        "import pandas as pd\n"
        "def render_chart(v_id, *, output_path='chart.png'):\n"
        "    df = pd.read_csv('frozen_output.csv')\n"
        "    return df\n"
    )
    with pytest.raises(ValueError, match="reads from CSV"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_open_csv_rejected(tmp_path):
    """An open('foo.csv') call is also caught by the CSV sweep."""
    cand = tmp_path / "open_csv"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    (cand / "chart.py").write_text(
        "def render_chart(v_id, *, output_path='chart.png'):\n"
        "    with open('frozen.csv') as f:\n"
        "        data = f.read()\n"
        "    return data\n"
    )
    with pytest.raises(ValueError, match="reads from CSV"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_iq_query_passes(tmp_path):
    """A chart.py that uses iq.query (the intended pattern) passes."""
    cand = tmp_path / "iq_query"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    (cand / "chart.py").write_text(
        "def render_chart(v_id, *, output_path='chart.png'):\n"
        "    # df = iq.query(f'CALL my_proc({v_id});')\n"
        "    return None\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    render_chart(v_id=42)\n"
    )
    AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_syntax_error_rejected(tmp_path):
    """A chart.py with a syntax error is rejected by smoke import."""
    cand = tmp_path / "syntax_err"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    # Include __main__ text so the syntax-error gate (smoke import) is what
    # actually fires — otherwise the missing-__main__ gate would fire first.
    (cand / "chart.py").write_text(
        "def render_chart(:\n"
        "    pass\n"
        "if __name__ == '__main__':\n"
        "    render_chart()\n"
    )
    with pytest.raises(ValueError, match="failed to import"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_no_callable_rejected(tmp_path):
    """A chart.py with no top-level callable is rejected."""
    cand = tmp_path / "no_callable"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    # Include __main__ so the missing-callable gate (signature check) is
    # what actually fires.
    (cand / "chart.py").write_text(
        "X = 42\n"
        "Y = 'string'\n"
        "if __name__ == '__main__':\n"
        "    print(X)\n"
    )
    with pytest.raises(ValueError, match="no callable"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_no_required_args_rejected(tmp_path):
    """A chart.py whose render_chart has only kwargs (no required args)
    is rejected — procedure params must be required positional.
    """
    cand = tmp_path / "no_required"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    (cand / "chart.py").write_text(
        "def render_chart(*, output_path='chart.png'):\n"
        "    return None\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    render_chart()\n"
    )
    with pytest.raises(ValueError, match="no required positional args"):
        AnalysisBankReceiver._require_candidate_files(cand)


def test_chart_py_missing_main_block_rejected(tmp_path):
    """A chart.py without an `if __name__ == '__main__':` block is rejected
    — the broker must append one so `python chart.py` reproduces the PNG.
    """
    cand = tmp_path / "no_main"
    cand.mkdir()
    (cand / "procedure.sql").write_text("x")
    (cand / "README.md").write_text("x")
    (cand / "chart.py").write_text(
        "def render_chart(v_id, *, output_path='chart.png'):\n"
        "    return None\n"
    )
    with pytest.raises(ValueError, match="__main__"):
        AnalysisBankReceiver._require_candidate_files(cand)


# ---------------------------------------------------------------------------
# _parse_verdict
# ---------------------------------------------------------------------------


def test_parse_verdict_accept():
    rcvr = AnalysisBankReceiver()
    v = rcvr._parse_verdict("c1", "VERDICT: ACCEPT — adds value")
    assert v.verdict == "ACCEPT"
    assert "adds value" in v.reason


def test_parse_verdict_reject():
    rcvr = AnalysisBankReceiver()
    v = rcvr._parse_verdict(
        "c2",
        "blah blah\nVERDICT: REJECT — redundant\n\n## Suggested Changes\n- delete this\n",
    )
    assert v.verdict == "REJECT"
    assert "redundant" in v.reason


def test_parse_verdict_revise_treated_as_reject():
    """REVISE no longer exists; falling back to REJECT prevents silent merges."""
    rcvr = AnalysisBankReceiver()
    v = rcvr._parse_verdict("c3", "VERDICT: REVISE — needs work")
    assert v.verdict == "REJECT"


def test_parse_verdict_no_verdict_line_treated_as_reject():
    rcvr = AnalysisBankReceiver()
    v = rcvr._parse_verdict("c4", "agent rambled but never voted")
    assert v.verdict == "REJECT"
    assert "did not produce" in v.reason


# ---------------------------------------------------------------------------
# evaluate: smoke-fail auto-REJECT (no LLM call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_smoke_fail_auto_rejects(tmp_bank, fake_scorer):
    _, _, _, cands = tmp_bank
    make_candidate(cands, name="boom_cand")
    rcvr = AnalysisBankReceiver()
    with patch(
        "analysis_bank.receiver.smoke_test_procedure",
        side_effect=SmokeTestError("snowflake said no"),
    ):
        results = await rcvr.evaluate()
    assert len(results) == 1
    assert results[0].verdict == "REJECT"
    assert "Smoke test failed" in results[0].reason
    # Candidate stays in place — operator decides whether to fix or discard
    assert (cands / "boom_cand").exists()


# ---------------------------------------------------------------------------
# evaluate: ACCEPT auto-merges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_accept_auto_merges(monkeypatch, tmp_bank, fake_scorer):
    """Patch the inspector verdict to ACCEPT; real merge path runs."""
    _, csv_path, procs, cands = tmp_bank
    make_candidate(cands, name="a_20260424_abc123")

    # Patch _evaluate_one so it skips the smoke test + LLM call but still
    # invokes the real _merge_accepted path. This keeps the test honest about
    # what merge actually does.
    async def patched_eval_one(self, cd):
        verdict = ReceiverVerdict(candidate=cd.name, verdict="ACCEPT", reason="ok")
        await self._merge_accepted(cd)
        return verdict

    monkeypatch.setattr(AnalysisBankReceiver, "_evaluate_one", patched_eval_one)

    rcvr = AnalysisBankReceiver()
    results = await rcvr.evaluate()

    assert len(results) == 1
    assert results[0].verdict == "ACCEPT"
    assert (procs / "a_20260424_abc123" / "procedure.sql").exists()
    assert not (cands / "a_20260424_abc123").exists()
    assert csv_path.exists()
    assert "a_20260424_abc123" in csv_path.read_text()


# ---------------------------------------------------------------------------
# evaluate: REJECT leaves candidate in place
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_reject_leaves_candidate(monkeypatch, tmp_bank, fake_scorer):
    _, csv_path, procs, cands = tmp_bank
    make_candidate(cands, name="rejected_cand")

    async def patched(self, cd):
        return ReceiverVerdict(candidate=cd.name, verdict="REJECT", reason="thin")

    monkeypatch.setattr(AnalysisBankReceiver, "_evaluate_one", patched)
    rcvr = AnalysisBankReceiver()
    results = await rcvr.evaluate()
    assert len(results) == 1
    assert results[0].verdict == "REJECT"
    # Candidate still on disk for inspection
    assert (cands / "rejected_cand").exists()
    # No new procedure folder was added
    assert not (procs / "rejected_cand").exists()
    # No CSV row written for this id
    if csv_path.exists():
        assert "rejected_cand" not in csv_path.read_text()


# ---------------------------------------------------------------------------
# discard / discard_all
# ---------------------------------------------------------------------------


def test_discard_happy(tmp_bank):
    _, _, _, cands = tmp_bank
    rcvr = AnalysisBankReceiver()
    cand = make_candidate(cands, name="bye")
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
    make_candidate(cands, name="a")
    make_candidate(cands, name="b")
    make_candidate(cands, name="c")
    n = rcvr.discard_all()
    assert n == 3
    assert list(cands.iterdir()) == []


def test_discard_all_empty(tmp_bank):
    rcvr = AnalysisBankReceiver()
    assert rcvr.discard_all() == 0


# ---------------------------------------------------------------------------
# _build_prompt: paths-only, no INDEX references
# ---------------------------------------------------------------------------


def test_build_prompt_paths_only(tmp_bank):
    _, _, _, cands = tmp_bank
    cand = make_candidate(cands, name="a_20260424_abc123")
    rcvr = AnalysisBankReceiver()
    prompt = rcvr._build_prompt(cand)
    assert "procedure.sql" in prompt
    assert "README.md" in prompt
    assert "VERDICT" in prompt
    assert "Suggested Changes" in prompt
    # Should not mention any of the deleted INDEX scaffolding
    assert "INDEX" not in prompt
    assert "BASELINE" not in prompt
    assert "PROPOSED" not in prompt
