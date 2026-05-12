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
        BGE-reranker-large. The rerank pair is
        ``(question, summary + top-3 paraphrases)`` — pairing against
        the summary alone misses paraphrase-level signal that the
        questions.json was authored to carry.

    Stage E — LLM panel jury (final filter)
        ONE LLM call reads the question recap and EVERY top-K
        candidate's README + procedure.sql, then emits per-candidate
        STRONG/WEAK/REJECT labels AND nominates a single winner. The
        panel design lets the jury compare candidates side-by-side
        instead of judging in isolation, which is where single-pass
        per-candidate fitness used to drift.

The output is a list of ``Candidate(analysis_id, chart_eligible,
fitness_label, rationale, is_jury_winner, ...)`` ready for breakdown_ask
to write into questions_to_answer.md.
"""

from __future__ import annotations

import json
import logging
import os
import warnings
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
    is_jury_winner: bool = False


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


def _rerank_input(
    procedures_dir: Path,
    aid: str,
    *,
    n_paraphrases: int = 3,
    max_chars: int = 1600,
) -> str:
    """Build the candidate-side string for the cross-encoder pair.

    History: this used to return only ``questions.json["summary"]``
    (800 chars). That left the rerank starved of paraphrase signal —
    Mary Ruth Run 7 Q1 picked the wrong procedure because the winning
    procedure's summary was abstract ("by date bucket") while the
    losing procedure's summary contained the exact lexical match
    ("months-since-acquisition") even though its cadence was wrong.

    Including a few paraphrases gives the cross-encoder the same
    surface-form coverage that questions.json was authored to provide
    for dense recall. Falls back to README first chars if questions.json
    is missing or unreadable.
    """
    qjson = procedures_dir / aid / "questions.json"
    if qjson.exists():
        try:
            data = json.loads(qjson.read_text(encoding="utf-8"))
            summary = (data.get("summary") or "").strip()
            paraphrases = [
                q.strip()
                for q in (data.get("questions") or [])
                if isinstance(q, str) and q.strip()
            ][:n_paraphrases]
            parts = [p for p in [summary, *paraphrases] if p]
            if parts:
                return "\n".join(parts)[:max_chars]
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
    pairs = [(question_text, _rerank_input(procedures_dir, aid)) for aid in candidate_ids]
    scores = _rerank_model().predict(pairs).tolist()
    ranked = sorted(zip(candidate_ids, scores), key=lambda t: -t[1])
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# Stage E — LLM panel jury
# ---------------------------------------------------------------------------


_PANEL_JURY_SYSTEM_PROMPT = """\
You are a panel jury that judges whether bank-retrieved analysis
procedures can answer a given question via *chart-pattern reuse*. You
read the question recap and EVERY shortlisted candidate's README and
procedure.sql side-by-side, then emit per-candidate labels and
nominate a single winner.

# Output format (STRICT — downstream parsers depend on this layout)

```
## Question recap
<one-paragraph restatement of the question, in your own words. Quote
 the literal retrieval spec at the end so the per-candidate analysis
 below can be checked against it.>

## Per-candidate analysis

### <analysis_id>
- Label: STRONG | WEAK | REJECT
- Output schema: <does the SQL emit the columns/grain the question needs?>
- Chart family: <does the chart-pattern match the question's required visual?>
- Parameter shape: <does the SAMPLE CALL accept the IDs/dates the question carries?>
- Cohort/CTE swap distance: <how surgical would the swap be — drop-in CTE replacement, or rewrite?>
- Rationale: <one sentence summarizing the fit>

(repeat for every candidate)

## Decision
- Winner: <analysis_id>
- Why it beats the others: <2–4 sentences comparing the winner to the runners-up on the axes above>
```

# Label rules

STRONG — chart family AND output table schema both fit the question.
         Parameter swaps and one-for-one CTE replacements are still
         STRONG; chart.py operates on the output schema, not on which
         CTE produced it.
WEAK   — chart family fits but the output schema needs new columns,
         a fundamentally different grain, or a restructured aggregation.
         Reuse-with-parameter-swap is insufficient.
REJECT — different chart family / different analytical idea altogether.

# Winner rules

You MUST nominate exactly one winner — even when every candidate is
WEAK. The winner is the candidate that requires the smallest delta to
answer the question. Downstream code uses the winner as the REUSE pick;
the per-candidate label remains the truth about how clean that reuse
is. Do NOT decline to pick.

# Comparison axes (use these to break ties between similarly-labelled candidates)

1. Parameter shape match — a procedure whose SAMPLE CALL accepts the
   IDs the question carries (e.g., account_id when the plan resolves
   campaigns dynamically) beats one that requires upstream resolution
   (e.g., a hardcoded campaign_ids comma-separated list).
2. Default cadence match — a procedure whose default bucket cadence
   matches the question's required cadence beats one that requires
   parameter swap, even if both support swaps.
3. Cohort-CTE swap distance — a procedure whose existing cohort CTE
   matches the question's cohort definition beats one that needs a
   one-for-one CTE replacement.
4. Output column literalness — a procedure whose output column names
   match what the question asks beats one that requires renaming.

When axes disagree, the parameter-shape axis wins (it's the most
expensive mismatch to bridge in downstream SQL).
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


def _read_proc_sql(procedures_dir: Path, aid: str, max_chars: int = 8000) -> str:
    p = procedures_dir / aid / "procedure.sql"
    if not p.exists():
        return "(procedure.sql not found)"
    return p.read_text(encoding="utf-8", errors="replace")[:max_chars]


def _read_proc_readme(procedures_dir: Path, aid: str, max_chars: int = 6000) -> str:
    p = procedures_dir / aid / "README.md"
    if not p.exists():
        return "(README.md not found)"
    return p.read_text(encoding="utf-8", errors="replace")[:max_chars]


async def _panel_jury(
    question_text: str,
    candidate_aids: list[str],
    procedures_dir: Path,
    *,
    lenient: bool = True,
) -> tuple[dict[str, tuple[FitnessLabel, str]], str | None, str]:
    """Run a single panel-jury LLM call across every candidate.

    Returns:
        per_candidate: {aid: (label, one-line rationale)}
        winner_aid:    the nominated winner (or None if parsing failed)
        verbose_text:  the jury's full markdown output (for _debug log)
    """
    if not candidate_aids:
        return ({}, None, "")

    try:
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore
    except ImportError:
        # SDK unavailable: emit a degraded result so callers don't hard-fail.
        # Picks the first candidate as winner, labels everything WEAK.
        per = {aid: ("WEAK", "claude_agent_sdk not installed; jury skipped") for aid in candidate_aids}
        return (per, candidate_aids[0], "(jury skipped — SDK unavailable)")

    candidate_blocks: list[str] = []
    for aid in candidate_aids:
        readme = _read_proc_readme(procedures_dir, aid)
        sql = _read_proc_sql(procedures_dir, aid)
        candidate_blocks.append(
            f"### {aid}\n\n"
            f"#### README.md\n```markdown\n{readme}\n```\n\n"
            f"#### procedure.sql\n```sql\n{sql}\n```\n"
        )

    prompt = (
        f"## Question (verbatim retrieval spec)\n\n{question_text.strip()}\n\n"
        f"## Candidates ({len(candidate_aids)})\n\n"
        + "\n\n---\n\n".join(candidate_blocks)
        + "\n\nProduce the panel verdict per the format in your system prompt."
    )

    captured = ""
    sdk_env = {"CLAUDECODE": ""}
    system_prompt = _PANEL_JURY_SYSTEM_PROMPT + (_LENIENCY_BLOCK if lenient else "")
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

    verbose = (captured or "").strip()
    per_candidate, winner = _parse_panel_output(verbose, candidate_aids)
    return (per_candidate, winner, verbose)


def _parse_panel_output(
    raw: str,
    candidate_aids: list[str],
) -> tuple[dict[str, tuple[FitnessLabel, str]], str | None]:
    """Parse the panel jury's markdown output.

    Tolerant: missing labels default to WEAK, missing winner returns
    None (caller falls back to top-rerank). The verbose text is always
    persisted so an operator can debug parsing misses.
    """
    per: dict[str, tuple[FitnessLabel, str]] = {}
    winner: str | None = None
    if not raw:
        return ({aid: ("WEAK", "jury returned no output") for aid in candidate_aids}, None)

    # Find each candidate's block by `### <aid>` header.
    for aid in candidate_aids:
        header = f"### {aid}"
        idx = raw.find(header)
        if idx == -1:
            per[aid] = ("WEAK", "candidate block not found in jury output")
            continue
        # Pull the block until the next `### ` or `## ` header.
        rest = raw[idx + len(header):]
        next_h3 = rest.find("\n### ")
        next_h2 = rest.find("\n## ")
        cuts = [c for c in (next_h3, next_h2) if c != -1]
        block = rest[: min(cuts)] if cuts else rest

        label: FitnessLabel = "WEAK"
        rationale = "(no rationale parsed)"
        for line in block.splitlines():
            stripped = line.strip().lstrip("-* ").strip()
            if stripped.lower().startswith("label:"):
                tail = stripped.split(":", 1)[1].strip().upper()
                for L in ("STRONG", "WEAK", "REJECT"):
                    if tail.startswith(L):
                        label = L
                        break
            elif stripped.lower().startswith("rationale:"):
                rationale = stripped.split(":", 1)[1].strip() or rationale
        per[aid] = (label, rationale)

    # Find winner via `Winner: <aid>` line.
    for line in raw.splitlines():
        stripped = line.strip().lstrip("-* ").strip()
        if stripped.lower().startswith("winner:"):
            tail = stripped.split(":", 1)[1].strip()
            # Strip backticks/whitespace; the model sometimes formats the id.
            tail_clean = tail.strip("` ").split()[0] if tail else ""
            if tail_clean in candidate_aids:
                winner = tail_clean
                break

    return (per, winner)


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
    force_pick_one: bool = False,
    lenient: bool = True,
    run_dir: Path | None = None,
    question_index: int | None = None,
    # Deprecated alias — kept for one release cycle.
    force_top_1: bool | None = None,
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
        force_pick_one: the panel jury still runs on the full top-K. The
            ONLY effect is that the winning candidate is marked
            ``is_jury_winner=True`` and downstream plan_analysis is
            instructed to REUSE it unconditionally. The jury's per-
            candidate STRONG/WEAK/REJECT labels are still emitted so
            the operator can audit the gap (e.g., "REUSEd a WEAK
            candidate because force_pick_one was set").
        lenient: when True (default), append a leniency block to the
            jury system prompt so metric-formula differences alone do
            not block STRONG.
        run_dir: pipeline run directory. When provided, the panel
            jury's verbose markdown is written to
            ``run_dir/analyses/_debug/jury_q<n>.md`` for auditability.
        question_index: 1-based index used in the verbose log filename.
        force_top_1: DEPRECATED — alias for ``force_pick_one``. Kept for
            one release; emits a warning if used.

    Returns up to ``top_k_rerank`` candidates with fitness labels.
    """
    # Deprecation shim — accept the old name, prefer the new.
    if force_top_1 is not None:
        warnings.warn(
            "force_top_1 is deprecated; use force_pick_one instead. "
            "The new name reflects the actual contract: the jury still "
            "reasons over the full top-K, and the flag only forces "
            "downstream plan_analysis to commit to the winner.",
            DeprecationWarning,
            stacklevel=2,
        )
        force_pick_one = force_pick_one or bool(force_top_1)

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

    # Stage D — Rerank (always to top_k_rerank — force_pick_one no longer caps to 1)
    reranked = _rerank(question_text, union_ids, procedures_dir, top_k_rerank)
    reranked_aids = [aid for aid, _ in reranked]

    # Stage E — Panel jury (always runs unless skipped by the eval harness)
    if skip_llm_fitness:
        per_candidate = {aid: ("WEAK", "fitness skipped") for aid in reranked_aids}
        winner_aid = reranked_aids[0] if reranked_aids else None
        verbose = ""
    else:
        per_candidate, winner_aid, verbose = await _panel_jury(
            question_text,
            reranked_aids,
            procedures_dir,
            lenient=lenient,
        )
        # If parsing didn't recover a winner, fall back to top-rerank so
        # downstream never sees a missing pick.
        if winner_aid is None and reranked_aids:
            winner_aid = reranked_aids[0]
            logger.warning(
                "Panel jury did not nominate a parseable winner; falling "
                "back to top-rerank candidate %s", winner_aid,
            )

    # Persist verbose jury output for auditability.
    if verbose and run_dir is not None:
        debug_dir = Path(run_dir) / "analyses" / "_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        idx_label = question_index if question_index is not None else "_"
        out_path = debug_dir / f"jury_q{idx_label}.md"
        out_path.write_text(verbose, encoding="utf-8")

    candidates: list[Candidate] = []
    for aid, rerank_score in reranked:
        readme_path = procedures_dir / aid / "README.md"
        label, rationale = per_candidate.get(aid, ("WEAK", "no jury verdict"))
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
                is_jury_winner=(aid == winner_aid),
            )
        )

    # Under force_pick_one we keep every candidate (so plan_analysis sees
    # the audit trail next to the winner). Otherwise drop REJECTs.
    if force_pick_one:
        return candidates
    return [c for c in candidates if c.fitness_label != "REJECT" or c.is_jury_winner]


def retrieve(
    question_text: str,
    **kwargs,
) -> list[Candidate]:
    """Sync wrapper around :func:`aretrieve`. Jupyter-safe."""
    from analysis_bank._async import run_sync
    return run_sync(aretrieve(question_text, **kwargs))
