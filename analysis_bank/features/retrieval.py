"""Hybrid 5-stage retrieval over the analysis bank.

    Stage A — HyDE (optional): an LLM drafts a short synthetic README for
        the question; we encode it and average with the raw question
        vector. The hypothesis: a hypothetical document lives closer to
        real procedure READMEs in BGE space than the bare question does.

    Stage B — Dense recall (top 20)
        BGE-large embeddings over per-procedure question paraphrases
        (questions.json, max-pooled). Question vector vs procedure
        vectors → cosine top-20. Embeddings are persisted at intake
        (see analysis_bank.features.embeddings).

    Stage C — Sparse recall (top 20)
        BM25 over the curated keyword categories in keyword_matrix.csv.
        Catches literal-string matches that dense drift past.

    Stage D — Cross-encoder rerank (top 5)
        Union the two top-20 lists, dedupe, cross-encoder rank with
        BGE-reranker-large.

    Stage E — LLM fitness (final filter)
        For each of the top 5, an LLM call reads the procedure README
        and the question spec and emits {STRONG | WEAK | REJECT} plus
        a one-line rationale.

The output is a list of `Candidate(analysis_id, chart_eligible,
fitness_label, rationale, ...)` ready for breakdown_ask to write into
questions_to_answer.md.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from analysis_bank.features.embeddings import encode as _encode_lines, load_corpus
from analysis_bank.features.keyword_index import rank_question
from analysis_bank.features.registry import load_chart_eligibility
from analysis_bank.paths import PROCEDURES_DIR


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


FitnessLabel = str  # one of "STRONG" | "WEAK" | "REJECT"


@dataclass
class Candidate:
    """Final retrieval candidate handed to plan_analysis."""

    analysis_id: str
    chart_eligible: bool
    fitness_label: FitnessLabel
    rationale: str
    dense_score: float | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None
    matched_categories: list[str] = field(default_factory=list)
    path_to_readme: Path | None = None


# ---------------------------------------------------------------------------
# Cached models / corpus
# ---------------------------------------------------------------------------


_RERANK_MODEL = None  # CrossEncoder instance once loaded
_CORPUS: dict[str, np.ndarray] | None = None


def _rerank_model():
    global _RERANK_MODEL
    if _RERANK_MODEL is None:
        from sentence_transformers import CrossEncoder
        name = os.environ.get(
            "ANALYSIS_BANK_RERANK_MODEL", "BAAI/bge-reranker-large"
        )
        logger.info("Loading cross-encoder %s …", name)
        _RERANK_MODEL = CrossEncoder(name)
    return _RERANK_MODEL


def _corpus(procedures_dir: Path) -> dict[str, np.ndarray]:
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = load_corpus(procedures_dir, encode_missing=True)
    return _CORPUS


def reset_caches() -> None:
    """Drop the in-process caches. Useful for tests and after intake.

    Receiver._merge_accepted calls this so a freshly promoted procedure
    is visible to retrieval without restarting the process.
    """
    global _RERANK_MODEL, _CORPUS
    _CORPUS = None
    # The cross-encoder is expensive to reload; keep it cached.


# ---------------------------------------------------------------------------
# Stage A — HyDE
# ---------------------------------------------------------------------------


_HYDE_SYSTEM_PROMPT = """\
You draft a short, plausible README that a stored procedure answering
the user's question would carry. The reader (an embedding model) needs
the README to look like a real bank entry — concrete, technical, and
focused on the data shape, grain, columns, and chart pattern that the
ideal procedure would produce.

Write 4–8 sentences. No headings, no bullets, no preamble. Mention
table-style data sources only at a high level (e.g., "ad attribution
table", "promo redemption events") — do NOT invent column names from
the Instacart Ads schema. The point is to land in the right
neighborhood of embedding space, not to specify a real implementation.
"""


async def _draft_hyde(question_text: str) -> str | None:
    """Ask the LLM to draft a synthetic README for the question.

    Returns the synthetic text, or None if the SDK isn't available
    (HyDE is then skipped and Stage B falls back to encoding the raw
    question text directly).
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore
    except ImportError:
        logger.info("claude_agent_sdk unavailable; skipping HyDE")
        return None

    captured = ""
    sdk_env = {"CLAUDECODE": ""}
    async for message in query(
        prompt=f"Question:\n{question_text.strip()}\n",
        options=ClaudeAgentOptions(
            system_prompt=_HYDE_SYSTEM_PROMPT,
            allowed_tools=[],
            model="haiku",
            permission_mode="bypassPermissions",
            max_turns=2,
            env=sdk_env,
        ),
    ):
        if hasattr(message, "result") and message.result:
            captured = message.result
    text = (captured or "").strip()
    return text or None


# ---------------------------------------------------------------------------
# Stage B — Dense recall (BGE)
# ---------------------------------------------------------------------------


def _dense_recall(
    query_vector: np.ndarray,
    procedures_dir: Path,
    top_k: int,
) -> list[tuple[str, float]]:
    """Return [(analysis_id, max-pool cosine)] top-k.

    Procedures are indexed by max-pooled cosine over their paraphrase
    matrix — a procedure ranks high if any of its 8 paraphrases (or
    summary) is close to the query.
    """
    corpus = _corpus(procedures_dir)
    if not corpus or query_vector.size == 0:
        return []
    scored: list[tuple[str, float]] = []
    for aid, matrix in corpus.items():
        if matrix.shape[1] != query_vector.shape[0]:
            logger.warning(
                "Embedding dim mismatch for %s (corpus=%d, query=%d) — re-encode the bank",
                aid, matrix.shape[1], query_vector.shape[0],
            )
            continue
        sims = matrix @ query_vector  # both L2-normalized → dot == cosine
        scored.append((aid, float(sims.max())))
    scored.sort(key=lambda t: -t[1])
    return scored[:top_k]


def _build_query_vector(
    question_text: str,
    hyde_text: str | None,
) -> np.ndarray:
    """Encode question + optional HyDE doc, return one averaged vector.

    Averaging two L2-normalized vectors is not itself unit-norm, so we
    re-normalize before returning. Empirically the average outperforms
    using HyDE alone — the raw question keeps the embedding tied to the
    actual phrasing the user wrote.
    """
    if hyde_text:
        matrix = _encode_lines([question_text, hyde_text])
        avg = matrix.mean(axis=0)
    else:
        matrix = _encode_lines([question_text])
        avg = matrix[0]
    norm = np.linalg.norm(avg)
    if norm > 0:
        avg = avg / norm
    return avg.astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# Stage D — Cross-encoder rerank
# ---------------------------------------------------------------------------


def _readme_summary(procedures_dir: Path, aid: str, max_chars: int = 800) -> str:
    """Pull a short summary string for the cross-encoder input."""
    qjson = procedures_dir / aid / "questions.json"
    if qjson.exists():
        try:
            import json
            data = json.loads(qjson.read_text(encoding="utf-8"))
            summary = (data.get("summary") or "").strip()
            if summary:
                return summary[:max_chars]
        except Exception:
            pass
    readme = procedures_dir / aid / "README.md"
    if readme.exists():
        return readme.read_text(encoding="utf-8", errors="replace")[:max_chars]
    return aid


def _rerank(
    question_text: str,
    candidate_ids: list[str],
    procedures_dir: Path,
    top_k: int,
) -> list[tuple[str, float]]:
    """Cross-encoder rerank. Identity ordering if there's nothing to rank."""
    if not candidate_ids:
        return []
    pairs = [(question_text, _readme_summary(procedures_dir, aid)) for aid in candidate_ids]
    scores = _rerank_model().predict(pairs).tolist()
    ranked = sorted(zip(candidate_ids, scores), key=lambda t: -t[1])
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# Stage E — LLM fitness
# ---------------------------------------------------------------------------


_FITNESS_SYSTEM_PROMPT = """\
You judge whether a bank-retrieved analysis procedure can answer a given
question shape via *chart-pattern reuse*.

Read the question spec and the procedure README, then emit exactly one of:

    STRONG — chart family AND output table schema both fit the question.
             That means the procedure's chart pattern (the visual it
             produces) is the right answer for this question, AND the
             output table the SQL emits has the same column shape and
             grain (e.g. cohort × time-bucket wide table with cumulative
             metrics) the chart contract expects. Cohort definition,
             time-axis labels, filter axes, and upstream CTEs may need
             parameter swaps or one-for-one CTE replacements (e.g. swap
             clicker-cohort CTE for an NTB-cohort CTE) — those are still
             STRONG, because chart.py operates on the output schema, not
             on which CTE produced it. The README's "When to use" /
             "Question shapes this procedure answers" sections are
             authoritative on what swaps are in-scope.
    WEAK   — chart family fits but the output table schema needs new
             columns, fundamentally different grain, or a restructured
             aggregation that the existing chart.py cannot render
             without changes. Reuse-with-parameter-swap is insufficient.
    REJECT — different chart family / different analytical idea
             altogether; retrieving it was a recall mistake.

The bar for STRONG is "could a competent SQL engineer reuse this
procedure by swapping parameters and at most a single upstream CTE,
keeping the output table schema and chart.py untouched?" If yes →
STRONG. If they would need to add columns, change the grain of the
output table, or rewrite the aggregation logic → WEAK.

Output format (one line, no preamble, no markdown):

    LABEL — one-sentence rationale citing the schema/chart fit.
"""

_LENIENCY_BLOCK = """
LENIENCY MODE IS ON (default).
Do NOT downgrade to WEAK or REJECT solely because the procedure's metric
formula differs from the question's phrasing. Metric-domain compatibility
is sufficient:
- ROAS and CAC are both "efficiency over time" → STRONG if chart family fits.
- Cumulative repeat rate and repeat frequency are both "retention metrics" → STRONG.
Only downgrade to WEAK for: (a) fundamentally different chart family, or
(b) the procedure has NO column in the required metric *category* at all
(e.g., zero spend columns when the question is purely about spend).
"""


async def _fitness_one(
    question_text: str,
    aid: str,
    readme_text: str,
    lenient: bool = True,
) -> tuple[FitnessLabel, str]:
    """Run the fitness check for a single (question, procedure) pair."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore
    except ImportError:
        return ("WEAK", "claude_agent_sdk not installed; fitness check skipped")

    prompt = (
        f"## Question spec\n{question_text.strip()}\n\n"
        f"## Procedure README ({aid})\n{readme_text.strip()[:4000]}\n\n"
        f"Emit `LABEL — rationale` per the rules in your system prompt."
    )
    captured = ""
    sdk_env = {"CLAUDECODE": ""}
    system_prompt = _FITNESS_SYSTEM_PROMPT + _LENIENCY_BLOCK if lenient else _FITNESS_SYSTEM_PROMPT
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=[],
            model="opus",
            permission_mode="bypassPermissions",
            max_turns=2,
            env=sdk_env,
        ),
    ):
        if hasattr(message, "result") and message.result:
            captured = message.result
    return _parse_fitness(captured)


def _parse_fitness(raw: str) -> tuple[FitnessLabel, str]:
    raw = (raw or "").strip()
    if not raw:
        return ("WEAK", "fitness agent returned no output")
    first_line = raw.splitlines()[0].strip()
    for label in ("STRONG", "WEAK", "REJECT"):
        if first_line.upper().startswith(label):
            tail = first_line[len(label):].lstrip(" —-:")
            return (label, tail or "(no rationale)")
    return ("WEAK", f"unparseable fitness output: {first_line[:200]}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def aretrieve(
    question_text: str,
    *,
    top_k_dense: int = 20,
    top_k_sparse: int = 20,
    top_k_rerank: int = 5,
    procedures_dir: Path | None = None,
    require_chart_eligible: bool = False,
    skip_llm_fitness: bool = False,
    skip_hyde: bool = False,
    force_top_1: bool = False,
    lenient: bool = True,
) -> list[Candidate]:
    """Run the full hybrid retrieval pipeline.

    Args:
        question_text: the question spec (grain, columns, headline metric)
            written by breakdown_ask.
        top_k_dense / top_k_sparse / top_k_rerank: per-stage caps.
        procedures_dir: defaults to the bank's procedures/.
        require_chart_eligible: drop chart-ineligible candidates before
            Stage E (used under existing_analysis_only=True).
        skip_llm_fitness: bypass Stage E. Every candidate gets WEAK with
            "fitness skipped". Used by the eval harness.
        skip_hyde: bypass Stage A. Speeds the eval harness up; in
            production breakdown_ask leaves it on.
        force_top_1: skip Stage E entirely; return only the top-ranked
            candidate with fitness_label="FORCED". Overrides lenient.
        lenient: when True (default), append a leniency block to the
            fitness system prompt so metric-formula differences alone do
            not block STRONG. When False, strict metric matching applies.

    Returns up to ``top_k_rerank`` candidates with fitness labels.
    """
    if procedures_dir is None:
        import analysis_bank.features.retrieval as _self  # honor monkeypatch
        procedures_dir = _self.PROCEDURES_DIR

    chart_flags = load_chart_eligibility()

    # Stage A — HyDE (optional)
    hyde_text = None if skip_hyde else await _draft_hyde(question_text)

    # Stage B — Dense
    query_vec = _build_query_vector(question_text, hyde_text)
    dense_hits = _dense_recall(query_vec, procedures_dir, top_k_dense)
    dense_scores = {aid: s for aid, s in dense_hits}

    # Stage C — Sparse
    sparse_hits = rank_question(question_text, top_k=top_k_sparse)
    sparse_scores = {h.analysis_id: h.bm25_score for h in sparse_hits}
    sparse_categories = {h.analysis_id: h.matched_categories for h in sparse_hits}

    # Union → dedupe → optional chart gate
    union_ids = list(dict.fromkeys([aid for aid, _ in dense_hits] + list(sparse_scores.keys())))
    if require_chart_eligible:
        union_ids = [aid for aid in union_ids if chart_flags.get(aid, False)]

    if not union_ids:
        return []

    # Stage D — Rerank (cap to 1 when force_top_1 to avoid wasted cross-encoder calls)
    rerank_k = 1 if force_top_1 else top_k_rerank
    reranked = _rerank(question_text, union_ids, procedures_dir, rerank_k)

    # Stage E — Fitness (skipped entirely when force_top_1)
    candidates: list[Candidate] = []
    for aid, rerank_score in reranked:
        readme_path = procedures_dir / aid / "README.md"
        readme_text = readme_path.read_text(encoding="utf-8", errors="replace") if readme_path.exists() else ""
        if force_top_1:
            label, rationale = ("FORCED", "force_top_1=True; fitness bypassed")
        elif skip_llm_fitness:
            label, rationale = ("WEAK", "fitness skipped")
        else:
            label, rationale = await _fitness_one(question_text, aid, readme_text, lenient=lenient)
        candidates.append(
            Candidate(
                analysis_id=aid,
                chart_eligible=chart_flags.get(aid, False),
                fitness_label=label,
                rationale=rationale,
                dense_score=dense_scores.get(aid),
                bm25_score=sparse_scores.get(aid),
                rerank_score=rerank_score,
                matched_categories=sparse_categories.get(aid, []),
                path_to_readme=readme_path,
            )
        )

    if force_top_1:
        return candidates  # single FORCED candidate, no REJECT filter
    return [c for c in candidates if c.fitness_label != "REJECT"]


def retrieve(
    question_text: str,
    **kwargs,
) -> list[Candidate]:
    """Sync wrapper around :func:`aretrieve`. Jupyter-safe."""
    from analysis_bank._async import run_sync
    return run_sync(aretrieve(question_text, **kwargs))
