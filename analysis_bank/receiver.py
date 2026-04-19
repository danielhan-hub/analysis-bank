"""Evaluate, apply, submit, and discard candidate procedure submissions.

The receiver acts as "customs" for the analysis bank — it inspects candidate
folders, runs an Opus agent to critically evaluate each, and on operator
approval merges them into the live library.

Public methods (curator-facing):
- ``submit(source, name=None)``   — copy a candidate folder into ``candidates/``
- ``evaluate(candidates_dir=None)`` — run the agent over every candidate
- ``apply(candidate_name)``        — merge an accepted candidate into the library
- ``discard(candidate_name)``      — delete one candidate from ``candidates/``
- ``discard_all()``                — delete every candidate from ``candidates/``
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from analysis_bank.paths import CANDIDATES_DIR, INDEX_PATH, PROCEDURES_DIR, PROMPTS_DIR
from analysis_bank.smoke import SmokeTestError, smoke_test_procedure

logger = logging.getLogger(__name__)


# Curator-loop artifacts — live in candidates/, filtered out by apply() when
# copying into procedures/. Names mirror the verdict enum (REVISE/REJECT) so
# the on-disk shape directly reflects the receiver's most recent judgment.
REVISE_FILENAME = "RECEIVER_REVISE.md"
REJECT_FILENAME = "RECEIVER_REJECT.md"
SOURCE_FILENAME = "_source.sql"
CURATOR_ARTIFACTS = (SOURCE_FILENAME, REVISE_FILENAME, REJECT_FILENAME)


@dataclass
class ReceiverVerdict:
    """Result of evaluating one candidate."""

    candidate: str
    verdict: str  # "ACCEPT" | "REJECT" | "REVISE"
    reason: str


class AnalysisBankReceiver:
    """Evaluates candidate procedure submissions and accepts/rejects them.

    Usage::

        receiver = AnalysisBankReceiver()
        receiver.submit("/path/to/case/codes/new_analysis_candidate_5")
        verdicts = await receiver.evaluate()
        # review verdicts, then for each ACCEPT:
        receiver.apply("new_analysis_candidate_5")
        # or, to throw away rejected candidates:
        receiver.discard("new_analysis_candidate_5")
    """

    def __init__(self, timeout_seconds: int = 600, max_agent_turns: int = 50):
        self.timeout_seconds = timeout_seconds
        self.max_agent_turns = max_agent_turns
        self._prompt: str | None = None

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def submit(self, source: str | Path, name: str | None = None) -> Path:
        """Copy a candidate folder into the library's ``candidates/`` directory.

        Saves the curator from manually moving folders in Finder.

        **Resubmission semantics.** If the source candidate's ``_source.sql``
        matches the ``_source.sql`` of any existing candidate in
        ``candidates/``, those existing candidates are **removed** and the
        newest of their ``RECEIVER_REVISE.md`` files (if any) is
        **carried forward** into the new candidate's procedure subfolder.
        This keeps the candidates inbox in a clean one-per-source state and
        preserves the receiver's prior feedback so the next ``evaluate()``
        can judge whether the broker actually addressed it.

        ``RECEIVER_REJECT.md`` files on matching old candidates are NOT
        carried forward — a prior REJECT means "give up on this script,"
        so the next round should evaluate fresh.

        **Operation order is failure-safe.** The new candidate is copied into
        ``candidates/`` and the carried-forward feedback is written there
        BEFORE the old matching candidates are removed. If anything fails
        during the copy/write step, the partial new candidate is rolled back
        and the old candidates are left intact — no data loss.

        Args:
            source: Path to the candidate folder produced by
                ``ads_ms_analysis.AdsMSAnalyzer.promote_code()`` (e.g.
                ``<case>/codes/new_analysis_candidate_5``).
            name: Optional name to use inside ``candidates/``. Defaults to
                the source folder's name. Override this to namespace by case
                (e.g. ``"panera__new_analysis_candidate_5"``) when multiple
                cases would otherwise collide.

        Returns:
            Path of the new folder under ``candidates/``.

        Raises:
            FileNotFoundError: If ``source`` doesn't exist.
            FileExistsError: If a folder with the resolved name already exists
                in ``candidates/`` — refuses to overwrite.
        """
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source candidate folder not found: {source_path}")

        # Sanity-check the source has the expected shape before copying
        self._require_candidate_files(source_path)

        # Find matching candidates; do NOT delete them yet. We delete only
        # after the new candidate is safely in place. If we deleted up front
        # and then the copytree failed, we'd lose the prior feedback with no
        # way to recover it.
        matching, latest_revise_path = self._find_same_source_candidates(source_path)

        target_name = name or source_path.name
        CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        target = CANDIDATES_DIR / target_name
        if target.exists():
            raise FileExistsError(
                f"A candidate named '{target_name}' already exists at {target}. "
                f"Either pass a different `name=` to receiver.submit(), or "
                f"discard the existing one first with receiver.discard('{target_name}')."
            )

        # --- Copy + carry-forward (rollback on any failure) ---------------
        try:
            shutil.copytree(source_path, target)
            if latest_revise_path is not None:
                target_proc = self._require_candidate_files(target)
                # shutil.copy preserves content + permissions; no in-memory
                # round-trip and no risk of reading the source twice.
                shutil.copy(latest_revise_path, target_proc / REVISE_FILENAME)
        except Exception:
            # Roll back the partial new candidate so the user is back to the
            # pre-submit state. Old matching candidates have NOT been touched
            # yet, so their feedback is still on disk.
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise

        if latest_revise_path is not None:
            print(f"Carried forward prior {REVISE_FILENAME} from removed candidate(s).")

        # --- Now safe to remove old matching candidates -------------------
        if matching:
            names = [c.name for c in matching]
            print(
                f"Removing {len(matching)} stale candidate(s) with matching "
                f"{SOURCE_FILENAME}: {names}"
            )
            for cand in matching:
                shutil.rmtree(cand)

        print(f"Submitted candidate -> {target}")
        return target

    @staticmethod
    def _find_same_source_candidates(
        new_candidate_dir: Path,
    ) -> tuple[list[Path], Path | None]:
        """Locate existing candidates with matching ``_source.sql``.

        Returns ``(matching_candidate_dirs, newest_revise_file_or_None)``.
        Does NOT delete anything — the caller decides when it's safe.

        Match is by exact source-content equality, the same comparison the
        producer side uses, so both sides agree on "the same script".
        Defensive against malformed candidates and unreadable files.
        """
        # Read the new candidate's source. Without _source.sql we can't match
        # on content — return empty.
        new_proc_subs = [
            d for d in new_candidate_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        if len(new_proc_subs) != 1:
            return [], None
        new_source_file = new_proc_subs[0] / SOURCE_FILENAME
        if not new_source_file.exists():
            return [], None
        try:
            new_source_text = new_source_file.read_text(encoding="utf-8")
        except OSError:
            return [], None
        if not CANDIDATES_DIR.exists():
            return [], None

        matching: list[Path] = []
        revise_paths: list[tuple[Path, float]] = []
        for cand in CANDIDATES_DIR.iterdir():
            if not cand.is_dir() or cand.name.startswith("."):
                continue
            for sub in cand.iterdir():
                if not sub.is_dir() or sub.name.startswith("."):
                    continue
                src_file = sub / SOURCE_FILENAME
                if not src_file.exists():
                    continue
                try:
                    if src_file.read_text(encoding="utf-8") != new_source_text:
                        continue
                except OSError:
                    continue
                matching.append(cand)
                # Carry forward only REVISE feedback. REJECT means "give up
                # on this script" — passing it forward would just confuse the
                # next round's receiver.
                rev = sub / REVISE_FILENAME
                if rev.exists():
                    try:
                        revise_paths.append((rev, rev.stat().st_mtime))
                    except OSError:
                        pass
                break  # one matching subfolder per candidate is enough

        if not matching:
            return [], None
        latest_revise: Path | None = None
        if revise_paths:
            revise_paths.sort(key=lambda t: t[1], reverse=True)
            latest_revise = revise_paths[0][0]
        return matching, latest_revise

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        candidates_dir: Path | None = None,
        auto_discard_rejects: bool = False,
    ) -> list[ReceiverVerdict]:
        """Inspect ``candidates/``, evaluate each candidate, return verdicts.

        For each candidate folder:
          1. Sanity-check files (BASELINE, PROPOSED, procedure subfolder)
          2. Smoke test the procedure against Snowflake — auto-REJECT if it
             fails, without spending an LLM call
          3. Run the Opus receiver agent for the qualitative judgment

        Args:
            candidates_dir: Override the directory to scan. Defaults to the
                bank's ``candidates/`` folder.
            auto_discard_rejects: When ``True``, immediately delete any
                candidate folder that receives a ``REJECT`` verdict (saves a
                manual ``discard()`` call). Defaults to ``False`` so the
                operator can inspect the candidate before it's removed.

                There is deliberately NO symmetric ``auto_apply_accepts``
                knob: ``apply()`` is irreversible (writes to live INDEX.md +
                procedures/) and runs drift refusal + smoke re-test guards
                that the operator should consciously gate. Keeping
                acceptance manual is a feature, not an oversight.

        Returns:
            List of :class:`ReceiverVerdict`. The list still includes REJECT
            verdicts even when ``auto_discard_rejects=True`` — only the
            on-disk folder is removed.
        """
        candidates_dir = candidates_dir or CANDIDATES_DIR
        if not candidates_dir.exists():
            logger.warning("Candidates directory does not exist: %s", candidates_dir)
            return []

        candidate_folders = sorted(
            d for d in candidates_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )

        if not candidate_folders:
            logger.info("No candidate folders found in %s", candidates_dir)
            return []

        results: list[ReceiverVerdict] = []
        discarded = 0
        for folder in candidate_folders:
            verdict = await self._evaluate_candidate(folder)
            results.append(verdict)
            self._print_verdict(verdict)
            if auto_discard_rejects and verdict.verdict == "REJECT":
                shutil.rmtree(folder)
                discarded += 1
                print(f"      auto-discarded (REJECT, auto_discard_rejects=True)")

        accepted = [r for r in results if r.verdict == "ACCEPT"]
        if accepted:
            print(f"\n{len(accepted)} candidate(s) accepted.")
            print("Call receiver.apply(<candidate_name>) on each to merge into the library.")
        if discarded:
            print(f"\n{discarded} REJECTed candidate(s) auto-discarded.")

        return results

    async def _evaluate_candidate(self, candidate_dir: Path) -> ReceiverVerdict:
        """Smoke-test, then run Opus agent if smoke passed."""
        from claude_agent_sdk import ClaudeAgentOptions, query

        proc_dir = self._require_candidate_files(candidate_dir)

        # Code-side gate: smoke test before spending an LLM call. A candidate
        # that doesn't compile or whose SAMPLE CALL fails has no business
        # being judged on documentation quality.
        try:
            smoke_test_procedure(proc_dir / "procedure.sql", verbose=False)
        except SmokeTestError as e:
            return ReceiverVerdict(
                candidate=candidate_dir.name,
                verdict="REJECT",
                reason=f"Smoke test failed (auto-REJECT, no LLM call). {e}",
            )

        prompt = self._build_prompt(candidate_dir)
        sdk_env = {"CLAUDECODE": ""}

        async def _drive_agent() -> str:
            captured = ""
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    system_prompt=self._load_agent_prompt(),
                    allowed_tools=["Read", "Glob", "Grep"],
                    model="opus",
                    cwd=str(candidate_dir),
                    permission_mode="bypassPermissions",
                    max_turns=self.max_agent_turns,
                    env=sdk_env,
                ),
            ):
                if hasattr(message, "result") and message.result:
                    captured = message.result
            return captured

        try:
            result_text = await asyncio.wait_for(_drive_agent(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"Receiver agent timed out after {self.timeout_seconds}s "
                f"evaluating {candidate_dir.name}"
            ) from e
        except Exception as e:
            error_msg = f"Receiver agent failed for {candidate_dir.name}: {type(e).__name__}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        if not result_text:
            logger.warning("Receiver agent returned empty result for %s", candidate_dir.name)

        verdict = self._parse_verdict(candidate_dir.name, result_text)

        # Persist the most recent verdict file (REVISE or REJECT) so the
        # producer side can detect and react. Mutual exclusion: writing one
        # always removes the other if present, so the on-disk state always
        # reflects the latest verdict only.
        if verdict.verdict == "REVISE":
            structured = self._extract_section(result_text, "RECEIVER_REVISE")
            body = structured if structured else (
                f"## RECEIVER_REVISE\n\n"
                f"### Summary\n{verdict.reason}\n\n"
                f"_Note: agent did not emit a structured REVISE block; "
                f"this is the raw verdict reason._\n"
            )
            self._write_verdict_file(proc_dir, REVISE_FILENAME, body)
            logger.info("Wrote %s for %s", REVISE_FILENAME, candidate_dir.name)
        elif verdict.verdict == "REJECT":
            structured = self._extract_section(result_text, "RECEIVER_REJECT")
            body = structured if structured else (
                f"## RECEIVER_REJECT\n\n"
                f"### Summary\n{verdict.reason}\n\n"
                f"_Note: agent did not emit a structured REJECT block; "
                f"this is the raw verdict reason._\n"
            )
            self._write_verdict_file(proc_dir, REJECT_FILENAME, body)
            logger.info("Wrote %s for %s", REJECT_FILENAME, candidate_dir.name)

        return verdict

    @staticmethod
    def _write_verdict_file(proc_dir: Path, filename: str, body: str) -> None:
        """Write ``filename`` (REVISE or REJECT) and remove the OTHER one.

        Mutual exclusion: a candidate's procedure subfolder holds at most one
        verdict file at a time. The on-disk shape always reflects the most
        recent verdict, so the producer side never has to disambiguate.
        """
        if filename not in (REVISE_FILENAME, REJECT_FILENAME):
            raise ValueError(f"Unsupported verdict filename: {filename}")
        other = REJECT_FILENAME if filename == REVISE_FILENAME else REVISE_FILENAME
        other_path = proc_dir / other
        if other_path.exists():
            other_path.unlink()
        (proc_dir / filename).write_text(body, encoding="utf-8")

    @staticmethod
    def _extract_section(agent_output: str, marker_name: str) -> str | None:
        """Return everything from ``## <marker_name>`` heading onward.

        ``None`` if no such heading is present. The heading match is anchored
        to a line start (so prose mentions don't trigger) and case-insensitive.
        Underscore vs space inside the marker is tolerated, so e.g.
        ``RECEIVER_REVISE`` also matches ``RECEIVER REVISE``.
        """
        # Tolerate "_" or " " anywhere there is "_" in the marker name.
        pattern_body = marker_name.replace("_", "[_ ]")
        marker = re.compile(
            rf"^##\s+{pattern_body}\b",
            re.IGNORECASE | re.MULTILINE,
        )
        m = marker.search(agent_output)
        if not m:
            return None
        return agent_output[m.start():].strip() + "\n"

    def _build_prompt(self, candidate_dir: Path) -> str:
        """Per-candidate user prompt — paths only.

        The system prompt (``prompts/receiver_agent.md``) is the canonical
        explanation of how to read the three INDEX files and what criteria
        to apply. This builder just hands the agent the paths it needs.

        If a ``RECEIVER_REVISE.md`` or ``RECEIVER_REJECT.md`` exists in the
        candidate's procedure subfolder (placed there by a prior round or
        carried forward by ``submit()``), surface its path explicitly so the
        agent doesn't have to glob to discover it. The two files are mutually
        exclusive — only the latest verdict's file is on disk at any time.
        """
        proc_dir = self._require_candidate_files(candidate_dir)
        prior_revise = proc_dir / REVISE_FILENAME
        prior_reject = proc_dir / REJECT_FILENAME

        # Mutual exclusion is enforced on write, so at most one of these
        # exists. Surface whichever is present.
        prior_block = ""
        if prior_revise.exists():
            prior_block = (
                f"\n## Prior Round Feedback (re-promote)\n"
                f"A previous round already reviewed an earlier version of this exact source "
                f"script and asked for revisions. Read it FIRST, then in your new verdict "
                f"explicitly call out any 'Concrete Fixes' items that remain unaddressed.\n"
                f"- Prior feedback: {prior_revise}\n"
            )
        elif prior_reject.exists():
            prior_block = (
                f"\n## Prior Round Rejection (re-promote)\n"
                f"A previous round already REJECTed an earlier version of this exact source "
                f"script. Read it FIRST. If the new candidate did not meaningfully address "
                f"the rejection reasons, REJECT again. If it did, judge the new candidate "
                f"on its own merits.\n"
                f"- Prior rejection: {prior_reject}\n"
            )

        return (
            f"Evaluate the candidate procedure submission in this folder.\n\n"
            f"## Paths\n"
            f"- Candidate folder: {candidate_dir}\n"
            f"- BASELINE INDEX: {candidate_dir}/INDEX_BASELINE.md\n"
            f"- PROPOSED INDEX: {candidate_dir}/INDEX_PROPOSED.md\n"
            f"- LIVE INDEX: {INDEX_PATH}\n"
            f"- Procedures directory: {PROCEDURES_DIR}\n"
            f"{prior_block}\n"
            f"See your system prompt for how to read the three INDEX files and what to "
            f"judge. Respond with exactly one VERDICT line.\n"
        )

    @staticmethod
    def _require_candidate_files(candidate_dir: Path) -> Path:
        """Fail fast if any required candidate file is missing.

        Returns the procedure subfolder path so callers don't have to re-glob.
        Subfolder name shape encodes intent:
          - ``\\d+_<name>/``  → modification of existing procedure
          - ``<name>/``       → new procedure (number assigned at apply time)
        """
        required = ["INDEX_BASELINE.md", "INDEX_PROPOSED.md"]
        missing = [name for name in required if not (candidate_dir / name).exists()]
        if missing:
            raise FileNotFoundError(
                f"Candidate {candidate_dir.name} is missing required file(s): "
                f"{', '.join(missing)}. Every candidate produced by promote_code() must "
                f"include both INDEX_BASELINE.md and INDEX_PROPOSED.md."
            )

        proc_subdirs = [
            d for d in candidate_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        if len(proc_subdirs) != 1:
            raise RuntimeError(
                f"Candidate {candidate_dir.name} must contain exactly one procedure "
                f"subfolder, found {len(proc_subdirs)}: {[d.name for d in proc_subdirs]}"
            )
        proc_dir = proc_subdirs[0]
        if not (proc_dir / "procedure.sql").exists():
            raise FileNotFoundError(
                f"Candidate {candidate_dir.name}: {proc_dir.name}/procedure.sql is missing."
            )
        if not (proc_dir / "README.md").exists():
            raise FileNotFoundError(
                f"Candidate {candidate_dir.name}: {proc_dir.name}/README.md is missing."
            )
        return proc_dir

    @staticmethod
    def _classify_proc_subfolder(proc_dir: Path) -> tuple[str, int | None]:
        """Return ``("modify", NN)`` or ``("add", None)`` from folder name.

        The broker signals intent by folder shape:
        - Starts with ``<digits>_`` → modification (NN must match an existing
          procedure folder; receiver does not renumber).
        - No digit prefix → new procedure (receiver assigns next-free NN at
          apply time).
        """
        m = re.match(r"^(\d+)_", proc_dir.name)
        if m:
            return ("modify", int(m.group(1)))
        return ("add", None)

    def _load_agent_prompt(self) -> str:
        """Load the receiver agent system prompt."""
        if self._prompt is None:
            prompt_path = PROMPTS_DIR / "receiver_agent.md"
            self._prompt = prompt_path.read_text()
        return self._prompt

    def _parse_verdict(self, candidate_name: str, agent_output: str) -> ReceiverVerdict:
        """Parse agent output into a ReceiverVerdict."""
        for line in agent_output.splitlines():
            stripped = line.strip()
            if stripped.startswith("VERDICT:"):
                parts = stripped.split("—", 1)
                verdict_part = parts[0].replace("VERDICT:", "").strip()
                reason = parts[1].strip() if len(parts) > 1 else ""
                if verdict_part in ("ACCEPT", "REJECT", "REVISE"):
                    return ReceiverVerdict(
                        candidate=candidate_name,
                        verdict=verdict_part,
                        reason=reason,
                    )

        logger.warning("Could not parse structured verdict for %s", candidate_name)
        return ReceiverVerdict(
            candidate=candidate_name,
            verdict="REVISE",
            reason=f"Agent did not produce a structured verdict. Full output:\n{agent_output}",
        )

    @staticmethod
    def _print_verdict(verdict: ReceiverVerdict) -> None:
        """Print a verdict to the terminal."""
        symbol = {"ACCEPT": "+", "REJECT": "x", "REVISE": "~"}.get(verdict.verdict, "?")
        print(f"  [{symbol}] {verdict.candidate}: {verdict.verdict} — {verdict.reason}")

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    # Hard refusal threshold for INDEX.md replacement. PROPOSED must keep at
    # least this fraction of BASELINE's line count. Catches catastrophic broker
    # corruption (truncation, scramble) without blocking legitimate edits.
    #
    # 0.8 is empirical: a real ADD adds rows (PROPOSED > BASELINE in line
    # count), and a real MODIFY rewrites a single row in place (PROPOSED ≈
    # BASELINE). Anything that loses more than 20% of the lines is almost
    # certainly broker truncation/scramble, not a legitimate edit. Tune up
    # toward 0.9 if false positives appear; tune down toward 0.5 only if a
    # legitimate workflow trims many index entries (none today).
    INDEX_LINE_COUNT_FLOOR = 0.8

    def apply(self, candidate_name: str) -> Path:
        """Merge an accepted candidate into the live library.

        Run this after reviewing the verdict from :meth:`evaluate`. The full
        sequence:

        1. Validate the candidate's shape and required files.
        2. Refuse if BASELINE differs from live INDEX (drift) — caller must
           re-promote against the new state.
        3. Refuse if PROPOSED is dramatically shorter than BASELINE
           (catastrophic broker corruption guard).
        4. Smoke-test the procedure against Snowflake — abort on failure
           (catches schema drift since the candidate was promoted).
        5. Classify the procedure subfolder shape:
             - ``\\d+_<name>/``  → modification (overwrite existing)
             - ``<name>/``       → new (assign next-free NN, rename, replace
               every literal ``{{NN}}`` in PROPOSED with the assigned NN)
        6. Write PROPOSED → live INDEX.md and copy the procedure folder into
           ``procedures/``.
        7. Delete the candidate folder.

        Args:
            candidate_name: Folder name in ``candidates/``, e.g.
                ``"new_analysis_candidate_5"``.

        Returns:
            Path of the newly installed procedure folder.
        """
        candidate_dir = CANDIDATES_DIR / candidate_name
        if not candidate_dir.is_dir():
            raise FileNotFoundError(f"Candidate folder not found: {candidate_dir}")

        proc_src = self._require_candidate_files(candidate_dir)
        proposed = candidate_dir / "INDEX_PROPOSED.md"
        baseline = candidate_dir / "INDEX_BASELINE.md"
        baseline_text = baseline.read_text(encoding="utf-8")
        proposed_text = proposed.read_text(encoding="utf-8")

        # --- Drift refusal -------------------------------------------------
        if INDEX_PATH.exists():
            live_text = INDEX_PATH.read_text(encoding="utf-8")
            if baseline_text != live_text:
                msg = (
                    f"Refusing to apply {candidate_name}: INDEX.md has drifted "
                    f"since this candidate was generated. The candidate's BASELINE "
                    f"no longer matches the live INDEX.md, which means PROPOSED was "
                    f"authored against a stale view of the library and may now have "
                    f"conflicting routing entries.\n\n"
                    f"Re-run promote_code() on the original SQL to regenerate the "
                    f"candidate against the current library state."
                )
                print("\n" + "=" * 60)
                print("DRIFT REFUSAL")
                print("=" * 60)
                print(msg)
                print("=" * 60 + "\n")
                raise RuntimeError(msg)

        # --- Catastrophic-corruption guard --------------------------------
        baseline_lines = baseline_text.count("\n") + 1
        proposed_lines = proposed_text.count("\n") + 1
        floor = int(baseline_lines * self.INDEX_LINE_COUNT_FLOOR)
        if proposed_lines < floor:
            raise RuntimeError(
                f"Refusing to apply {candidate_name}: PROPOSED INDEX.md has "
                f"{proposed_lines} lines, less than {self.INDEX_LINE_COUNT_FLOOR:.0%} "
                f"of BASELINE's {baseline_lines}. Likely broker corruption "
                f"(truncation or scramble) — review the candidate manually."
            )

        # --- Smoke test re-run --------------------------------------------
        try:
            smoke_test_procedure(proc_src / "procedure.sql", verbose=False)
        except SmokeTestError as e:
            raise RuntimeError(
                f"Refusing to apply {candidate_name}: smoke test failed at "
                f"apply-time (the procedure may have passed at promote-time but "
                f"now fails — possibly schema drift in Snowflake).\n\n{e}"
            ) from e

        # --- Classify and resolve numbering -------------------------------
        kind, existing_nn = self._classify_proc_subfolder(proc_src)
        if kind == "modify":
            assert existing_nn is not None
            existing_dst = PROCEDURES_DIR / proc_src.name
            if not existing_dst.exists():
                raise RuntimeError(
                    f"Refusing to apply {candidate_name}: candidate is shaped as a "
                    f"modification of procedure {existing_nn} ({proc_src.name}/), "
                    f"but no such procedure folder exists at {existing_dst}. "
                    f"If this is a new procedure, re-promote with no NN prefix."
                )
            final_proc_name = proc_src.name
            final_proposed_text = proposed_text
            logger.info("Apply path: MODIFY procedure %d (%s)", existing_nn, proc_src.name)
        else:
            next_nn = self._next_available_nn()
            final_proc_name = f"{next_nn}_{proc_src.name}"
            if "{{NN}}" not in proposed_text:
                raise RuntimeError(
                    f"Refusing to apply {candidate_name}: PROPOSED INDEX.md is missing "
                    f"the literal '{{{{NN}}}}' placeholder for the new procedure number. "
                    f"The broker prompt requires `{{{{NN}}}}` wherever the new NN should "
                    f"appear so the receiver can substitute it at apply time."
                )
            final_proposed_text = proposed_text.replace("{{NN}}", str(next_nn))
            logger.info(
                "Apply path: ADD new procedure as %d (%s -> %s)",
                next_nn, proc_src.name, final_proc_name,
            )

        # --- Commit changes -----------------------------------------------
        INDEX_PATH.write_text(final_proposed_text, encoding="utf-8")
        logger.info("Replaced INDEX.md from %s", proposed)

        PROCEDURES_DIR.mkdir(parents=True, exist_ok=True)
        proc_dst = PROCEDURES_DIR / final_proc_name
        if proc_dst.exists():
            shutil.rmtree(proc_dst)
            logger.info("Removed existing %s (overwrite)", proc_dst)
        # Curator-loop artifacts (`_source.sql`, `RECEIVER_REVISE.md`,
        # `RECEIVER_REJECT.md`) belong in candidates/, not procedures/. They
        # exist to drive the broker↔receiver feedback loop, not to document
        # the installed procedure.
        shutil.copytree(
            proc_src, proc_dst,
            ignore=shutil.ignore_patterns(*CURATOR_ARTIFACTS),
        )
        logger.info("Installed procedure to %s", proc_dst)

        shutil.rmtree(candidate_dir)
        logger.info("Removed candidate folder %s", candidate_dir)

        print(f"Applied {candidate_name} -> {proc_dst}")
        return proc_dst

    @staticmethod
    def _next_available_nn() -> int:
        """Scan PROCEDURES_DIR for the highest existing NN and return NN+1."""
        if not PROCEDURES_DIR.exists():
            return 1
        used = []
        for d in PROCEDURES_DIR.iterdir():
            if not d.is_dir():
                continue
            m = re.match(r"^(\d+)_", d.name)
            if m:
                used.append(int(m.group(1)))
        return (max(used) + 1) if used else 1

    # ------------------------------------------------------------------
    # Discard
    # ------------------------------------------------------------------

    def discard(self, candidate_name: str) -> None:
        """Delete a single candidate folder from ``candidates/``.

        Use this for REJECT or REVISE candidates you don't intend to fix.
        Raises ``FileNotFoundError`` if the candidate doesn't exist.
        """
        candidate_dir = CANDIDATES_DIR / candidate_name
        if not candidate_dir.is_dir():
            raise FileNotFoundError(f"Candidate folder not found: {candidate_dir}")
        shutil.rmtree(candidate_dir)
        print(f"Discarded {candidate_name}")

    def discard_all(self) -> int:
        """Delete every candidate folder from ``candidates/``.

        Returns the number of candidates deleted.
        """
        if not CANDIDATES_DIR.exists():
            print("No candidates directory — nothing to discard.")
            return 0
        candidate_folders = [
            d for d in CANDIDATES_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
        for d in candidate_folders:
            shutil.rmtree(d)
            print(f"Discarded {d.name}")
        n = len(candidate_folders)
        if n == 0:
            print("No candidates to discard.")
        else:
            print(f"\nDiscarded {n} candidate(s).")
        return n
