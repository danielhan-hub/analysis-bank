"""Nearest-neighbor retrieval over the feature CSV.

Strategy: for each query vector, score *every* corpus entry on two metrics:
  - Euclidean distance — top `euclidean_percentile` of the corpus, capped at
    `max_per_strategy`. This catches "tonally similar" procedures whose
    feature levels are close in absolute terms.
  - Cosine similarity — entries with cosine ≥ `min_cosine`, top
    `max_per_strategy`. This catches "directionally similar" procedures
    whose feature *pattern* is similar even if magnitudes differ.

Union the two strategies and dedupe by analysis_id, preserving each entry's
best (smallest distance / largest cosine). Up to 6 unique matches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from analysis_bank.features.registry import feature_columns, load_all
from analysis_bank.paths import PROCEDURES_DIR


@dataclass
class Match:
    analysis_id: str
    euclidean_dist: float
    cosine_sim: float
    path_to_readme: Path


def _vector(scores: dict[str, int], cols: list[str]) -> list[float]:
    """Materialize a dict-of-scores into an ordered vector. Missing → 0."""
    return [float(scores.get(c, 0)) for c in cols]


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def nearest(
    target_scores: dict[str, int],
    max_per_strategy: int = 3,
    euclidean_percentile: float = 0.10,
    min_cosine: float = 0.5,
    procedures_dir: Path | None = None,
) -> list[Match]:
    """Return up to 6 nearest-neighbor procedures from the corpus.

    Args:
        target_scores: {feature_name: int} for the query (a question or a
            candidate analysis).
        max_per_strategy: cap per strategy (default 3 → up to 6 deduped).
        euclidean_percentile: take the top fraction of the corpus by
            distance. With 10 procedures and 0.10, that's 1; the cap then
            permits up to `max_per_strategy`. Effectively `min(cap,
            max(1, ceil(percentile * N)))`.
        min_cosine: cosine similarity threshold; entries below this are
            ignored even if they're nearest.
        procedures_dir: where each match's README.md lives
            (procedures_dir / <analysis_id> / README.md).
    """
    corpus = load_all()
    if not corpus:
        return []

    if procedures_dir is None:
        # Resolve at call time so test patches take effect.
        import analysis_bank.features.retrieval as _self
        procedures_dir = _self.PROCEDURES_DIR

    cols = feature_columns()
    target = _vector(target_scores, cols)

    scored: list[tuple[str, float, float]] = []
    for aid, scores in corpus.items():
        v = _vector(scores, cols)
        scored.append((aid, _euclidean(target, v), _cosine(target, v)))

    n = len(scored)
    take = min(max_per_strategy, max(1, math.ceil(euclidean_percentile * n)))

    # Strategy 1: top-`take` smallest Euclidean distance
    by_dist = sorted(scored, key=lambda t: t[1])
    eucl_picks = by_dist[:take]

    # Strategy 2: cosine ≥ threshold, top `max_per_strategy` by cosine desc
    cos_eligible = [t for t in scored if t[2] >= min_cosine]
    by_cos = sorted(cos_eligible, key=lambda t: -t[2])
    cos_picks = by_cos[:max_per_strategy]

    # Dedupe by analysis_id; keep the best metrics seen across strategies
    by_aid: dict[str, tuple[float, float]] = {}
    for aid, d, c in eucl_picks + cos_picks:
        if aid not in by_aid:
            by_aid[aid] = (d, c)

    matches = [
        Match(
            analysis_id=aid,
            euclidean_dist=d,
            cosine_sim=c,
            path_to_readme=procedures_dir / aid / "README.md",
        )
        for aid, (d, c) in by_aid.items()
    ]
    # Order: by Euclidean ascending so the most similar surfaces first
    matches.sort(key=lambda m: m.euclidean_dist)
    return matches
