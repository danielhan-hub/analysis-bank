"""Retrieval eval harness — recall@1, recall@5, MRR.

Loads `cases.yaml`, runs the hybrid retrieval pipeline against each
question, and reports per-case + aggregate metrics. Used to detect
regressions when swapping the dense encoder, the cross-encoder, the
keyword YAML, or the paraphrase set.

Usage:

    # Quick local run, sparse + rerank only (no LLM fitness)
    python tests/retrieval_eval/eval.py

    # With LLM fitness (slower, hits the SDK)
    python tests/retrieval_eval/eval.py --with-fitness

    # Compare against a baseline JSON
    python tests/retrieval_eval/eval.py --baseline baseline.json
        --fail-on-regression

The harness exits non-zero if `--fail-on-regression` is set and
recall@5 drops vs the baseline — wire that into CI for the retrieval
package.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analysis_bank.features.retrieval import aretrieve  # noqa: E402

DEFAULT_CASES = Path(__file__).parent / "cases.yaml"


@dataclass
class EvalCase:
    id: str
    question: str
    expected: str
    accept_top_k: list[str]
    notes: str = ""


@dataclass
class EvalResult:
    case_id: str
    expected: str
    rank: int | None  # 1-indexed; None if expected not in top-K returned
    top_ids: list[str]


def _load_cases(path: Path) -> list[EvalCase]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text) or {}
    except ImportError:
        raise SystemExit(
            "PyYAML not installed; install dev extras: `pip install -e .[dev]`"
        )
    raw_cases = data.get("cases") or []
    out: list[EvalCase] = []
    for c in raw_cases:
        if not c.get("expected"):
            continue
        out.append(
            EvalCase(
                id=c["id"],
                question=str(c["question"]).strip(),
                expected=str(c["expected"]).strip(),
                accept_top_k=[str(x).strip() for x in (c.get("accept_top_k") or [])],
                notes=str(c.get("notes") or "").strip(),
            )
        )
    return out


async def _eval_one(case: EvalCase, *, with_fitness: bool) -> EvalResult:
    candidates = await aretrieve(
        case.question,
        require_chart_eligible=False,
        skip_llm_fitness=not with_fitness,
    )
    accepted = {case.expected, *case.accept_top_k}
    rank: int | None = None
    for i, c in enumerate(candidates, start=1):
        if c.analysis_id in accepted:
            rank = i
            break
    return EvalResult(
        case_id=case.id,
        expected=case.expected,
        rank=rank,
        top_ids=[c.analysis_id for c in candidates],
    )


def _aggregate(results: Iterable[EvalResult]) -> dict[str, float]:
    results = list(results)
    n = len(results)
    if n == 0:
        return {"n": 0, "recall@1": 0.0, "recall@5": 0.0, "mrr": 0.0}
    r1 = sum(1 for r in results if r.rank == 1) / n
    r5 = sum(1 for r in results if r.rank is not None and r.rank <= 5) / n
    mrr = sum(1.0 / r.rank for r in results if r.rank) / n
    return {"n": n, "recall@1": r1, "recall@5": r5, "mrr": mrr}


async def run_eval(cases_path: Path, *, with_fitness: bool) -> tuple[list[EvalResult], dict[str, float]]:
    cases = _load_cases(cases_path)
    if not cases:
        raise SystemExit(
            f"{cases_path} has no labelled cases; populate it before running the eval."
        )
    results = await asyncio.gather(
        *[_eval_one(c, with_fitness=with_fitness) for c in cases]
    )
    return list(results), _aggregate(results)


def _print_results(results: list[EvalResult], metrics: dict[str, float]) -> None:
    print(f"{'case':<48s}  {'rank':>5s}  top-5")
    print("-" * 90)
    for r in results:
        rank_s = str(r.rank) if r.rank is not None else "miss"
        top = ", ".join(r.top_ids[:5]) or "(empty)"
        print(f"{r.case_id:<48s}  {rank_s:>5s}  {top}")
    print()
    print("Aggregate:")
    print(f"  n         = {metrics['n']}")
    print(f"  recall@1  = {metrics['recall@1']:.3f}")
    print(f"  recall@5  = {metrics['recall@5']:.3f}")
    print(f"  MRR       = {metrics['mrr']:.3f}")


def _check_regression(metrics: dict[str, float], baseline_path: Path) -> int:
    if not baseline_path.exists():
        print(
            f"\nNo baseline at {baseline_path}; writing current metrics as new baseline."
        )
        baseline_path.write_text(json.dumps(metrics, indent=2))
        return 0
    baseline = json.loads(baseline_path.read_text())
    delta = metrics["recall@5"] - baseline.get("recall@5", 0.0)
    print(
        f"\nrecall@5: {metrics['recall@5']:.3f} vs baseline {baseline.get('recall@5', 0.0):.3f} "
        f"(delta {delta:+.3f})"
    )
    if delta < -1e-9:
        print("REGRESSION — recall@5 dropped vs baseline.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--with-fitness",
        action="store_true",
        help="Run the LLM fitness stage (slower; needs claude-agent-sdk).",
    )
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit non-zero if recall@5 regresses vs baseline.",
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="Write current metrics to this path (overwrites).",
    )
    args = parser.parse_args()

    results, metrics = asyncio.run(
        run_eval(args.cases, with_fitness=args.with_fitness)
    )
    _print_results(results, metrics)

    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(metrics, indent=2))
        print(f"Baseline written to {args.write_baseline}")

    if args.baseline:
        rc = _check_regression(metrics, args.baseline)
        if rc != 0 and args.fail_on_regression:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
