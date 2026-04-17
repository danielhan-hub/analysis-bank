"""Evaluate candidate procedure submissions for the analysis bank.

The receiver acts as "customs" — it inspects candidate folders dropped
into candidates/, critically evaluates each against the existing library,
and accepts or rejects them.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from analysis_bank.paths import CANDIDATES_DIR, INDEX_PATH, PROCEDURES_DIR, PROMPTS_DIR

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
        results = await receiver.evaluate()
    """

    def __init__(self, timeout_seconds: int = 600, max_agent_turns: int = 50):
        self.timeout_seconds = timeout_seconds
        self.max_agent_turns = max_agent_turns
        self._prompt: str | None = None

    async def evaluate(
        self, candidates_dir: Path | None = None
    ) -> list[ReceiverVerdict]:
        """Inspect candidates/ folder, evaluate each, accept or reject.

        Each candidate folder (new_analysis_candidate_N/) contains:
          - INDEX_PROPOSED.md (full INDEX.md with proposed additions)
          - {NN}_{name}/README.md
          - {NN}_{name}/procedure.sql

        Returns list of ReceiverVerdict.
        """
        candidates_dir = candidates_dir or CANDIDATES_DIR
        if not candidates_dir.exists():
            logger.warning("Candidates directory does not exist: %s", candidates_dir)
            return []

        candidate_folders = sorted(
            d for d in candidates_dir.iterdir()
            if d.is_dir() and d.name.startswith("new_analysis_candidate_")
        )

        if not candidate_folders:
            logger.info("No candidate folders found in %s", candidates_dir)
            return []

        results: list[ReceiverVerdict] = []
        for folder in candidate_folders:
            verdict = await self._evaluate_candidate(folder)
            results.append(verdict)
            self._print_verdict(verdict)

        # Summarize net INDEX.md changes across all accepted candidates
        accepted = [r for r in results if r.verdict == "ACCEPT"]
        if accepted:
            print(f"\n{len(accepted)} candidate(s) accepted.")
            print("Run the receiver's apply step to merge into the library.")

        return results

    async def _evaluate_candidate(self, candidate_dir: Path) -> ReceiverVerdict:
        """Run Opus agent to critically evaluate one candidate."""
        from claude_agent_sdk import AgentDefinition, AgentRunner

        prompt = self._build_prompt(candidate_dir)

        agent_def = AgentDefinition(
            model="claude-opus-4-6",
            instructions=self._load_agent_prompt(),
            tools=["Read", "Glob", "Grep"],
        )

        runner = AgentRunner(
            agent=agent_def,
            prompt=prompt,
            cwd=str(candidate_dir),
            timeout_seconds=self.timeout_seconds,
            max_turns=self.max_agent_turns,
        )

        result = await runner.run()
        return self._parse_verdict(candidate_dir.name, result)

    def _build_prompt(self, candidate_dir: Path) -> str:
        """Build the evaluation prompt for a single candidate."""
        return (
            f"Evaluate the candidate procedure submission in this folder.\n\n"
            f"## Current Library\n"
            f"- INDEX.md: {INDEX_PATH}\n"
            f"- Procedures directory: {PROCEDURES_DIR}\n\n"
            f"## Candidate\n"
            f"- Candidate folder: {candidate_dir}\n"
            f"- Look for: INDEX_PROPOSED.md, a subfolder with README.md and procedure.sql\n\n"
            f"## Your Task\n"
            f"1. Read the current INDEX.md to understand what already exists\n"
            f"2. Read the candidate's INDEX_PROPOSED.md to see what changes are proposed\n"
            f"3. Read the candidate's README.md and procedure.sql\n"
            f"4. If this proposes modifying an existing procedure, read that procedure too\n"
            f"5. Critically evaluate:\n"
            f"   - Is this genuinely distinct from existing procedures?\n"
            f"   - Is the SQL well-parameterized and generalizable?\n"
            f"   - Does the README follow the library's conventions?\n"
            f"   - Are the INDEX.md routing entries sensible?\n"
            f"   - For expansion proposals: does the modification bring real additional value?\n"
            f"6. Respond with exactly one of:\n"
            f"   VERDICT: ACCEPT — [reason]\n"
            f"   VERDICT: REJECT — [reason]\n"
            f"   VERDICT: REVISE — [specific feedback on what to change]\n"
        )

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

        # If we couldn't parse a structured verdict, treat the whole output as the reason
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

    def apply(self, candidate_name: str) -> Path:
        """Merge a candidate into the live library.

        Run this after reviewing the verdict from ``evaluate()``. Applies
        the changes wholesale and removes the candidate folder.

        Steps:
          1. Wholesale replace INDEX.md with the candidate's INDEX_PROPOSED.md
          2. Copy the {NN}_{name}/ procedure folder into procedures/,
             overwriting any existing folder of the same name (expansion case)
          3. Delete the candidate folder

        If INDEX_BASELINE.md is present and differs from the live INDEX.md,
        emit a warning — the wholesale replace will silently overwrite any
        edits made to INDEX.md after this candidate was generated. The apply
        proceeds anyway; review ``git diff INDEX.md`` before committing.

        Args:
            candidate_name: The candidate folder name, e.g.
                "new_analysis_candidate_3". Resolved against CANDIDATES_DIR.

        Returns:
            The path of the newly installed procedure folder.
        """
        candidate_dir = CANDIDATES_DIR / candidate_name
        if not candidate_dir.is_dir():
            raise FileNotFoundError(f"Candidate folder not found: {candidate_dir}")

        proposed = candidate_dir / "INDEX_PROPOSED.md"
        if not proposed.exists():
            raise FileNotFoundError(f"Missing required file: {proposed}")

        proc_subdirs = [
            d for d in candidate_dir.iterdir()
            if d.is_dir() and re.match(r"\d+_", d.name)
        ]
        if len(proc_subdirs) != 1:
            raise RuntimeError(
                f"Expected exactly one {{NN}}_{{name}}/ procedure subfolder in "
                f"{candidate_dir}, found {len(proc_subdirs)}: {[d.name for d in proc_subdirs]}"
            )
        proc_src = proc_subdirs[0]

        baseline = candidate_dir / "INDEX_BASELINE.md"
        if baseline.exists() and INDEX_PATH.exists():
            if baseline.read_text(encoding="utf-8") != INDEX_PATH.read_text(encoding="utf-8"):
                logger.warning(
                    "INDEX.md has drifted since this candidate was generated. "
                    "Wholesale replace will overwrite intervening changes. "
                    "Review `git diff INDEX.md` before committing."
                )

        INDEX_PATH.write_text(proposed.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Replaced INDEX.md from %s", proposed)

        proc_dst = PROCEDURES_DIR / proc_src.name
        if proc_dst.exists():
            shutil.rmtree(proc_dst)
            logger.info("Removed existing %s (overwrite)", proc_dst)
        shutil.copytree(proc_src, proc_dst)
        logger.info("Installed procedure to %s", proc_dst)

        shutil.rmtree(candidate_dir)
        logger.info("Removed candidate folder %s", candidate_dir)

        print(f"Applied {candidate_name} -> {proc_dst}")
        return proc_dst
