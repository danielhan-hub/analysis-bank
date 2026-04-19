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

**Resubmission semantics.** If the new candidate's `_source.sql` matches the `_source.sql` of any existing candidate in `candidates/`, those candidates are **removed** before the copy and the newest of their `RECEIVER_FEEDBACK.md` files is **carried forward** into the new candidate's procedure subfolder. This keeps the inbox in a clean one-per-source state and gives the receiver agent the prior round's notes to judge against on re-evaluation. Removals are logged to stdout.

**Refuses** (raises) on:
- Source folder doesn't exist (`FileNotFoundError`)
- Source folder is malformed — missing `INDEX_BASELINE.md`, `INDEX_PROPOSED.md`, or the procedure subfolder
- A candidate with the resolved name already exists in `candidates/` (`FileExistsError`) — note that same-source candidates with *different* names are removed automatically; this only fires on a literal name collision.

Returns the new path under `candidates/`.

### `await evaluate(candidates_dir=None, auto_discard_rejects=False) -> list[ReceiverVerdict]`

Asynchronously evaluate every candidate in `candidates/`. For each one:

1. **Sanity check** files (BASELINE, PROPOSED, procedure subfolder)
2. **Smoke test** the procedure against Snowflake (compiles + runs SAMPLE CALL). On failure → auto-`REJECT`, no LLM call spent.
3. **Run the receiver agent** (Opus) for the qualitative judgment. If a `RECEIVER_FEEDBACK.md` is present in the candidate's procedure subfolder (carried forward from a prior round), the agent is told to read it and call out unaddressed items.

| Param | Type | Description |
|---|---|---|
| `candidates_dir` | `Path \| None` | Override the directory to scan. Defaults to the bank's `candidates/`. |
| `auto_discard_rejects` | `bool` | When `True`, immediately delete any candidate that receives a `REJECT` verdict (saves a manual `discard()` call). Default `False` — operator can inspect first. |

Returns a list of `ReceiverVerdict(candidate, verdict, reason)` where verdict is `"ACCEPT"`, `"REJECT"`, or `"REVISE"`. The list still includes REJECT verdicts even when `auto_discard_rejects=True` — only the on-disk folder is removed.

```python
import asyncio
# Default: keep rejects so you can inspect them
verdicts = asyncio.run(receiver.evaluate())
# Or trust the receiver and tidy up automatically
verdicts = asyncio.run(receiver.evaluate(auto_discard_rejects=True))
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

## REVISE feedback loop

When `evaluate()` returns a `REVISE` verdict, the receiver agent emits a structured `RECEIVER_FEEDBACK.md` into the candidate's procedure subfolder (alongside `procedure.sql` and `README.md`). Format:

```
## RECEIVER_FEEDBACK

### Summary
<2–3 sentence overview>

### Issues by Criterion
- **Distinctiveness**: ...
- **Generalizability**: ...
- ...

### Concrete Fixes
- [ ] specific change 1
- [ ] specific change 2
```

The loop is **symmetric** — both the broker (producer) and the receiver (curator) read the most recent prior feedback when they next see this same source script:

**Producer side** (`ads_ms_analysis.AdsMSAnalyzer.promote_code()`):
- At promote time it writes the bare source SQL into the candidate as `_source.sql`.
- On every subsequent promote, it scans `analysis_bank/candidates/*/*/_source.sql` for a content match against the current source. If matched and a sibling `RECEIVER_FEEDBACK.md` exists, the markdown is injected into the broker prompt as "Prior Reviewer Feedback" so the next pass starts from the reviewer's actual notes.

**Curator side** (`AnalysisBankReceiver.submit()` → `evaluate()`):
- `submit()` finds and removes existing candidates with the same `_source.sql`, then carries forward the newest `RECEIVER_FEEDBACK.md` into the new candidate's procedure subfolder before the receiver runs.
- `evaluate()` surfaces the prior feedback path in the receiver agent's user prompt; the agent's system prompt instructs it to read the file first and explicitly call out any prior `Concrete Fixes` items that remain unaddressed.

**Bounded size — no compression needed.** Each round's `RECEIVER_FEEDBACK.md` **overwrites** the previous one (single-file replacement on the receiver side; `submit()` carries forward exactly the most recent on the producer side). Both sides only ever see one round of prior context, never accumulated history. This breaks the oscillation pattern (broker ignores reviewer's point ad infinitum) without unbounded growth.

Matching is by **source-content equality** — deterministic, immune to LLM renaming the broker-chosen subfolder. If `analysis_bank` isn't installed or `candidates/` doesn't exist, the producer-side lookup silently returns `None` and the promote runs fresh.

`apply()` filters both `_source.sql` and `RECEIVER_FEEDBACK.md` when copying into `procedures/` — they are curator-loop artifacts, not part of the procedure's contract.

`ACCEPT` and `REJECT` verdicts do **not** write a feedback file (REJECT means discard; ACCEPT means apply — neither triggers a re-promote).

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
