# Analysis Bank

A curated library of reusable Snowflake stored procedures for Instacart Ads Measurement Science. Each procedure is scored against a fixed 76-feature rubric so retrieval can find the closest matches for a new question by feature-vector similarity.

## What's in here

```
analysis_bank/                  # Python package
  receiver.py                   # AnalysisBankReceiver — curator API (single-shot ACCEPT/REJECT)
  smoke.py                      # Smoke-test a procedure.sql against Snowflake
  paths.py                      # Resolves repo paths at runtime
  features/
    feature_dict.md             # Canonical 76-feature rubric (scoring spec)
    registry.py                 # CSV upsert/load by analysis_id
    retrieval.py                # Euclidean + cosine nearest-neighbor search
    scorer.py                   # 5-juror Olympics ensemble scorer
  prompts/
    scoring_agent.md            # System prompt for the 5-juror scorer
    inspector_agent.md          # System prompt for the receiver Opus agent
analysis_features.csv           # One row per scored procedure (analysis_id + 76 features)
procedures/                     # Installed stored procedures (a_<YYYYMMDD>_<6hex>/)
  <analysis_id>/
    README.md                   # What this procedure answers, params, output
    procedure.sql               # The Snowflake CREATE OR REPLACE PROCEDURE
candidates/                     # Inbox for proposed new procedures (curator workspace)
scripts/
  backfill_existing_procedures.py  # One-shot migration: score + rename existing procs
```

The bank is **curated**. New procedures don't land here automatically — they're proposed by the [`ads_ms_analysis`](../ads_ms_analysis) promote step, then evaluated and merged by the curator using `AnalysisBankReceiver`.

## Curator workflow

End-to-end, a new procedure travels:

```
analyst writes one-off SQL
        │
        │  ads_ms_analysis.AdsMSAnalyzer.promote_code()
        │  (broker generalizes script → procedure.sql + README.md)
        ▼
<case>/codes/<analysis_id>/      ← candidate folder (a_<YYYYMMDD>_<6hex>/)
        │
        │  receiver.submit(<path>)
        ▼
analysis_bank/candidates/<analysis_id>/
        │
        │  await receiver.evaluate()    ← smoke-test + Opus single-shot judgment
        ▼
verdict: ACCEPT | REJECT
        │
        ├─ ACCEPT → 5-juror score, upsert CSV row, copy to procedures/, drop from candidates
        └─ REJECT → prints reason + suggested changes; candidate stays in candidates/ for inspection
```

There is no REVISE loop. Each candidate gets one shot. Iteration happens by the analyst editing the source SQL and re-promoting.

## Public API — `AnalysisBankReceiver`

```python
from analysis_bank import AnalysisBankReceiver

receiver = AnalysisBankReceiver(timeout_seconds=600, max_agent_turns=50)
```

### `submit(source, name=None) -> Path`

Copy a candidate folder into the bank's `candidates/` directory.

| Param | Type | Description |
|---|---|---|
| `source` | `str \| Path` | Path to the candidate folder produced by `promote_code()`, e.g. `<case>/codes/a_20260424_ab12cd`. |
| `name` | `str \| None` | Optional override for the folder name inside `candidates/`. Useful for namespacing across cases (e.g. `"panera__a_20260424_ab12cd"`). |

**Refuses** (raises) on:
- Source folder doesn't exist (`FileNotFoundError`)
- Source folder is malformed — missing `procedure.sql` or `README.md`
- A candidate with the resolved name already exists in `candidates/` (`FileExistsError`)

Returns the new path under `candidates/`.

### `await evaluate(candidates_dir=None) -> list[ReceiverVerdict]`

Asynchronously evaluate every candidate in `candidates/`. For each one:

1. **Sanity check** files (`procedure.sql`, `README.md`)
2. **Smoke test** the procedure against Snowflake (compiles + runs SAMPLE CALL). On failure → auto-`REJECT`, no LLM call spent.
3. **Run the inspector agent** (Opus) for the qualitative judgment — single-shot ACCEPT/REJECT.
4. On `ACCEPT`: invoke the 5-juror scorer, upsert the row in `analysis_features.csv`, copy the folder into `procedures/<analysis_id>/`, remove from `candidates/`. Prints `ACCEPTED AND MERGED: <analysis_id>`.
5. On `REJECT`: print the reason + suggested changes block. The candidate stays in `candidates/` so the operator can inspect it (or `discard()` it).

Returns a list of `ReceiverVerdict(candidate, verdict, reason)` where verdict is `"ACCEPT"` or `"REJECT"`.

```python
import asyncio
verdicts = asyncio.run(receiver.evaluate())
for v in verdicts:
    print(v.candidate, v.verdict, v.reason)
```

### `discard(candidate_name) -> None`

Delete a single candidate folder from `candidates/`. Use for `REJECT` candidates you don't intend to retry. Raises `FileNotFoundError` if missing.

### `discard_all() -> int`

Delete every candidate folder from `candidates/`. Returns the number deleted.

## Feature scoring + retrieval

The 76-feature rubric in `features/feature_dict.md` is the canonical scoring spec. Both procedures and questions are scored against the same rubric so they live in the same vector space.

**Scorer.** `analysis_bank.score(readme_text, sql_text)` and `analysis_bank.score_question(question, case_summary)` both run a 5-juror Olympics ensemble:

1. Five parallel scoring runs (Opus, `asyncio.gather`)
2. Per feature: drop the highest and lowest score
3. Average the middle three; round to the nearest integer
4. Clamp to `[-5, 5]`

**Retrieval.** `analysis_bank.nearest(target_scores)` returns up to 6 deduped matches:
- Top 10% of the corpus by Euclidean distance, capped at 3
- Plus entries with cosine similarity ≥ 0.5, top 3
- Sorted by Euclidean ascending so the most similar surfaces first

The orchestrator scores each plan-analysis question and surfaces these matches as candidates the agent can consider — they are not mandates.

## Smoke testing

`analysis_bank.smoke.smoke_test_procedure(proc_sql)` runs:
1. `snow sql -f <procedure.sql>` to compile the procedure
2. Extracts the `-- SAMPLE CALL: CALL foo(...)` line from the procedure header and runs it

Fails fast with `SmokeTestError` if either step fails. Used by both `ads_ms_analysis.promote_code()` (post-broker gate) and the receiver (pre-LLM gate in `evaluate`).

Supports three SAMPLE CALL header formats:
1. `-- SAMPLE CALL: CALL foo(args);` (same line)
2. `-- CALL foo(args);` (no `SAMPLE CALL:` prefix)
3. `-- SAMPLE CALL:` followed by `--` continuation lines (multi-line)

## Installation

Editable install from the repo root:

```bash
cd ~/projects/dev/analysis_bank
pip install -e .
```

This makes `analysis_bank` importable while letting `paths.py` resolve `procedures/`, `candidates/`, `analysis_features.csv`, and the prompt files relative to the repo root.

## Related

- [`ads_ms_analysis`](../ads_ms_analysis) — the analysis pipeline that produces candidates via `promote_code()` and consumes the bank via `nearest()` for plan-analysis retrieval.
