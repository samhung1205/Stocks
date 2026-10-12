# Stage F0 — Walk-Forward / OOS Research Contract

> **Superseded in part by [Stage F0.5](stage-f0-5-contract-hotfix-report.md):** §2 item 2 (primary = top IS point) and §7 obligation (3) (replaying the candidate from `panel_start` and carrying pre-test positions into OOS) are replaced. Everything else stands.

Status: complete. Contract + guards only — no Walk Forward engine, no UI, Strategy Templates untouched. Full suite **162/162** (127 existing + 35 new in `tests/test_walkforward_contract.py`). The real `data/stocks.db` was not touched; migration v3 runs on next app startup.

## 1. Final research protocol (`app/services/walkforward/protocol.py`)

| Item | Default | Why (3.9 years of data) |
|---|---|---|
| Window | **expanding**, anchored at 2022-11-01 | Rolling would discard scarce early data and make each fold's optimisation noisier |
| Initial training | **24 months** | Spans ≥1 regime change; lookbacks ≤252 sessions come from the warm-up before `data_start` (feeds signals, never metrics) |
| Test length / step | **6 / 6 months**, non-overlapping | More/shorter folds re-optimise on near-identical data and test on a handful of trades |
| Re-optimisation | once per fold | — |
| Gap / embargo | **0 sessions** (`gap_sessions` configurable) | No forward labels, so nothing overlaps the boundary; a signal on the last train day executes T+1 inside the test window under the *frozen* candidate. An embargo would burn ~8% of the sample for no leak |
| Final short fold | kept if ≥3 months, flagged `truncated` | |
| Execution policy | `partial` (§3) | |

Result on 2022-11-01 → 2026-09-18: **4 folds** — tests 2024-11..2025-04, 2025-05..2025-10, 2025-11..2026-04, 2026-05-01..2026-09-18 (4.6 mo, `PARTIAL_FOLD_TEST_WINDOW`). Validation rejects `step < test` (overlapping tests double-count), rolling without a length, unknown keys. This is a reasonable default, not a statistically optimal design; ≤4 folds means fold-level dispersion is descriptive only.

## 2. Candidate freeze contract (`app/services/walkforward/freeze.py`)

Data flow: `Training optimisation job → freeze → (test window opened) → frozen primary evaluated on test`.

1. **Training job = fold's training window exactly.** `freeze_from_job(job, fold)` refuses (`TRAIN_WINDOW_MISMATCH`) any job whose `research_start/end` differs from the fold's train window, or that isn't `completed`. The job's panel is built only to its `research_end`, and `data_fingerprint` is bounded by it, so no row after `train_end` can reach training (tested: test-window rows ×3 and deleted → panel end == `train_end`, metrics byte-identical even with the cache defeated, identical freeze digest).
2. **Fixed, versioned rule** (`wf-freeze/f0/1`), reads only in-sample results: label `plateau` → region = plateau region, **primary = its top in-sample point, declared at freeze time**. Any other label → `NO_STABLE_CANDIDATE` (not freezable) unless the caller explicitly passes `require_plateau=False`, in which case only the single top point is frozen and the label travels with it. The test window evaluates the primary and *reports* the region as a set; it never chooses among them.
3. **Write-once.** `wf_candidate_freezes` has triggers blocking UPDATE/DELETE (only `test_opened_at` may go NULL→timestamp, once). One freeze per job; re-freezing is idempotent. `candidate_digest` = content hash independent of ids/timestamps.
4. **`open_test_window(freeze_id)`** must be called by F1 before loading any row dated after `train_end`. It closes the revision chain of the training job.
5. **Edge of search range**: freezing does not auto-expand; `edge_axes` is recorded and surfaced as `EDGE_OF_SEARCH_RANGE`. Expansion = a new job (§4) created **before** the test window is opened. After it is opened, `create_job(parent_job_id=…)` fails with `TEST_WINDOW_ALREADY_OPENED`; the only route left is a brand-new root research, which is disclosed as a separate trial.
6. **Residual risk (honest):** nothing technical stops a user from starting a fresh root job with a wider space after seeing OOS. The contract makes that visibly *not a revision* (no shared root, no parent) so F1 can count/flag trials per strategy; it cannot forbid it.

## 3. Execution policy decision

Problem: `missing.execution = next_available` defers the **whole** target order if *any* held or target name lacks an execution price; one suspended stock can freeze the entire rebalance for days.

**Decision: `partial` (partial rebalance, frozen suspended names) is the research default for Walk Forward** (`DEFAULT_PROTOCOL.execution_policy`). Stage B's own default stays `next_available` so E1 results/hashes remain reproducible. Implemented as a third value of `MissingDataRule.execution` (backward-compatible; invalid values now raise). Semantics (`engine._rebalance`, `marks` arg):

- **Accounting:** names with a price trade normally; names without one (held or targeted) are *frozen*: shares unchanged, valued at last close (existing stale-valuation warnings apply). Equity for sizing includes frozen holdings at that mark. Both net and gross ledgers behave identically.
- **Target-weight drift:** a frozen holding keeps its drifted weight (e.g. 30% vs 25% target). Not re-normalised; next rebalance retries.
- **Suspended holding with target 0:** cannot be sold; stays until it trades.
- **Cash:** proceeds of tradable sells fund tradable buys only (buys already clip to cash). The weight of an unbought frozen *target* stays cash — not redistributed to other names (would silently change strategy exposure). No margin, no shares/cash created (tested: flat prices, zero cost ⇒ equity ≡ initial cash on every day; buys−sells = position shares; cash conserved).
- **Nothing tradable at all** → deferred, same as `next_available`.
- **Turnover/cost:** trades happen on the earliest tradable day instead of being delayed, so turnover is at the intended time and cost is paid on the tradable subset only; frozen names add no cost until they are traded. `metadata.partial_rebalances` / `partial_rebalance_count` and warning `PARTIAL_REBALANCE` (caution) record every event.
- With no missing prices `partial` is byte-identical to `next_available` (tested).

## 4. Provenance / revision contract

New immutable columns on `optimization_jobs` (migration v3, ALTER-only; triggers abort any later change, including NULL→value): `strategy_id`, `strategy_version_id`, `strategy_version_no`, `definition_hash`, `parent_job_id`, `root_job_id`, `revision_no`, `revision_kind`, `revision_reason`, `revision_json`.

- **Strategy link (optional):** payload `strategy_id` + `strategy_version_id` (or `strategy_version_no`). The submitted definition is checked against the stored version (resolved template config and backtest config); mismatch → 400, never mis-attributed. `definition_hash` = the version's hash when linked, else the hash of the submitted definition.
- **Revision:** payload `parent_job_id` + `revision_reason` (required). Parent must be `completed`, same template/strategy link; **only the search space may change** — window, objective, params, backtest, mode, universe, benchmark must equal the parent's (prevents objective/window shopping under the "revision" label). It is always a new job; parent row and runs are never written (tested byte-identical). Stores `root_job_id`, `revision_no`, and `revision_json` = `space_diff` (added/removed axis values, `is_pure_expansion`) + parent's edge axes at creation.
- **Legacy jobs:** provenance columns stay NULL; `get_job().provenance.known = false` with `LEGACY_JOB_NO_PROVENANCE` (info). Nothing back-filled.
- `job_hash` now includes strategy version and parent so a revision never dedupes into an active job.

## 5. Warning severity (`app/services/warning_contract.py`, `GET /api/strategy-lab/warning-contract`)

Three levels, stable **codes**, first-match pattern table; unknown text → `UNCLASSIFIED` = **caution** (never silently info). No scoring.

| Severity | Codes |
|---|---|
| **critical** | `UNRESOLVED_CORPORATE_ACTION`, `DELISTING_EXIT_UNAVAILABLE`, `INVALID_SHARE_MULTIPLIER`, `NO_VALUATION_PRICE`, `EXECUTION_CANCELLED`; structural: `ALL_SIGNALS_EMPTY`, `UNRESOLVED_ACTIONS_IN_WINDOW`, `NO_STABLE_CANDIDATE` |
| **caution** | `PARTIAL_REBALANCE`, `EXECUTION_DEFERRED`, `TARGET_ORDER_REPLACED`, `VALUATION_USED_EXECUTION_PRICE`, `STALE_VALUATION`, `ADJUSTED_PRICE_RESIDUAL_JUMP`, `SECTOR_NEUTRAL_FALLBACK`, `ASSUMED_PUBLICATION_MODE`, `TRADING_CALENDAR_MISSING`, `UNCLASSIFIED`; structural: `EDGE_OF_SEARCH_RANGE`, `ISOLATED_PEAK`, `SCATTERED_SURFACE`, `EMPTY_SIGNALS`, `PARTIAL_FOLD_TEST_WINDOW`, `REVISION_WITHOUT_EDGE`, `PARTIAL_REBALANCE_USED`, `FAILED_RUNS` |
| **info** | `SIGNAL_BEYOND_WINDOW`; structural: `IN_SAMPLE_ONLY`, `SAMPLED_GRID`, `INSUFFICIENT_ROBUSTNESS_DATA`, `LEGACY_JOB_NO_PROVENANCE` |

`GET …/optimization/jobs/{id}/robustness` now also returns `severity` (`items`, `counts`, `codes`, `max_severity`). This replaces the UI's `>=50 warnings` heuristic as the contract; UI adoption is left to E2 follow-up / F. `REVISION_WITHOUT_EDGE` and `PARTIAL_REBALANCE_USED` are defined but not yet emitted (F1 wiring).

## 6. Tests (35 new; suite 162/162)

- Protocol (3): default folds/no overlap/expanding, gap & rolling, validation + short final fold dropped.
- Warning contract (4): mapping table, unknown→caution, summary/catalog, every warning the engine actually emits is classified.
- Execution policy (9): all-or-nothing defers; partial freezes suspended holding; no cash/shares created (equity ≡ initial); frozen holding not sellable; frozen unheld target stays cash; nothing-tradable defers; partial ≡ next_available without gaps; Stage B default unchanged/invalid value rejected; cost under partial.
- Freeze / leakage (7): selection rule, digest, **test-window mutation doesn't change training results/frozen candidates**, panel ends at `train_end`, window mismatch/incomplete refused, idempotent + write-once + `test_opened_at` once.
- Provenance / revision (8): version provenance, mismatched definition refused, immutable columns (status still writable), legacy stays NULL + migration rerunnable, revision never overwrites parent, only-search-space rule, revision blocked after test opened, severity in robustness.
- Boundary regression (3): publication strictly after `publish_date`, adjusted-price point-in-time, folds train < test. Existing D+/Stage B suites also pass unchanged (two migration-count assertions bumped 2→3).

Not verified: freeze on a real full-grid job; F0 changes exercised only on synthetic/offline data.

## 7. Stage F1 readiness

**Ready**, with these F1 obligations: (1) run the training job per fold with `research_start/end` = fold train window and `missing.execution="partial"`; (2) `freeze_from_job` → `open_test_window` **before** any test data load; (3) evaluate the frozen primary on `panel_start → test_end`, metrics windowed to the test window (same E1 windowing; positions inherited from pre-test trading), report region as a set; (4) handle a fold with `NO_STABLE_CANDIDATE` explicitly (skip/cash, reported, never silently backfilled); (5) emit `PARTIAL_FOLD_TEST_WINDOW` / `PARTIAL_REBALANCE_USED` / `REVISION_WITHOUT_EDGE`; (6) surface the `TRAIN_WINDOW_MISMATCH` and trial-count disclosure. Open choice for F1: whether `window` per fold reuses the ~17 s universe recompute per job (E1 limitation) — a cost, not a contract issue.
