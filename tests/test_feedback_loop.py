"""Tests for the receiver-side REVISE/REJECT feedback-loop machinery.

Covers:
- ``_extract_section`` parser correctness for both REVISE and REJECT markers
- ``_evaluate_candidate`` writes the right verdict file (REVISE / REJECT /
  none for ACCEPT), including fallback when the agent forgets the marker
- Mutual exclusion of ``RECEIVER_REVISE.md`` and ``RECEIVER_REJECT.md``
- ``apply()`` filters all three curator artifacts
  (``_source.sql``, ``RECEIVER_REVISE.md``, ``RECEIVER_REJECT.md``)
- ``submit()`` carry-forward: removes same-source candidates, copies the
  newest REVISE forward, deliberately does NOT carry forward REJECT,
  rolls back on copytree failure
- ``evaluate(auto_discard_rejects=...)`` opt-in delete behavior
- ``_build_prompt`` surfaces prior REVISE / REJECT paths

The async LLM call is replaced by a fake driver that runs the same
verdict-file write code path the real ``_evaluate_candidate`` does.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import analysis_bank.receiver as rcv_mod
from analysis_bank.receiver import (
    AnalysisBankReceiver,
    REJECT_FILENAME,
    REVISE_FILENAME,
    SOURCE_FILENAME,
)

from .conftest import make_candidate


# ---------------------------------------------------------------------------
# _extract_section
# ---------------------------------------------------------------------------


def test_extract_no_marker():
    out = "VERDICT: REVISE — bad\n\nsome ramble"
    assert AnalysisBankReceiver._extract_section(out, "RECEIVER_REVISE") is None


def test_extract_simple_revise():
    out = "VERDICT: REVISE — bad\n\n## RECEIVER_REVISE\n\nstuff"
    got = AnalysisBankReceiver._extract_section(out, "RECEIVER_REVISE")
    assert got is not None
    assert got.startswith("## RECEIVER_REVISE")
    assert "stuff" in got


def test_extract_simple_reject():
    out = "VERDICT: REJECT — dup\n\n## RECEIVER_REJECT\n\n### Reasons\n- duplicate"
    got = AnalysisBankReceiver._extract_section(out, "RECEIVER_REJECT")
    assert got is not None
    assert got.startswith("## RECEIVER_REJECT")
    assert "duplicate" in got


def test_extract_case_insensitive():
    out = "## receiver_revise\n\nbody"
    got = AnalysisBankReceiver._extract_section(out, "RECEIVER_REVISE")
    assert got is not None and "body" in got


def test_extract_space_variant():
    out = "## RECEIVER REVISE\n\nbody"
    got = AnalysisBankReceiver._extract_section(out, "RECEIVER_REVISE")
    assert got is not None


def test_extract_inline_phrase_does_not_match():
    """The phrase appearing in prose (not as a heading) must NOT trigger."""
    out = "VERDICT: REVISE — see receiver_revise below maybe\n\nno heading here"
    assert AnalysisBankReceiver._extract_section(out, "RECEIVER_REVISE") is None


def test_extract_returns_full_tail():
    out = (
        "preface garbage\n"
        "VERDICT: REVISE — x\n\n"
        "## RECEIVER_REVISE\n\n"
        "### Summary\nfoo\n\n"
        "### Concrete Fixes\n- [ ] one\n- [ ] two\n"
    )
    got = AnalysisBankReceiver._extract_section(out, "RECEIVER_REVISE")
    assert "Concrete Fixes" in got
    assert "preface garbage" not in got
    assert got.startswith("## RECEIVER_REVISE")


def test_extract_only_matches_requested_marker():
    """Asking for REVISE should not pick up a REJECT block, and vice versa."""
    out = "VERDICT: REJECT — dup\n\n## RECEIVER_REJECT\n\n### Reasons\n- dup\n"
    assert AnalysisBankReceiver._extract_section(out, "RECEIVER_REVISE") is None
    got = AnalysisBankReceiver._extract_section(out, "RECEIVER_REJECT")
    assert got is not None and "dup" in got


# ---------------------------------------------------------------------------
# Verdict-file write (mirrors _evaluate_candidate's tail)
# ---------------------------------------------------------------------------


def _patch_drive_agent(canned_output: str):
    """Build a fake _evaluate_candidate that uses canned agent output.

    Mirrors the real flow's verdict-file writes via _write_verdict_file so
    mutual exclusion behavior is exercised end-to-end.
    """

    async def fake_drive(self_, candidate_dir):
        proc_dir = AnalysisBankReceiver._require_candidate_files(candidate_dir)
        verdict = self_._parse_verdict(candidate_dir.name, canned_output)
        if verdict.verdict == "REVISE":
            structured = AnalysisBankReceiver._extract_section(
                canned_output, "RECEIVER_REVISE"
            )
            md = structured if structured else (
                f"## RECEIVER_REVISE\n\n### Summary\n{verdict.reason}\n\n"
                f"_Note: agent did not emit a structured REVISE block; "
                f"this is the raw verdict reason._\n"
            )
            AnalysisBankReceiver._write_verdict_file(proc_dir, REVISE_FILENAME, md)
        elif verdict.verdict == "REJECT":
            structured = AnalysisBankReceiver._extract_section(
                canned_output, "RECEIVER_REJECT"
            )
            md = structured if structured else (
                f"## RECEIVER_REJECT\n\n### Summary\n{verdict.reason}\n\n"
                f"_Note: agent did not emit a structured REJECT block; "
                f"this is the raw verdict reason._\n"
            )
            AnalysisBankReceiver._write_verdict_file(proc_dir, REJECT_FILENAME, md)
        return verdict

    return fake_drive


def _run_one_candidate(rcvr, cand_dir, canned_output):
    fake = _patch_drive_agent(canned_output)
    return asyncio.run(fake(rcvr, cand_dir))


def test_revise_writes_revise_file(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(cands, name="rev1", proc_subfolder="thing")
    canned = (
        "VERDICT: REVISE — params hardcoded\n\n"
        "## RECEIVER_REVISE\n\n"
        "### Summary\nNeeds parameterization.\n\n"
        "### Concrete Fixes\n- [ ] Replace 12345 with :v_account_id\n"
    )
    v = _run_one_candidate(AnalysisBankReceiver(), cand, canned)
    assert v.verdict == "REVISE"
    fb = proc / REVISE_FILENAME
    assert fb.exists()
    body = fb.read_text()
    assert "Concrete Fixes" in body
    assert ":v_account_id" in body
    assert not (proc / REJECT_FILENAME).exists()


def test_revise_without_marker_falls_back(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(cands, name="rev2", proc_subfolder="thing")
    canned = "VERDICT: REVISE — agent forgot to emit feedback block"
    _run_one_candidate(AnalysisBankReceiver(), cand, canned)
    body = (proc / REVISE_FILENAME).read_text()
    assert "agent forgot to emit feedback block" in body
    assert "did not emit a structured REVISE block" in body


def test_reject_writes_reject_file(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(cands, name="rej1", proc_subfolder="thing")
    canned = (
        "VERDICT: REJECT — duplicate of procedure 03\n\n"
        "## RECEIVER_REJECT\n\n"
        "### Summary\nDuplicates an existing routing path.\n\n"
        "### Reasons\n- Redundant with procedure 03\n"
    )
    v = _run_one_candidate(AnalysisBankReceiver(), cand, canned)
    assert v.verdict == "REJECT"
    body = (proc / REJECT_FILENAME).read_text()
    assert "Redundant with procedure 03" in body
    assert not (proc / REVISE_FILENAME).exists()


def test_reject_without_marker_falls_back(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(cands, name="rej2", proc_subfolder="thing")
    canned = "VERDICT: REJECT — bare reason no block"
    _run_one_candidate(AnalysisBankReceiver(), cand, canned)
    body = (proc / REJECT_FILENAME).read_text()
    assert "bare reason no block" in body
    assert "did not emit a structured REJECT block" in body


def test_accept_writes_no_files(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(cands, name="acc1", proc_subfolder="thing")
    canned = (
        "VERDICT: ACCEPT — looks great\n\n"
        "## RECEIVER_REVISE\n\nthis should NOT be written\n"
    )
    v = _run_one_candidate(AnalysisBankReceiver(), cand, canned)
    assert v.verdict == "ACCEPT"
    assert not (proc / REVISE_FILENAME).exists()
    assert not (proc / REJECT_FILENAME).exists()


# ---------------------------------------------------------------------------
# Mutual exclusion of REVISE / REJECT files
# ---------------------------------------------------------------------------


def test_revise_then_reject_removes_revise(tmp_bank):
    """REVISE -> REJECT: prior RECEIVER_REVISE.md must be removed."""
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(cands, name="flip", proc_subfolder="thing")
    rcvr = AnalysisBankReceiver()
    _run_one_candidate(
        rcvr, cand,
        "VERDICT: REVISE — fix params\n\n## RECEIVER_REVISE\n\n### Summary\nFix.\n",
    )
    assert (proc / REVISE_FILENAME).exists()
    _run_one_candidate(
        rcvr, cand,
        "VERDICT: REJECT — duplicate\n\n## RECEIVER_REJECT\n\n### Reasons\n- dup\n",
    )
    assert (proc / REJECT_FILENAME).exists()
    assert not (proc / REVISE_FILENAME).exists(), \
        "Prior REVISE file should be removed (mutual exclusion)"


def test_reject_then_revise_removes_reject(tmp_bank):
    """REJECT -> REVISE: prior RECEIVER_REJECT.md must be removed."""
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(cands, name="flop", proc_subfolder="thing")
    rcvr = AnalysisBankReceiver()
    _run_one_candidate(
        rcvr, cand,
        "VERDICT: REJECT — dup\n\n## RECEIVER_REJECT\n\n### Reasons\n- dup\n",
    )
    assert (proc / REJECT_FILENAME).exists()
    _run_one_candidate(
        rcvr, cand,
        "VERDICT: REVISE — fix params\n\n## RECEIVER_REVISE\n\n### Summary\nFix.\n",
    )
    assert (proc / REVISE_FILENAME).exists()
    assert not (proc / REJECT_FILENAME).exists(), \
        "Prior REJECT file should be removed (mutual exclusion)"


def test_write_verdict_file_rejects_unknown(tmp_bank):
    """_write_verdict_file must refuse anything not in (REVISE, REJECT)."""
    _, _, _, cands = tmp_bank
    _, proc = make_candidate(cands, name="x", proc_subfolder="thing")
    with pytest.raises(ValueError, match="Unsupported verdict filename"):
        AnalysisBankReceiver._write_verdict_file(proc, "RANDOM.md", "body")


# ---------------------------------------------------------------------------
# apply() filters all three curator artifacts
# ---------------------------------------------------------------------------


def test_apply_filters_all_curator_artifacts(tmp_bank):
    _, index, _, cands = tmp_bank
    baseline = index.read_text()
    cand, _ = make_candidate(
        cands,
        name="apply_test",
        proc_subfolder="new_proc",
        baseline_text=baseline,
        proposed_text=baseline + "\n| {{NN}} | new_proc |",
        extra_files={
            SOURCE_FILENAME: "SELECT * FROM raw_orders WHERE 1=1",
            REVISE_FILENAME: "## RECEIVER_REVISE\nold revise feedback",
            # Both files coexist in this test only to assert filtering;
            # in practice mutual exclusion prevents this on disk.
            REJECT_FILENAME: "## RECEIVER_REJECT\nold reject reasons",
        },
    )
    rcvr = AnalysisBankReceiver()
    with patch("analysis_bank.receiver.smoke_test_procedure"):
        installed = rcvr.apply("apply_test")
    assert (installed / "procedure.sql").exists()
    assert (installed / "README.md").exists()
    for art in (SOURCE_FILENAME, REVISE_FILENAME, REJECT_FILENAME):
        assert not (installed / art).exists(), f"{art} leaked into procedures/"


# ---------------------------------------------------------------------------
# submit() carry-forward + rollback
# ---------------------------------------------------------------------------


def _make_source_candidate(
    candidates_dir: Path,
    *,
    name: str,
    proc_subfolder: str,
    source_text: str,
    revise_text: str | None = None,
    reject_text: str | None = None,
) -> Path:
    extra = {SOURCE_FILENAME: source_text}
    if revise_text is not None:
        extra[REVISE_FILENAME] = revise_text
    if reject_text is not None:
        extra[REJECT_FILENAME] = reject_text
    cand, _ = make_candidate(
        candidates_dir, name=name, proc_subfolder=proc_subfolder, extra_files=extra
    )
    return cand


def test_submit_removes_same_source_candidate(tmp_bank, src_dir):
    _, _, _, cands = tmp_bank
    _make_source_candidate(
        cands, name="old_cand", proc_subfolder="thing",
        source_text="SELECT * FROM raw_orders",
        revise_text="## RECEIVER_REVISE\n\n### Concrete Fixes\n- [ ] do X\n",
    )
    new_src = _make_source_candidate(
        src_dir, name="new_promote", proc_subfolder="thing",
        source_text="SELECT * FROM raw_orders",
    )
    AnalysisBankReceiver().submit(new_src)
    assert not (cands / "old_cand").exists()
    assert (cands / "new_promote").exists()


def test_submit_carries_forward_revise(tmp_bank, src_dir):
    _, _, _, cands = tmp_bank
    feedback = "## RECEIVER_REVISE\n\n### Concrete Fixes\n- [ ] parameterize 12345\n"
    _make_source_candidate(
        cands, name="old_cand", proc_subfolder="thing",
        source_text="SELECT 12345",
        revise_text=feedback,
    )
    new_src = _make_source_candidate(
        src_dir, name="new_promote", proc_subfolder="thing",
        source_text="SELECT 12345",
    )
    AnalysisBankReceiver().submit(new_src)
    carried = cands / "new_promote" / "thing" / REVISE_FILENAME
    assert carried.exists()
    assert "parameterize 12345" in carried.read_text()


def test_submit_does_not_carry_forward_reject(tmp_bank, src_dir):
    """A REJECT on the old candidate should NOT be carried forward."""
    _, _, _, cands = tmp_bank
    _make_source_candidate(
        cands, name="old_cand", proc_subfolder="thing",
        source_text="SELECT no_revise_here",
        reject_text="## RECEIVER_REJECT\n\n### Reasons\n- dup\n",
    )
    new_src = _make_source_candidate(
        src_dir, name="new_promote", proc_subfolder="thing",
        source_text="SELECT no_revise_here",
    )
    AnalysisBankReceiver().submit(new_src)
    assert not (cands / "old_cand").exists()
    assert not (cands / "new_promote" / "thing" / REVISE_FILENAME).exists()
    assert not (cands / "new_promote" / "thing" / REJECT_FILENAME).exists()


def test_submit_no_match_does_not_carry_anything(tmp_bank, src_dir):
    _, _, _, cands = tmp_bank
    _make_source_candidate(
        cands, name="other_cand", proc_subfolder="thing",
        source_text="SELECT 1",
        revise_text="## RECEIVER_REVISE\nold\n",
    )
    new_src = _make_source_candidate(
        src_dir, name="new_promote", proc_subfolder="thing",
        source_text="SELECT 2",
    )
    AnalysisBankReceiver().submit(new_src)
    assert (cands / "other_cand").exists()
    assert not (cands / "new_promote" / "thing" / REVISE_FILENAME).exists()


def test_submit_removes_all_matching_candidates(tmp_bank, src_dir):
    _, _, _, cands = tmp_bank
    for n in ("old_a", "old_b", "old_c"):
        _make_source_candidate(
            cands, name=n, proc_subfolder="thing",
            source_text="SELECT same",
            revise_text=f"## RECEIVER_REVISE\nfb-{n}\n",
        )
    new_src = _make_source_candidate(
        src_dir, name="new_promote", proc_subfolder="thing",
        source_text="SELECT same",
    )
    AnalysisBankReceiver().submit(new_src)
    for n in ("old_a", "old_b", "old_c"):
        assert not (cands / n).exists()
    assert (cands / "new_promote").exists()
    assert (cands / "new_promote" / "thing" / REVISE_FILENAME).exists()


def test_submit_match_with_no_revise_still_removes(tmp_bank, src_dir):
    _, _, _, cands = tmp_bank
    _make_source_candidate(
        cands, name="old_cand", proc_subfolder="thing",
        source_text="SELECT no_fb",
    )
    new_src = _make_source_candidate(
        src_dir, name="new_promote", proc_subfolder="thing",
        source_text="SELECT no_fb",
    )
    AnalysisBankReceiver().submit(new_src)
    assert not (cands / "old_cand").exists()
    assert (cands / "new_promote").exists()
    assert not (cands / "new_promote" / "thing" / REVISE_FILENAME).exists()


def test_submit_no_source_file_skips_lookup(tmp_bank, src_dir):
    _, _, _, cands = tmp_bank
    _make_source_candidate(
        cands, name="other", proc_subfolder="thing", source_text="SELECT 1",
    )
    new_cand, _ = make_candidate(src_dir, name="new_promote", proc_subfolder="thing")
    AnalysisBankReceiver().submit(new_cand)
    assert (cands / "other").exists()
    assert (cands / "new_promote").exists()


def test_submit_rolls_back_on_copytree_failure(tmp_bank, src_dir):
    """If shutil.copytree fails mid-submit, rollback target and leave old candidates."""
    _, _, _, cands = tmp_bank
    _make_source_candidate(
        cands, name="precious_old", proc_subfolder="thing",
        source_text="SELECT precious",
        revise_text="## RECEIVER_REVISE\n\n### Concrete Fixes\n- [ ] do X\n",
    )
    new_src = _make_source_candidate(
        src_dir, name="will_fail", proc_subfolder="thing",
        source_text="SELECT precious",
    )

    def boom(src, dst, *args, **kwargs):
        # Simulate partial creation then failure
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / "INDEX_BASELINE.md").write_text("partial")
        raise OSError("simulated copytree failure")

    rcvr = AnalysisBankReceiver()
    with patch.object(rcv_mod.shutil, "copytree", side_effect=boom), \
         pytest.raises(OSError, match="simulated copytree failure"):
        rcvr.submit(new_src)
    # Target rolled back
    assert not (cands / "will_fail").exists()
    # Old matching candidate preserved (copy-first ordering)
    assert (cands / "precious_old").exists()
    assert (cands / "precious_old" / "thing" / REVISE_FILENAME).exists()


# ---------------------------------------------------------------------------
# evaluate(auto_discard_rejects=...)
# ---------------------------------------------------------------------------


async def _evaluate_with_canned(rcvr, cands_dir, canned_outputs_by_name, **kwargs):
    """Drive evaluate() but stub out _evaluate_candidate to return canned verdicts."""

    async def fake_eval(self_, folder):
        return self_._parse_verdict(folder.name, canned_outputs_by_name[folder.name])

    with patch.object(AnalysisBankReceiver, "_evaluate_candidate", fake_eval):
        return await rcvr.evaluate(candidates_dir=cands_dir, **kwargs)


def test_evaluate_default_does_not_discard_rejects(tmp_bank):
    _, _, _, cands = tmp_bank
    make_candidate(cands, name="rej_keep", proc_subfolder="thing")
    verdicts = asyncio.run(_evaluate_with_canned(
        AnalysisBankReceiver(), cands,
        {"rej_keep": "VERDICT: REJECT — duplicate"},
    ))
    assert verdicts[0].verdict == "REJECT"
    assert (cands / "rej_keep").exists(), "default evaluate() should NOT auto-discard"


def test_evaluate_auto_discards_rejects_when_opted_in(tmp_bank):
    _, _, _, cands = tmp_bank
    make_candidate(cands, name="rej_gone", proc_subfolder="thing")
    verdicts = asyncio.run(_evaluate_with_canned(
        AnalysisBankReceiver(), cands,
        {"rej_gone": "VERDICT: REJECT — duplicate"},
        auto_discard_rejects=True,
    ))
    assert verdicts[0].verdict == "REJECT"
    assert not (cands / "rej_gone").exists()


def test_evaluate_auto_discard_does_not_touch_accept_or_revise(tmp_bank):
    _, _, _, cands = tmp_bank
    make_candidate(cands, name="a_acc", proc_subfolder="thing")
    make_candidate(cands, name="b_rev", proc_subfolder="thing")
    make_candidate(cands, name="c_rej", proc_subfolder="thing")
    verdicts = asyncio.run(_evaluate_with_canned(
        AnalysisBankReceiver(), cands,
        {
            "a_acc": "VERDICT: ACCEPT — good",
            "b_rev": "VERDICT: REVISE — fix params",
            "c_rej": "VERDICT: REJECT — dup",
        },
        auto_discard_rejects=True,
    ))
    assert (cands / "a_acc").exists()
    assert (cands / "b_rev").exists()
    assert not (cands / "c_rej").exists()
    assert {v.verdict for v in verdicts} == {"ACCEPT", "REVISE", "REJECT"}


# ---------------------------------------------------------------------------
# _build_prompt — surfaces prior REVISE / REJECT path
# ---------------------------------------------------------------------------


def test_build_prompt_no_prior_files(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, _ = make_candidate(cands, name="fresh", proc_subfolder="thing")
    prompt = AnalysisBankReceiver()._build_prompt(cand)
    assert "Prior Round Feedback" not in prompt
    assert "Prior Round Rejection" not in prompt


def test_build_prompt_surfaces_prior_revise_path(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(
        cands, name="redo", proc_subfolder="thing",
        extra_files={REVISE_FILENAME: "## RECEIVER_REVISE\nold\n"},
    )
    prompt = AnalysisBankReceiver()._build_prompt(cand)
    assert "Prior Round Feedback" in prompt
    assert str(proc / REVISE_FILENAME) in prompt
    assert "Prior Round Rejection" not in prompt


def test_build_prompt_surfaces_prior_reject_path(tmp_bank):
    _, _, _, cands = tmp_bank
    cand, proc = make_candidate(
        cands, name="redo_rej", proc_subfolder="thing",
        extra_files={REJECT_FILENAME: "## RECEIVER_REJECT\nold reasons\n"},
    )
    prompt = AnalysisBankReceiver()._build_prompt(cand)
    assert "Prior Round Rejection" in prompt
    assert str(proc / REJECT_FILENAME) in prompt
    assert "Prior Round Feedback" not in prompt
