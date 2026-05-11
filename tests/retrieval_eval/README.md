# Retrieval eval harness

A small offline harness that scores the hybrid retrieval pipeline on a
labelled set of `(case → expected_procedure)` pairs.

## Files

- `cases.yaml` — labelled pairs. **You** populate this from past
  correctly-answered runs. Schema and template inside the file.
- `eval.py` — the harness. Computes `recall@1`, `recall@5`, `MRR` over
  every labelled case.
- `baseline.json` (created on first run) — frozen metrics from the
  current retrieval implementation. New runs compare against it; CI
  fails if `recall@5` regresses.

## Workflow

```bash
# 1. Populate cases.yaml with labelled pairs (≥10 cases is the floor for
#    the metric to be meaningful)

# 2. Run the harness — quick mode (no LLM fitness, sparse + rerank only)
python tests/retrieval_eval/eval.py

# 3. Once you trust the result, freeze it as the baseline
python tests/retrieval_eval/eval.py --write-baseline tests/retrieval_eval/baseline.json

# 4. CI: re-run after every change to retrieval.py / keywords.yaml /
#    questions.json paraphrases / encoder swap, and fail on regression
python tests/retrieval_eval/eval.py \
    --baseline tests/retrieval_eval/baseline.json \
    --fail-on-regression
```

## What "regression" means

Only `recall@5` gates CI — `recall@1` and `MRR` are reported but
non-blocking. Rationale: as long as the right procedure makes the top 5,
`plan_analysis` will see it as evidence; the cross-encoder + LLM fitness
stages can still pick it as the winner. A drop in `recall@5` means
either Stage A (dense recall) or Stage B (BM25) is missing relevant
procedures entirely — that's the catastrophic failure mode worth
gating on.

## Why an eval harness at all

The bank's retrieval has historically failed silently — Mary Ruth Run 2
picked the same procedure for three different question shapes and
no one noticed until the charts came out wrong. A labelled eval set
turns that silent failure into a CI signal: every encoder swap,
keyword-YAML edit, or paraphrase regen now has a measurable
recall delta.
