"""5-scorer Olympics jury ensemble for feature scoring.

Each call to ``score`` / ``score_question`` invokes the scoring agent **5
times in parallel**. For each feature, the 5 scores are sorted, the min and
max are dropped, and the middle 3 are averaged then rounded to the nearest
int. This trims out one accidental outlier in either direction without
collapsing onto a single agent's idiosyncrasy.

Both modes use the same scoring agent system prompt; the user prompt
switches the input section ("Score this question…" vs. "Score this
analysis…"). Output is a dict[feature_name, int].
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
from functools import lru_cache
from typing import Awaitable, Callable

from analysis_bank.features.registry import feature_columns
from analysis_bank.paths import FEATURE_DICT_PATH, SCORING_PROMPT_PATH


logger = logging.getLogger(__name__)


_JURY_SIZE = 5
_PER_SCORE_TIMEOUT_S = 240


@lru_cache(maxsize=1)
def _load_scoring_prompt() -> str:
    return SCORING_PROMPT_PATH.read_text(encoding="utf-8")


def _build_analysis_user_prompt(readme_text: str, sql_text: str) -> str:
    return (
        "Score this analysis. The input is a stored procedure with its README.\n\n"
        f"## Rubric\nRead the canonical 76-feature rubric first:\n  {FEATURE_DICT_PATH}\n\n"
        "## README.md\n"
        f"{readme_text.strip()}\n\n"
        "## procedure.sql\n"
        f"```sql\n{sql_text.strip()}\n```\n\n"
        "Now emit a JSON object as instructed by your system prompt: every one "
        "of the 76 feature keys, integer −5..+5, with a one-line rationale per "
        "feature."
    )


def _build_question_user_prompt(question_text: str, case_summary_text: str) -> str:
    return (
        "Score this question as if you were scoring the SQL that would best "
        "answer it — what would that SQL emphasize, scope, control for, or "
        "compute? Use the case summary for context (it explains the brand, the "
        "moment, and the analytical setting) but score the *question*, not the "
        "summary.\n\n"
        f"## Rubric\nRead the canonical 76-feature rubric first:\n  {FEATURE_DICT_PATH}\n\n"
        "## Question\n"
        f"{question_text.strip()}\n\n"
        "## Case summary (context only)\n"
        f"{case_summary_text.strip()}\n\n"
        "Now emit a JSON object as instructed by your system prompt: every one "
        "of the 76 feature keys, integer −5..+5, with a one-line rationale per "
        "feature."
    )


def _extract_json_object(text: str) -> dict:
    """Pull the first {...} JSON object out of an agent message.

    The scorer prompt asks for JSON wrapped in a fenced code block, but agents
    occasionally drop the fence or add prose. We scan for the first balanced
    ``{...}`` and json.loads it.
    """
    # Prefer fenced JSON blocks if present
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Fall back to the first top-level brace match
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    start = -1
                    continue
    raise ValueError(f"Could not extract a JSON object from agent output:\n{text}")


def _coerce_scores(raw: dict) -> dict[str, int]:
    """Map raw JSON {feature: score|{score, rationale}} → {feature: int}.

    Agents may either output flat ``{feature: int}`` or
    ``{feature: {"score": int, "rationale": "..."}}``. Accept both.
    Clamps to [-5, 5]. Skips unknown keys silently.
    """
    cols = set(feature_columns())
    out: dict[str, int] = {}
    for k, v in raw.items():
        if k not in cols:
            continue
        if isinstance(v, dict):
            v = v.get("score")
        if v is None:
            continue
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            continue
        if n < -5:
            n = -5
        if n > 5:
            n = 5
        out[k] = n
    return out


async def _one_scorer(user_prompt: str) -> dict[str, int]:
    """Single scorer call — returns one juror's {feature: int} verdict."""
    from claude_agent_sdk import ClaudeAgentOptions, query

    system_prompt = _load_scoring_prompt()
    captured = ""
    sdk_env = {"CLAUDECODE": ""}
    async for message in query(
        prompt=user_prompt,
        options=ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read"],
            model="opus",
            permission_mode="bypassPermissions",
            max_turns=8,
            env=sdk_env,
        ),
    ):
        if hasattr(message, "result") and message.result:
            captured = message.result
    if not captured:
        raise RuntimeError("Scoring agent returned no result.")
    raw = _extract_json_object(captured)
    return _coerce_scores(raw)


async def _jury(user_prompt: str, jury_size: int = _JURY_SIZE) -> dict[str, int]:
    """Run the Olympics jury and return ensembled {feature: int}.

    Strategy: 5 parallel scorer calls; for each feature, sort the 5 scores,
    drop min and max, average the middle 3, round to int. Features that
    fewer than 3 jurors managed to score are skipped from the output (they
    will read as 0 in the registry vector — see `registry.upsert_row`).
    """
    tasks = [
        asyncio.wait_for(_one_scorer(user_prompt), timeout=_PER_SCORE_TIMEOUT_S)
        for _ in range(jury_size)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    jurors: list[dict[str, int]] = []
    for r in results:
        if isinstance(r, BaseException):
            logger.warning("Scoring juror failed: %s: %s", type(r).__name__, r)
            continue
        jurors.append(r)
    if len(jurors) < 3:
        # Olympics rule needs ≥3 surviving votes per feature; refuse to fake it.
        raise RuntimeError(
            f"Only {len(jurors)} of {jury_size} scoring jurors returned a "
            f"usable verdict; cannot run Olympics scoring with fewer than 3."
        )

    final: dict[str, int] = {}
    for feature in feature_columns():
        votes = [j[feature] for j in jurors if feature in j]
        if len(votes) < 3:
            continue
        votes.sort()
        # Drop top + bottom; average the middle votes (3 of 5; or middle of
        # however many survived if some jurors failed).
        if len(votes) >= 5:
            middle = votes[1:-1]
        elif len(votes) == 4:
            middle = votes[1:-1]  # drop one of each end → middle 2
        else:  # 3
            middle = votes
        final[feature] = int(round(statistics.fmean(middle)))

    # Sanity floor: if the jury collectively scored fewer than half the rubric,
    # the output isn't usable for retrieval (downstream cosine collapses to 0
    # and Euclidean ranks the smallest-magnitude corpus entry as "nearest" —
    # silently bogus). Most often this means agents matched a preview JSON
    # block instead of the real one. Refuse to fake a score vector.
    min_features = len(feature_columns()) // 2
    if len(final) < min_features:
        raise RuntimeError(
            f"Jury produced scores for only {len(final)} of "
            f"{len(feature_columns())} features (need ≥{min_features}); "
            f"likely the scoring agents returned malformed or partial JSON. "
            f"Inspect raw juror outputs and rerun."
        )
    return final


async def score(readme_text: str, sql_text: str) -> dict[str, int]:
    """5-jury score of an analysis (README + procedure.sql).

    Returns ``{feature_name: int}`` covering the 76 features in
    ``feature_columns()``.
    """
    return await _jury(_build_analysis_user_prompt(readme_text, sql_text))


async def score_question(question_text: str, case_summary_text: str) -> dict[str, int]:
    """5-jury score of an analyst question, with case summary as context.

    Returns ``{feature_name: int}`` covering the 76 features in
    ``feature_columns()``.
    """
    return await _jury(_build_question_user_prompt(question_text, case_summary_text))
