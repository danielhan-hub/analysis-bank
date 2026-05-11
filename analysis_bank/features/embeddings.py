"""Per-procedure embedding persistence for hybrid retrieval Stage A.

At intake the receiver calls :func:`compute_and_persist` to encode the
8 paraphrases + summary from ``questions.json`` with BGE-large and
write the matrix to ``procedures/<analysis_id>/embeddings.npy``. At
retrieval time :func:`load_corpus` walks every procedure folder and
reads the cached matrices — no encoder load needed unless a procedure
is missing its file (which we then encode on the fly and persist for
next time).

Why persist?
- Embedding 5 procedures × 9 lines each is fast on CPU (~1s with BGE),
  but doing it on every retrieval call is silly. Bigger banks (>100
  procedures) start to feel it.
- Stable bytes on disk make embeddings reviewable in PRs (the file is
  small enough — 9 × 1024 × float32 ≈ 37 KB per procedure).
- Decouples the dense encoder lifecycle from retrieval startup. The
  encoder is only loaded when (a) a new procedure is being promoted
  or (b) a query needs to be encoded.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

from analysis_bank.paths import PROCEDURES_DIR


logger = logging.getLogger(__name__)


EMBEDDINGS_FILENAME = "embeddings.npy"
DEFAULT_DENSE_MODEL = "BAAI/bge-large-en-v1.5"


_DENSE_MODEL = None  # SentenceTransformer instance once loaded


def _model_name() -> str:
    return os.environ.get("ANALYSIS_BANK_DENSE_MODEL", DEFAULT_DENSE_MODEL)


def get_dense_model():
    """Lazy-load the BGE encoder. Returned model is cached process-wide.

    sentence_transformers is imported here (not at module scope) so that
    tooling that only needs registry/keyword features doesn't have to
    pay the torch import cost or fail when running in slim test envs
    that monkeypatch :func:`encode`.
    """
    global _DENSE_MODEL
    if _DENSE_MODEL is None:
        from sentence_transformers import SentenceTransformer
        name = _model_name()
        logger.info("Loading dense encoder %s …", name)
        _DENSE_MODEL = SentenceTransformer(name)
    return _DENSE_MODEL


def encode(lines: list[str]) -> np.ndarray:
    """Encode a list of strings into an L2-normalized float32 matrix.

    Returns a (len(lines), embedding_dim) array. Empty input returns an
    empty (0, 0) array — callers must check shape[0] before using it.
    """
    if not lines:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = get_dense_model().encode(
        lines,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return vectors.astype(np.float32, copy=False)


def _lines_from_questions(qjson_path: Path) -> list[str]:
    """Read questions.json → [paraphrases..., summary]."""
    data = json.loads(qjson_path.read_text(encoding="utf-8"))
    questions = data.get("questions") or []
    summary = (data.get("summary") or "").strip()
    lines = [q for q in questions if isinstance(q, str) and q.strip()]
    if summary:
        lines.append(summary)
    return lines


def compute_and_persist(procedure_dir: Path) -> Path:
    """Encode the procedure's paraphrases and write embeddings.npy.

    Called from the receiver after a candidate is merged into procedures/.
    Raises if questions.json is missing or unreadable — the receiver gate
    should have already validated it, so a failure here means the bundle
    on disk is corrupt and retrieval would silently skip the procedure.
    """
    qjson = procedure_dir / "questions.json"
    if not qjson.exists():
        raise FileNotFoundError(
            f"compute_and_persist: {qjson} missing. The receiver gate should "
            f"have rejected this candidate before promotion."
        )
    lines = _lines_from_questions(qjson)
    if not lines:
        raise ValueError(
            f"compute_and_persist: {qjson} has no usable lines (empty summary "
            f"and no questions). Cannot index for dense recall."
        )
    matrix = encode(lines)
    out = procedure_dir / EMBEDDINGS_FILENAME
    np.save(out, matrix, allow_pickle=False)
    logger.info(
        "Persisted %d embeddings (%d-d) → %s",
        matrix.shape[0],
        matrix.shape[1],
        out,
    )
    return out


def load_procedure_vectors(procedure_dir: Path) -> np.ndarray | None:
    """Read one procedure's cached embeddings, or None if not on disk."""
    p = procedure_dir / EMBEDDINGS_FILENAME
    if not p.exists():
        return None
    try:
        arr = np.load(p, allow_pickle=False)
    except (ValueError, OSError) as e:
        logger.warning("Could not read %s: %s", p, e)
        return None
    if arr.ndim != 2 or arr.shape[0] == 0:
        logger.warning("Skipping malformed embeddings at %s (shape=%s)", p, arr.shape)
        return None
    return arr.astype(np.float32, copy=False)


def load_corpus(
    procedures_dir: Path | None = None,
    *,
    encode_missing: bool = True,
) -> dict[str, np.ndarray]:
    """Walk procedures/ and return {analysis_id: (n_lines, dim) matrix}.

    If a procedure has questions.json but no embeddings.npy on disk and
    ``encode_missing`` is True, encode it on the fly and persist for
    next time. Set ``encode_missing=False`` for diagnostic / read-only
    callers that don't want to trigger an encoder load.
    """
    if procedures_dir is None:
        procedures_dir = PROCEDURES_DIR
    out: dict[str, np.ndarray] = {}
    for proc in sorted(procedures_dir.iterdir()):
        if not proc.is_dir() or proc.name.startswith("."):
            continue
        vecs = load_procedure_vectors(proc)
        if vecs is None and encode_missing and (proc / "questions.json").exists():
            try:
                compute_and_persist(proc)
                vecs = load_procedure_vectors(proc)
            except (FileNotFoundError, ValueError) as e:
                logger.warning("Skipping %s in dense corpus: %s", proc.name, e)
                continue
        if vecs is not None:
            out[proc.name] = vecs
    return out
