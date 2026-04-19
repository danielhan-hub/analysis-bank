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

        target_name = name or source_path.name
        CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        target = CANDIDATES_DIR / target_name
        if target.exists():
            raise FileExistsError(
                f"A candidate named '{target_name}' already exists at {target}. "
                f"Either pass a different `name=` to receiver.submit(), or "
                f"discard the existing one first with receiver.discard('{target_name}')."
            )

        shutil.copytree(source_path, target)
        print(f"Submitted candidate -> {target}")
        return target

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------

    async def evaluate(
        self, candidates_dir: Path | None = None
    ) -> list[ReceiverVerdict]:
        """Inspect ``candidates/``, evaluate each candidate, return verdicts.

        For each candidate folder:
          1. Sanity-check files (BASELINE, PROPOSED, procedure subfolder)
          2. Smoke test the procedure against Snowflake — auto-REJECT if it
             fails, without spending an LLM call
          3. Run the Opus receiver agent for the qualitative judgment

        Returns list of :class:`ReceiverVerdict`.
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
        for folder in candidate_folders:
            verdict = await self._evaluate_candidate(folder)
            results.append(verdict)
            self._print_verdict(verdict)

        accepted = [r for r in results if r.verdict == "ACCEPT"]
        if accepted:
            print(f"\n{len(accepted)} candidate(s) accepted.")
            print("Call receiver.apply(<candidate_name>) on each to merge into the library.")

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

        return self._parse_verdict(candidate_dir.name, result_text)

    def _build_prompt(self, candidate_dir: Path) -> str:
        """Per-candidate user prompt — paths only.

        The system prompt (``prompts/receiver_agent.md``) is the canonical
        explanation of how to read the three INDEX files and what criteria
        to apply. This builder just hands the agent the paths it needs.
        """
        return (
            f"Evaluate the candidate procedure submission in this folder.\n\n"
            f"## Paths\n"
            f"- Candidate folder: {candidate_dir}\n"
            f"- BASELINE INDEX: {candidate_dir}/INDEX_BASELINE.md\n"
            f"- PROPOSED INDEX: {candidate_dir}/INDEX_PROPOSED.md\n"
            f"- LIVE INDEX: {INDEX_PATH}\n"
            f"- Procedures directory: {PROCEDURES_DIR}\n\n"
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
        shutil.copytree(proc_src, proc_dst)
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
