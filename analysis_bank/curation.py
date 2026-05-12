"""Bank curation helpers for hand-tuning retrieval coverage.

This module exists because the right time to add a question paraphrase
to a procedure's ``questions.json`` is the moment you notice retrieval
missed. Doing it by hand requires three coordinated edits — append to
``questions.json``, refresh ``embeddings.npy``, refresh
``keyword_matrix.csv`` — and any one of those being skipped silently
degrades retrieval until the next full re-index.

``add_question`` does all three atomically and returns a summary so
callers can confirm what landed.

Why this lives in the package (not a slash command): the same operation
should be callable from a notebook, a CLI script, a slash command, or a
post-run hook. Putting the logic here keeps a single source of truth;
wrapper UIs are free to add keystroke convenience without re-
implementing the workflow.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from analysis_bank.features import embeddings as _emb
from analysis_bank.features import retrieval as _retrieval
from analysis_bank.features.keyword_index import DEFAULT_KEYWORDS_PATH
from analysis_bank.paths import PROCEDURES_DIR, REPO_ROOT


logger = logging.getLogger(__name__)


DEFAULT_DEDUP_THRESHOLD = 0.95


@dataclass
class AddQuestionResult:
    analysis_id: str
    appended: bool
    reason: str
    n_questions_before: int
    n_questions_after: int
    nearest_existing: str | None = None
    nearest_similarity: float | None = None


def _load_questions(qjson_path: Path) -> dict:
    if not qjson_path.exists():
        raise FileNotFoundError(
            f"add_question: {qjson_path} missing — is {qjson_path.parent.name} a real procedure?"
        )
    return json.loads(qjson_path.read_text(encoding="utf-8"))


def _max_cosine_against_existing(
    new_question: str,
    existing_questions: list[str],
) -> tuple[float, str] | None:
    """Encode the new question against existing paraphrases, return
    ``(max_cosine, nearest_existing)`` or ``None`` if there are no
    existing paraphrases yet.
    """
    if not existing_questions:
        return None
    matrix = _emb.encode([new_question, *existing_questions])
    if matrix.shape[0] < 2:
        return None
    new_vec = matrix[0]
    sims = matrix[1:] @ new_vec  # already L2-normalized
    idx = int(np.argmax(sims))
    return (float(sims[idx]), existing_questions[idx])


def _refresh_keyword_matrix(procedures_dir: Path) -> None:
    """Re-run the keyword matrix build for the whole bank.

    A whole-bank rebuild is cheap (≤200 procedures × ≈80 categories ×
    short text) and sidesteps the bookkeeping bugs of partial updates.
    """
    # Imported lazily so module import doesn't pay the script's argparse
    # / path-fixup cost.
    import sys

    script_dir = REPO_ROOT / "scripts"
    if str(script_dir.parent) not in sys.path:
        sys.path.insert(0, str(script_dir.parent))
    from scripts.build_keyword_matrix import build_matrix

    matrix_path = REPO_ROOT / "keyword_matrix.csv"
    n_rows, n_cats = build_matrix(procedures_dir, DEFAULT_KEYWORDS_PATH, matrix_path)
    logger.info(
        "Refreshed keyword_matrix.csv: %d procedures × %d categories", n_rows, n_cats
    )


def add_question(
    analysis_id: str,
    question: str,
    *,
    dedup: bool = True,
    dedup_threshold: float = DEFAULT_DEDUP_THRESHOLD,
    procedures_dir: Path | None = None,
) -> AddQuestionResult:
    """Append ``question`` to ``procedures/<analysis_id>/questions.json``
    and refresh the dense embeddings + keyword matrix.

    Args:
        analysis_id: the procedure folder name (e.g. ``a_20260511_1c68bb``).
        question: the new paraphrase to add. Should read like a real
            stakeholder question, not like a chart caption.
        dedup: when True, encode the new question against the existing
            paraphrases and skip if cosine ≥ ``dedup_threshold``. The
            skip is non-fatal — the result object reports it.
        dedup_threshold: cosine cutoff for the dedup gate. 0.95 catches
            near-duplicates without blocking legitimate rephrasings
            (which empirically sit around 0.85–0.92).
        procedures_dir: defaults to the bank's procedures/.

    Returns ``AddQuestionResult`` describing whether the append happened
    and (if dedup tripped) what the closest existing paraphrase was.

    Raises:
        FileNotFoundError if the analysis_id has no questions.json.
        ValueError if the question is empty after stripping.
    """
    q = (question or "").strip()
    if not q:
        raise ValueError("add_question: question is empty after stripping.")

    pdir = procedures_dir or PROCEDURES_DIR
    proc_dir = pdir / analysis_id
    qjson = proc_dir / "questions.json"
    data = _load_questions(qjson)

    existing = [
        s.strip()
        for s in (data.get("questions") or [])
        if isinstance(s, str) and s.strip()
    ]
    n_before = len(existing)

    nearest_aid: str | None = None
    nearest_sim: float | None = None
    if dedup and existing:
        nearest = _max_cosine_against_existing(q, existing)
        if nearest is not None:
            nearest_sim, nearest_aid = nearest
            if nearest_sim >= dedup_threshold:
                return AddQuestionResult(
                    analysis_id=analysis_id,
                    appended=False,
                    reason=(
                        f"skipped: cosine {nearest_sim:.3f} ≥ threshold "
                        f"{dedup_threshold:.3f} vs existing paraphrase"
                    ),
                    n_questions_before=n_before,
                    n_questions_after=n_before,
                    nearest_existing=nearest_aid,
                    nearest_similarity=nearest_sim,
                )

    # Append + persist questions.json
    data["questions"] = [*existing, q]
    qjson.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # Refresh embeddings.npy for this procedure (whole-procedure re-encode)
    _emb.compute_and_persist(proc_dir)

    # Refresh keyword_matrix.csv (whole-bank rebuild — cheap and atomic)
    _refresh_keyword_matrix(pdir)

    # Drop the in-process retrieval cache so the next aretrieve() picks
    # up the new embeddings without a process restart.
    _retrieval.reset_caches()

    return AddQuestionResult(
        analysis_id=analysis_id,
        appended=True,
        reason="appended + embeddings + keyword matrix refreshed",
        n_questions_before=n_before,
        n_questions_after=n_before + 1,
        nearest_existing=nearest_aid,
        nearest_similarity=nearest_sim,
    )
