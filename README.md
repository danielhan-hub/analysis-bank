# Analysis Bank

A curated library of reusable **end-to-end analysis bundles** for Instacart Ads Measurement Science. Each bundle is anchored by a parameterized Snowflake stored procedure (`procedure.sql`) and ships with a documented README, a `questions.json` paraphrase set, and — when the source analysis produced a chart — a callable `chart.py` that renders the canonical visualization. Retrieval runs a hybrid pipeline (BGE dense embeddings + BM25 sparse keywords + cross-encoder rerank + LLM fitness) so a new question can find the closest matching bundle.

## What's in here

```
analysis_bank/                  # Python package
  receiver.py                   # AnalysisBankReceiver — curator API (single-shot ACCEPT/REJECT + auto-merge)
  smoke.py                      # Smoke-test a procedure.sql against Snowflake (SmokeTestError on failure)
  paths.py                      # Resolves repo paths at runtime
  _async.py                     # Jupyter-safe sync/async bridge (run_sync)
  features/
    embeddings.py               # BGE-large encoder + per-procedure embeddings.npy persistence
    keyword_index.py            # 15-category BM25 sparse retrieval over keyword_matrix.csv
    keywords.yaml               # Curated keyword taxonomy (15 categories)
    registry.py                 # CSV upsert/load: analysis_id → chart_eligible
    retrieval.py                # Hybrid pipeline: HyDE → dense + sparse → rerank → LLM fitness
  prompts/
    inspector_agent.md          # System prompt for the receiver Opus agent
analysis_features.csv           # One row per merged procedure (analysis_id, chart_eligible)
keyword_matrix.csv              # Sparse retrieval index (analysis_id × 15 categories)
procedures/                     # Installed analysis bundles (a_<YYYYMMDD>_<6hex>/)
  _index.md                     # Auto-maintained Markdown table (analysis_id | summary | chart_eligible)
  <analysis_id>/
    README.md                   # What this bundle answers, params, output
    procedure.sql               # The Snowflake CREATE OR REPLACE PROCEDURE
    questions.json              # {summary, questions: [8 paraphrases]} — embedded for retrieval
    embeddings.npy              # BGE-large vectors over questions + summary (computed at merge)
    chart.py                    # OPTIONAL — callable render_chart(...) that emits chart_n.png files
    chart_skipped.md            # OPTIONAL — present when no chart applies (rationale)
    [other source artifacts]    # CSV outputs, helper scripts, notes carried forward by promotion
candidates/                     # Inbox for proposed new bundles (curator workspace)
scripts/
  backfill_existing_procedures.py  # Idempotent: re-embed + rebuild keyword matrix + upsert chart_eligible rows
  build_keyword_matrix.py          # Rebuild keyword_matrix.csv from procedures/ + keywords.yaml
tests/
  retrieval_eval/               # Offline retrieval eval harness (recall@1/@5, MRR vs baseline.json)
```

The bank is **curated**. New bundles don't land here automatically — they're proposed by the [`ads_ms_analysis`](../ads_ms_analysis) promote step, then evaluated and merged by the curator using `AnalysisBankReceiver`.

## Curator workflow

End-to-end, a new analysis bundle travels:

```
analyst's run produces an end-to-end bundle
(SQL + chart.ipynb + chart_1.png [+ chart_2.png ...] + CSV)
        │
        │  ads_ms_analysis.AdsMSAnalyzer.promote_code()
        │  (broker generalizes script → procedure.sql + README.md;
        │   when chart.ipynb is present, also emits chart.py)
        ▼
<case>/codes/<analysis_id>/      ← candidate folder (a_<YYYYMMDD>_<6hex>/)
        │
        │  receiver.submit(<path>)
        ▼
analysis_bank/candidates/<analysis_id>/
        │
        │  receiver.evaluate()          ← smoke-test + Opus single-shot judgment
        ▼
verdict: ACCEPT | REJECT
        │
        ├─ ACCEPT → copy to procedures/, compute embeddings.npy, rebuild keyword_matrix.csv,
        │           upsert analysis_features.csv (chart_eligible), update procedures/_index.md,
        │           drop from candidates
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
- Source folder is malformed — missing `procedure.sql` or `README.md`. When `chart.py` is present, it must also pass smoke-import, signature, and hardcoded-id checks (see Receiver Validation below).
- A candidate with the resolved name already exists in `candidates/` (`FileExistsError`)

Returns the new path under `candidates/`.

### `evaluate(candidates_dir=None) -> list[ReceiverVerdict]`

Evaluate every candidate in `candidates/`. Sync — works in plain Python and in Jupyter without `asyncio.run` / `await`. For concurrent use inside an existing async context, call `await receiver.aevaluate()` instead. For each one:

1. **Sanity check** files (`procedure.sql`, `README.md`, `questions.json`, plus the chart contract: either valid `chart.py` + `chart_n.png` or a written `chart_skipped.md`).
2. **Smoke test** the procedure against Snowflake (compiles + runs SAMPLE CALL). On failure → auto-`REJECT`, no LLM call spent.
3. **Run the inspector agent** (Opus) for the qualitative judgment — single-shot ACCEPT/REJECT.
4. On `ACCEPT`: copy the folder into `procedures/<analysis_id>/`, compute and persist `embeddings.npy`, rebuild `keyword_matrix.csv`, upsert `chart_eligible` into `analysis_features.csv`, update `procedures/_index.md`, reset retrieval caches, and remove from `candidates/`. Prints `ACCEPTED AND MERGED: <analysis_id>`.
5. On `REJECT`: print the reason + suggested changes block. The candidate stays in `candidates/` so the operator can inspect it (or `discard()` it).

Returns a list of `ReceiverVerdict(candidate, verdict, reason)` where verdict is `"ACCEPT"` or `"REJECT"`.

```python
verdicts = receiver.evaluate()  # sync — works in Jupyter and terminal
for v in verdicts:
    print(v.candidate, v.verdict, v.reason)
```

### `discard(candidate_name) -> None`

Delete a single candidate folder from `candidates/`. Use for `REJECT` candidates you don't intend to retry. Raises `FileNotFoundError` if missing.

### `discard_all() -> int`

Delete every candidate folder from `candidates/`. Returns the number deleted.

## Retrieval — hybrid pipeline

`analysis_bank.aretrieve(question_text, ...)` (and its sync wrapper `retrieve(...)`) returns ranked `Candidate(analysis_id, chart_eligible, fitness_label, rationale, ...)` for a question. The pipeline runs in five stages:

1. **HyDE** — Haiku drafts a synthetic README that *would* answer the question; the draft is averaged with the raw question vector to bias the dense recall.
2. **Dense recall (BGE-large)** — cosine similarity (max-pool over each procedure's `embeddings.npy`) returns top-`k_dense` analysis_ids.
3. **Sparse recall (BM25)** — `keyword_index.rank_question` scores the question against `keyword_matrix.csv` (15 curated categories in `features/keywords.yaml`) and returns top-`k_sparse` analysis_ids. The two recall sets are unioned and deduped.
4. **Cross-encoder rerank (BGE-reranker-large)** — re-scores the union against the question; trims to top-`k_rerank`.
5. **Panel jury (Opus)** — reads every survivor's `README.md` + `procedure.sql` side-by-side, restates the question, then emits per-candidate `STRONG` / `WEAK` / `REJECT` labels with rationale and nominates a single winner (`is_jury_winner=True`). `force_pick_one=True` does **not** change the jury's reasoning — every candidate is still judged and returned with its label intact; the flag only binds downstream `plan_analysis` to REUSE the jury's winner. `lenient=False` tightens the rubric.

Optional flags: `require_chart_eligible=True` filters to `chart_eligible` rows (Reuse-Only Mode in `ads_ms_analysis`); `skip_hyde` / `skip_llm_fitness` short-circuit Stages 1 and 5 for offline eval.

**Embeddings persistence.** `embeddings.compute_and_persist(procedure_dir)` reads `questions.json`, encodes the 8 paraphrases plus the summary with BGE-large, and writes `embeddings.npy` next to the procedure. The receiver does this on every ACCEPT; `scripts/backfill_existing_procedures.py` re-embeds the corpus idempotently when the encoder or `keywords.yaml` changes.

**Chart-eligibility registry.** `analysis_features.csv` is a minimal CSV (`analysis_id,chart_eligible`) maintained by `registry.upsert_chart_eligible`. It is the only state retrieval reads to gate Reuse-Only Mode.

**Eval harness.** `tests/retrieval_eval/` runs labelled cases through `aretrieve` and reports recall@1, recall@5, and MRR against `baseline.json`. Recall@5 gates CI; recall@1 and MRR are informational so a single judgment-call regression doesn't block merges.

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

- [`ads_ms_analysis`](../ads_ms_analysis) — the analysis pipeline that produces candidates via `promote_code()` and consumes the bank via `aretrieve()` for plan-analysis retrieval.
