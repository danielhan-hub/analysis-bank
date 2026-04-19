# Analysis Bank

A curated library of reusable Snowflake stored procedures for Instacart Ads Measurement Science, paired with a routing index that lets an agent (or human) quickly find the right procedure for a given question.

## What's in here

```
analysis_bank/                  # Python package
  receiver.py                   # AnalysisBankReceiver — curator API
  smoke.py                      # Smoke-test a procedure.sql against Snowflake
  paths.py                      # Resolves repo paths at runtime
  prompts/
    receiver_agent.md           # System prompt for the receiver Opus agent
INDEX.md                        # Routing guide: question → procedure
procedures/                     # Installed stored procedures (NN_<name>/)
  NN_<name>/
    README.md                   # What this procedure answers, params, output
    procedure.sql               # The Snowflake CREATE OR REPLACE PROCEDURE
candidates/                     # Inbox for proposed new procedures (curator workspace)
```

The bank is **curated**. New procedures don't land here automatically — they're proposed by the [`ads_ms_analysis`](../ads_ms_analysis) promote step, then evaluated and merged by the curator using `AnalysisBankReceiver`.

## Curator workflow

End-to-end, a new procedure travels:

```
analyst writes one-off SQL
        │
        │  ads_ms_analysis.AdsMSAnalyzer.promote_code()
        │  (broker generalizes script → procedure + INDEX_PROPOSED)
        ▼
<case>/codes/new_analysis_candidate_N/   ← candidate folder
        │
        │  receiver.submit(<path>)        ← copies into bank's candidates/
        ▼
analysis_bank/candidates/new_analysis_candidate_N/
        │
        │  await receiver.evaluate()      ← smoke-test + Opus judgment
        ▼
verdict: ACCEPT | REJECT | REVISE
        │
        ├─ ACCEPT → receiver.apply(name)        ← merges into procedures/, updates INDEX.md
        └─ REJECT → receiver.discard(name)      ← deletes the candidate
```

## Public API — `AnalysisBankReceiver`

```python
from analysis_bank import AnalysisBankReceiver

receiver = AnalysisBankReceiver(timeout_seconds=600, max_agent_turns=50)
```

### `submit(source, name=None) -> Path`

Copy a candidate folder into the bank's `candidates/` directory. Saves the curator from manually moving folders in Finder.

| Param | Type | Description |
|---|---|---|
| `source` | `str \| Path` | Path to the candidate folder produced by `promote_code()`, e.g. `<case>/codes/new_analysis_candidate_5`. |
| `name` | `str \| None` | Optional override for the folder name inside `candidates/`. Useful for namespacing across cases (e.g. `"panera__new_analysis_candidate_5"`). |

**Refuses** (raises) on:
- Source folder doesn't exist (`FileNotFoundError`)
- Source folder is malformed — missing `INDEX_BASELINE.md`, `INDEX_PROPOSED.md`, or the procedure subfolder
- A candidate with the resolved name already exists in `candidates/` (`FileExistsError`)

Returns the new path under `candidates/`.

### `await evaluate(candidates_dir=None) -> list[ReceiverVerdict]`

Asynchronously evaluate every candidate in `candidates/`. For each one:

1. **Sanity check** files (BASELINE, PROPOSED, procedure subfolder)
2. **Smoke test** the procedure against Snowflake (compiles + runs SAMPLE CALL). On failure → auto-`REJECT`, no LLM call spent.
3. **Run the receiver agent** (Opus) for the qualitative judgment.

Returns a list of `ReceiverVerdict(candidate, verdict, reason)` where verdict is `"ACCEPT"`, `"REJECT"`, or `"REVISE"`.

```python
import asyncio
verdicts = asyncio.run(receiver.evaluate())
for v in verdicts:
    print(v.candidate, v.verdict, v.reason)
```

### `apply(candidate_name) -> Path`

Merge an accepted candidate into the live library. Run only after reviewing the `evaluate()` verdict.

Sequence — **any failure aborts before touching the live library**:

1. Validate the candidate's shape and required files.
2. **Drift refusal**: if `INDEX_BASELINE.md` no longer matches the live `INDEX.md` (another candidate was applied since this one was generated), refuse and require re-promote. There is intentionally no `force=True` escape — the safer path is to regenerate against the current state.
3. **Catastrophic-corruption guard**: if `INDEX_PROPOSED.md` has fewer than 80% of `INDEX_BASELINE.md`'s lines, refuse (likely broker truncation/scramble).
4. **Smoke test re-run**: catches schema drift in Snowflake since promote-time.
5. **Classify the procedure subfolder shape**:
   - `<digits>_<name>/` → **MODIFY** an existing procedure. NN must already exist in `procedures/`.
   - `<name>/` → **ADD** a new procedure. The receiver assigns the next-free NN, renames the folder, and substitutes every literal `{{NN}}` token in `INDEX_PROPOSED.md` with that NN.
6. Write `PROPOSED → INDEX.md`, copy procedure into `procedures/`.
7. Delete the candidate folder.

Returns the path of the newly installed procedure folder.

### `discard(candidate_name) -> None`

Delete a single candidate folder from `candidates/`. Use for `REJECT`/`REVISE` candidates you don't intend to fix. Raises `FileNotFoundError` if missing.

### `discard_all() -> int`

Delete every candidate folder from `candidates/`. Returns the number deleted.

## Folder-shape convention (broker ↔ receiver protocol)

The broker signals intent to the receiver by the procedure subfolder's name. There is no metadata file — the **shape is the signal**:

| Shape | Meaning | Receiver behavior |
|---|---|---|
| `<name>/` (no digit prefix) | New procedure | Assigns next-free NN, renames folder, substitutes `{{NN}}` token in INDEX_PROPOSED → live INDEX |
| `<digits>_<name>/` | Modification of existing procedure | Keeps the NN, overwrites the existing folder, validates the NN refers to a real procedure |

The `{{NN}}` token is the only string the receiver substitutes — it is unambiguous and deliberately ugly so it can't be confused with prose. The broker must use it everywhere a number would normally appear in `INDEX_PROPOSED.md` for a new procedure (routing table, keyword router, status table).

## Smoke testing

`analysis_bank.smoke.smoke_test_procedure(proc_sql)` runs:
1. `snow sql -f <procedure.sql>` to compile the procedure
2. Extracts the `-- SAMPLE CALL: CALL foo(...)` line from the procedure header and runs it

Fails fast with `SmokeTestError` if either step fails. Used by both `ads_ms_analysis.promote_code()` (post-broker gate) and the receiver (pre-LLM gate in `evaluate`, plus a re-run in `apply`).

For SAMPLE CALL parsing this supports three header formats:
1. `-- SAMPLE CALL: CALL foo(args);` (same line)
2. `-- CALL foo(args);` (no `SAMPLE CALL:` prefix)
3. `-- SAMPLE CALL:` followed by `--` continuation lines (multi-line)

## Installation

Editable install from the repo root:

```bash
cd ~/projects/dev/analysis_bank
pip install -e .
```

This makes `analysis_bank` importable while letting `paths.py` resolve `INDEX.md`, `procedures/`, and `candidates/` relative to the repo root.

## Related

- [`ads_ms_analysis`](../ads_ms_analysis) — the analysis pipeline that produces candidates via `promote_code()`.
