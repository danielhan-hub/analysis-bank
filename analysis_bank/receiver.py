"""Submit, evaluate, and discard candidate analysis bundle submissions.

The receiver is the customs gate for the analysis bank: it copies a candidate
folder (an end-to-end analysis bundle: ``procedure.sql`` + ``README.md`` +
optional ``chart.py`` + sibling artifacts) into ``candidates/``, smoke-tests +
inspector-judges it, and on ACCEPT auto-merges into ``procedures/`` while
writing the bundle's feature scores into ``analysis_features.csv``.
There is no REVISE loop; verdict is single-shot.

Public methods (curator-facing):
- ``submit(source, name=None)`` — copy a candidate folder into ``candidates/``
- ``evaluate(candidates_dir=None)`` — run the inspector over every candidate;
  ACCEPT auto-merges, REJECT leaves the folder in place for inspection
- ``discard(candidate_name)`` / ``discard_all()`` — manual cleanup of left-
  behind rejects
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from analysis_bank._async import run_sync
from analysis_bank.features import (
    compute_and_persist,
    reset_caches as reset_retrieval_caches,
    upsert_chart_eligible,
)
from analysis_bank.paths import (
    CANDIDATES_DIR,
    FEATURES_CSV_PATH,
    INSPECTOR_PROMPT_PATH,
    PROCEDURES_DIR,
    PROCEDURES_INDEX_PATH,
)
from analysis_bank.smoke import SmokeTestError, smoke_test_procedure

logger = logging.getLogger(__name__)


@dataclass
class ReceiverVerdict:
    """Result of evaluating one candidate."""

    candidate: str  # the analysis_id (folder name)
    verdict: str  # "ACCEPT" | "REJECT"
    reason: str


class AnalysisBankReceiver:
    """Evaluates candidate analysis bundle submissions; ACCEPT auto-merges.

    Usage (Jupyter or terminal — no asyncio needed)::

        receiver = AnalysisBankReceiver()
        receiver.submit("/path/to/case/codes/a_20260424_a1b2c3")
        verdicts = receiver.evaluate()
        # ACCEPTed candidates are already merged + scored.
        # REJECTed candidates remain in candidates/ — inspect, then:
        receiver.discard("a_20260424_a1b2c3")

    For concurrent use inside an existing async context, call
    ``await receiver.aevaluate()`` instead.
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

        The source folder is expected to be the ``<analysis_id>/`` directory
        produced by ``ads_ms_analysis.AdsMSAnalyzer.promote_code()``, containing
        ``procedure.sql`` + ``README.md``.

        Args:
            source: Path to the candidate folder.
            name: Optional override for the folder name inside ``candidates/``.
                Defaults to the source folder's name (which is already the
                analysis_id).

        Returns:
            Path of the new folder under ``candidates/``.

        Raises:
            FileNotFoundError: If ``source`` doesn't exist or is missing the
                required files.
            FileExistsError: If a folder with the resolved name already exists
                in ``candidates/`` — refuses to overwrite.
        """
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source candidate folder not found: {source_path}")

        # Sanity-check shape before copying so failures surface here, not later
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

    def evaluate(
        self,
        candidates_dir: Path | None = None,
    ) -> list[ReceiverVerdict]:
        """Sync wrapper around :meth:`aevaluate` — works in plain Python AND
        in Jupyter without ``asyncio.run`` or ``await``.

        For concurrent use inside an existing async context, call
        :meth:`aevaluate` directly.
        """
        return run_sync(self.aevaluate(candidates_dir=candidates_dir))

    async def aevaluate(
        self,
        candidates_dir: Path | None = None,
    ) -> list[ReceiverVerdict]:
        """Inspect every candidate; ACCEPT auto-merges, REJECT leaves in place.

        For each candidate folder:
          1. Sanity-check files (``procedure.sql`` + ``README.md``).
          2. Smoke test the procedure against Snowflake — auto-REJECT if it
             fails, without spending an LLM call.
          3. Run the Opus inspector agent for the qualitative judgment.
          4. On ACCEPT: 5-jury score the procedure, write the row into
             ``analysis_features.csv``, copy the folder to ``procedures/``,
             remove the folder from ``candidates/``.
          5. On REJECT: print the reason + suggested-changes block; leave the
             candidate in ``candidates/`` for inspection. Operator decides
             whether to fix manually (rare) or discard.

        Args:
            candidates_dir: Override the directory to scan. Defaults to the
                bank's ``candidates/`` folder.

        Returns:
            One :class:`ReceiverVerdict` per candidate.
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
            verdict = await self._evaluate_one(folder)
            results.append(verdict)
            self._print_verdict(verdict)

        accepted = [r for r in results if r.verdict == "ACCEPT"]
        rejected = [r for r in results if r.verdict == "REJECT"]
        if accepted:
            print(f"\n{len(accepted)} candidate(s) ACCEPTED and merged.")
        if rejected:
            print(
                f"\n{len(rejected)} candidate(s) REJECTED — left in candidates/ for "
                f"inspection. Use receiver.discard(<name>) to remove."
            )
        return results

    async def _evaluate_one(self, candidate_dir: Path) -> ReceiverVerdict:
        """Smoke-test → inspector → on ACCEPT score + merge."""
        from claude_agent_sdk import ClaudeAgentOptions, query

        self._require_candidate_files(candidate_dir)
        proc_sql = candidate_dir / "procedure.sql"

        # Code-side gate: a candidate that doesn't compile or whose SAMPLE CALL
        # fails has no business being judged on documentation quality.
        try:
            smoke_test_procedure(proc_sql, verbose=False)
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
                    system_prompt=self._load_inspector_prompt(),
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
            result_text = await asyncio.wait_for(
                _drive_agent(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"Inspector agent timed out after {self.timeout_seconds}s "
                f"evaluating {candidate_dir.name}"
            ) from e
        except Exception as e:
            error_msg = (
                f"Inspector agent failed for {candidate_dir.name}: "
                f"{type(e).__name__}: {e}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        verdict = self._parse_verdict(candidate_dir.name, result_text)

        if verdict.verdict == "REJECT":
            # Print suggested changes block (everything from "## Suggested
            # Changes" onward) so the operator sees concrete next steps.
            self._print_reject_details(result_text)
            return verdict

        await self._merge_accepted(candidate_dir)
        return verdict

    @staticmethod
    async def _merge_accepted(candidate_dir: Path) -> Path:
        """Promote a candidate into procedures/ (ACCEPT path).

        Run after the inspector returns ACCEPT. Steps:
          1. Copy the folder into procedures/<analysis_id>/
          2. Persist BGE embeddings (questions.json → embeddings.npy)
          3. Rebuild keyword_matrix.csv so Stage B picks the new rows up
          4. Upsert chart_eligible into analysis_features.csv
          5. Update procedures/_index.md
          6. Drop retrieval's in-process cache so the new procedure is
             visible without a process restart
          7. Remove the folder from candidates/

        Ordering note: copytree runs before any side-effect writes so a
        copytree failure cannot leave orphaned rows pointing at a
        nonexistent procedure folder. Embedding + keyword work happens
        on the *promoted* path, not the candidate path, so re-runs
        against the same candidate produce identical artifacts.
        """
        PROCEDURES_DIR.mkdir(parents=True, exist_ok=True)
        proc_dst = PROCEDURES_DIR / candidate_dir.name
        if proc_dst.exists():
            shutil.rmtree(proc_dst)
            logger.info("Removed existing %s (overwrite)", proc_dst)
        shutil.copytree(candidate_dir, proc_dst)

        try:
            compute_and_persist(proc_dst)
        except (FileNotFoundError, ValueError) as e:
            raise RuntimeError(
                f"Promoted {candidate_dir.name} but failed to persist "
                f"embeddings: {e}. The procedure is visible on disk but "
                f"invisible to dense retrieval — fix questions.json and "
                f"re-run analysis_bank.features.compute_and_persist()."
            ) from e

        AnalysisBankReceiver._rebuild_keyword_matrix()

        chart_eligible = (proc_dst / "chart.py").exists()
        upsert_chart_eligible(candidate_dir.name, chart_eligible)

        questions_data = json.loads(
            (proc_dst / "questions.json").read_text(encoding="utf-8")
        )
        AnalysisBankReceiver._update_procedures_index(
            candidate_dir.name,
            summary=questions_data.get("summary", "").strip(),
            chart_eligible=chart_eligible,
        )

        reset_retrieval_caches()
        shutil.rmtree(candidate_dir)
        print(f"ACCEPTED AND MERGED: {candidate_dir.name} -> {proc_dst}")
        return proc_dst

    @staticmethod
    def _rebuild_keyword_matrix() -> None:
        """Regenerate keyword_matrix.csv from the current procedures/.

        Imported lazily because the build script lives in scripts/, not
        in the package. We re-run it on every merge so Stage B never
        falls behind the bank — keyword_matrix.csv is otherwise easy to
        forget after a curated keywords.yaml edit.
        """
        from analysis_bank.features.keyword_index import (
            DEFAULT_KEYWORDS_PATH,
            DEFAULT_MATRIX_PATH,
        )
        # The builder script imports this module's helpers; importing it
        # under a private alias keeps it usable here without touching
        # sys.path the way the CLI entrypoint does.
        import importlib.util
        from analysis_bank.paths import REPO_ROOT
        script_path = REPO_ROOT / "scripts" / "build_keyword_matrix.py"
        spec = importlib.util.spec_from_file_location(
            "_build_keyword_matrix", script_path
        )
        if spec is None or spec.loader is None:
            logger.warning("Could not locate %s; skipping matrix rebuild", script_path)
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        n_rows, n_cats = module.build_matrix(
            PROCEDURES_DIR, DEFAULT_KEYWORDS_PATH, DEFAULT_MATRIX_PATH
        )
        logger.info("Rebuilt keyword_matrix.csv (%d rows × %d cats)", n_rows, n_cats)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, candidate_dir: Path) -> str:
        chart_py = candidate_dir / "chart.py"
        chart_line = (
            f"- chart.py: {chart_py} (OPTIONAL — present)\n"
            if chart_py.exists()
            else ""
        )
        return (
            f"Evaluate the candidate analysis bundle submission in this folder.\n\n"
            f"## Paths\n"
            f"- Candidate folder: {candidate_dir}\n"
            f"- procedure.sql: {candidate_dir / 'procedure.sql'}\n"
            f"- README.md: {candidate_dir / 'README.md'}\n"
            f"{chart_line}"
            f"\nSee your system prompt for what to judge. Respond with exactly "
            f"one VERDICT line. On REJECT, append the required "
            f"`## Suggested Changes` block.\n"
        )

    @staticmethod
    def _require_candidate_files(candidate_dir: Path) -> None:
        """Fail fast if the candidate is missing procedure.sql, README.md, or
        the chart contract.

        Chart contract — exactly one of:
          (a) chart.py AND chart_1.png (the rendered output of chart.py on
              the procedure's SAMPLE CALL); chart.py is then validated for
              signature, hardcoded-id, csv-read, and __main__ guard.
          (b) chart_skipped.md — a written rationale for why no chart applies
              (single scalar, narrative-only output, etc.).

        Either path makes downstream chart fidelity tractable: (a) gives the
        visualization_creator a callable to render_chart(**kwargs); (b) gives
        it explicit permission to skip without inventing a fresh chart from
        SQL (the failure mode the bank is being rearchitected to prevent).
        """
        for name in ("procedure.sql", "README.md"):
            if not (candidate_dir / name).exists():
                raise FileNotFoundError(
                    f"Candidate {candidate_dir.name} is missing {name}. Every "
                    f"candidate produced by promote_code() must include both "
                    f"procedure.sql and README.md at the top of the analysis_id "
                    f"folder."
                )

        AnalysisBankReceiver._validate_questions_json(candidate_dir)

        chart_py = candidate_dir / "chart.py"
        chart_png = candidate_dir / "chart_1.png"
        chart_skipped = candidate_dir / "chart_skipped.md"

        if chart_skipped.exists():
            rationale = chart_skipped.read_text(encoding="utf-8").strip()
            if len(rationale) < 10:
                raise ValueError(
                    f"chart_skipped.md in {candidate_dir.name} is empty or "
                    f"trivially short. Write a one-line rationale explaining "
                    f"why no chart applies (e.g., 'single scalar — no "
                    f"chart needed', 'narrative output — no plottable "
                    f"structure')."
                )
            return

        if not chart_py.exists():
            raise FileNotFoundError(
                f"Candidate {candidate_dir.name} has no chart contract: "
                f"missing both chart.py and chart_skipped.md. The bank gate "
                f"requires either (chart.py + chart_1.png) or a "
                f"chart_skipped.md rationale — see receiver._require_candidate_files."
            )
        if not chart_png.exists():
            raise FileNotFoundError(
                f"Candidate {candidate_dir.name} has chart.py but no "
                f"chart_1.png. The promotion broker must execute chart.py "
                f"on the procedure's SAMPLE CALL output and ship the rendered "
                f"PNG so the chart contract can be verified visually before "
                f"merge. If a chart genuinely doesn't apply, write "
                f"chart_skipped.md instead."
            )
        AnalysisBankReceiver._validate_chart_py(chart_py)

    @staticmethod
    def _validate_questions_json(candidate_dir: Path) -> None:
        """Require a well-formed questions.json — the dense retrieval encoder
        embeds these paraphrases at runtime, and the receiver writes the
        summary into procedures/_index.md on merge. A procedure without
        questions.json is invisible to Stage A recall.
        """
        path = candidate_dir / "questions.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Candidate {candidate_dir.name} is missing questions.json. "
                f"The promotion broker must emit {{summary, questions: [..]}} "
                f"with 8 paraphrases — see Step 3 of promotion_broker.md. "
                f"Without it, dense retrieval has no embedding surface for "
                f"this procedure."
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"questions.json in {candidate_dir.name} is not valid JSON: {e}"
            ) from e
        summary = (data.get("summary") or "").strip()
        questions = data.get("questions") or []
        if not summary:
            raise ValueError(
                f"questions.json in {candidate_dir.name} has no `summary`. "
                f"This is the 1-line headline that lands in _index.md."
            )
        if not isinstance(questions, list) or len(questions) < 4:
            raise ValueError(
                f"questions.json in {candidate_dir.name} needs at least 4 "
                f"paraphrases under `questions` (got {len(questions)}). "
                f"Eight is the target — fewer than four leaves dense recall "
                f"with too sparse a signal."
            )
        if any(not isinstance(q, str) or not q.strip() for q in questions):
            raise ValueError(
                f"questions.json in {candidate_dir.name} contains empty or "
                f"non-string entries in `questions`."
            )

    @staticmethod
    def _update_procedures_index(
        analysis_id: str, *, summary: str, chart_eligible: bool
    ) -> None:
        """Upsert one row into procedures/_index.md. Idempotent.

        The index is a plain Markdown table — `id | summary | chart_eligible`.
        Hand-maintained today; auto-maintained going forward so it stops
        rotting whenever a procedure is promoted.
        """
        header = "| analysis_id | summary | chart_eligible |\n|---|---|---|\n"
        clean_summary = " ".join(summary.split())  # collapse whitespace
        new_row = f"| {analysis_id} | {clean_summary} | {str(chart_eligible).lower()} |\n"

        if not PROCEDURES_INDEX_PATH.exists():
            PROCEDURES_INDEX_PATH.write_text(header + new_row, encoding="utf-8")
            return

        text = PROCEDURES_INDEX_PATH.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        # Drop any existing row for this analysis_id (upsert)
        kept = [ln for ln in lines if not ln.lstrip().startswith(f"| {analysis_id} |")]
        # Ensure header present
        if not any(ln.lstrip().startswith("| analysis_id ") for ln in kept):
            kept = [header.splitlines(keepends=True)[0],
                    header.splitlines(keepends=True)[1]] + kept
        # Append new row, sort body rows alphabetically by id for stable diffs
        head = kept[:2]
        body = [ln for ln in kept[2:] if ln.strip().startswith("|")]
        body.append(new_row)
        body.sort()
        PROCEDURES_INDEX_PATH.write_text("".join(head + body), encoding="utf-8")

    @staticmethod
    def _validate_chart_py(chart_py: Path) -> None:
        """Smoke-import + signature + hardcoded-id + CSV-read checks for chart.py."""
        text = chart_py.read_text(encoding="utf-8")

        # Hardcoded-id sweep: account_id / entity_l1_id / brand_id / promotion_id
        # equality assignments at module/function scope. Same intent as the
        # broker's parameterization rule. The `\b` prevents false positives
        # against the parameter names themselves (e.g. `v_account_id=45` in
        # the __main__ block, where `account_id` is only a substring).
        hardcoded = re.findall(
            r"\b(?:account_id|entity_l1_id|brand_id|promotion_id|campaign_id)\s*=\s*\d+",
            text,
        )
        if hardcoded:
            raise ValueError(
                f"chart.py in {chart_py.parent.name} hardcodes entity/account "
                f"IDs ({hardcoded[:3]}). Every case-specific value must be a "
                f"function parameter so the chart generalizes."
            )

        # CSV-read sweep: chart.py must hit the live procedure via iq.query, not
        # re-render a frozen CSV. The source notebook reads CSV because it
        # snapshots one case; the promoted callable has to work for any future
        # case's args, so reading from disk defeats the point.
        csv_reads = re.findall(
            r"pd\.read_csv\s*\(|(?<!\w)read_csv\s*\(|open\s*\([^)]*\.csv",
            text,
        )
        if csv_reads:
            raise ValueError(
                f"chart.py in {chart_py.parent.name} reads from CSV "
                f"({csv_reads[:3]}). The promoted chart must load data via "
                f"`iq.query(\"CALL <proc>(...)\")` so it works for any future "
                f"case — not from a frozen CSV path tied to the source bundle."
            )

        # __main__ guard sweep: chart.py must be runnable as `python chart.py`
        # to reproduce the source PNG. Without an `if __name__ == "__main__":`
        # block calling render_chart with the SAMPLE CALL args, running the
        # file does nothing and the operator can't smoke-test the chart.
        if not re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]", text):
            raise ValueError(
                f"chart.py in {chart_py.parent.name} is missing an "
                f"`if __name__ == \"__main__\":` block. The broker must "
                f"append one that calls render_chart with the SAMPLE CALL "
                f"args from procedure.sql, so `python chart.py` reproduces "
                f"the source PNG."
            )

        # Smoke import — catch syntax errors before the inspector runs.
        spec = importlib.util.spec_from_file_location(
            f"_chart_{chart_py.parent.name}", chart_py
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load chart.py from {chart_py}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise ValueError(
                f"chart.py in {chart_py.parent.name} failed to import: "
                f"{type(e).__name__}: {e}"
            ) from e

        # Signature check — find a callable function with at least one
        # required positional arg. Prefer `render_chart`, but accept any
        # such function so the broker has flexibility.
        candidates = [
            (name, obj) for name, obj in vars(module).items()
            if callable(obj)
            and not name.startswith("_")
            and getattr(obj, "__module__", None) == module.__name__
        ]
        chart_fn = next(
            (obj for name, obj in candidates if name == "render_chart"), None
        )
        if chart_fn is None and candidates:
            chart_fn = candidates[0][1]
        if chart_fn is None:
            raise ValueError(
                f"chart.py in {chart_py.parent.name} defines no callable "
                f"function. Expected a `render_chart(...)` (or similar)."
            )
        sig = inspect.signature(chart_fn)
        required = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        if not required:
            raise ValueError(
                f"chart.py in {chart_py.parent.name}: `{chart_fn.__name__}` "
                f"has no required positional args. The procedure parameters "
                f"must be required args so callers cannot accidentally render "
                f"the wrong case."
            )

    def _load_inspector_prompt(self) -> str:
        if self._prompt is None:
            self._prompt = INSPECTOR_PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt

    def _parse_verdict(self, candidate_name: str, agent_output: str) -> ReceiverVerdict:
        for line in agent_output.splitlines():
            stripped = line.strip()
            if stripped.startswith("VERDICT:"):
                parts = stripped.split("—", 1)
                verdict_part = parts[0].replace("VERDICT:", "").strip()
                reason = parts[1].strip() if len(parts) > 1 else ""
                if verdict_part in ("ACCEPT", "REJECT"):
                    return ReceiverVerdict(
                        candidate=candidate_name,
                        verdict=verdict_part,
                        reason=reason,
                    )
        # No structured verdict found → treat as REJECT so we don't silently
        # merge garbage. Operator can inspect the full output.
        logger.warning("Could not parse structured verdict for %s", candidate_name)
        return ReceiverVerdict(
            candidate=candidate_name,
            verdict="REJECT",
            reason=(
                f"Inspector did not produce a structured VERDICT line. "
                f"Full output:\n{agent_output}"
            ),
        )

    @staticmethod
    def _print_verdict(verdict: ReceiverVerdict) -> None:
        symbol = "+" if verdict.verdict == "ACCEPT" else "x"
        print(f"  [{symbol}] {verdict.candidate}: {verdict.verdict} — {verdict.reason}")

    @staticmethod
    def _print_reject_details(agent_output: str) -> None:
        """Print the inspector's `## Suggested Changes` block, if present."""
        idx = agent_output.find("## Suggested Changes")
        if idx < 0:
            return
        block = agent_output[idx:].strip()
        print("\n      Suggested Changes:")
        for line in block.splitlines():
            print(f"      {line}")
        print()

    # ------------------------------------------------------------------
    # Discard
    # ------------------------------------------------------------------

    def discard(self, candidate_name: str) -> None:
        """Delete a single candidate folder from ``candidates/``."""
        candidate_dir = CANDIDATES_DIR / candidate_name
        if not candidate_dir.is_dir():
            raise FileNotFoundError(f"Candidate folder not found: {candidate_dir}")
        shutil.rmtree(candidate_dir)
        print(f"Discarded {candidate_name}")

    def discard_all(self) -> int:
        """Delete every candidate folder from ``candidates/``."""
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
