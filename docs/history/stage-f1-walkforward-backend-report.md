# Stage F1 — Walk Forward / OOS Backend

Status: complete. No UI, Strategy Compare, Portfolio Lab; Strategy Templates, engine and F0/F0.5 modules untouched (`oos.py`, `freeze.py`, `protocol.py` unchanged). Full suite **202/202** (173 existing + 29 in `tests/test_walkforward_backend.py`). Formal DB backed up to `data/stocks.pre-stage-f1.db` before the real smoke.

## 1. Architecture
| File | Role |
|---|---|
| `app/services/walkforward_lab.py` | Job/fold orchestration, persistence, lifecycle, read side |
| `app/services/walkforward/stitched.py` | Stitched OOS contract `wf-stitched/f1/1`: gate rule, chain validation, single-ledger run |
| `app/services/walkforward/diagnostics.py` | Pure descriptive diagnostics, `primary_edge_axes` |
| `app/db/tables.py`, `migrations.py` | `walkforward_jobs`, `walkforward_folds`, `walkforward_equity`, `walkforward_trades` + triggers |
| `app/api/routes.py`, `app/main.py` | 15 thin routes under `/strategy-lab/walkforward/…`; startup `recover_interrupted()` |

SQLite + BackgroundTasks, no Celery/Redis. New tables only (create_all), so no schema-version bump.

## 2. Fold lifecycle
Per fold, strictly in order: training optimization (`research_start/end` = fold train window, `missing.execution="partial"`, same fixed search space for every fold) → `freeze_from_job` (`wf-freeze/f0.5/2`) → `open_test_window` → build test-window panel → `run_oos_fold` → persist in **one transaction** (fold row + equity + trades). Test data is loaded only after the window is opened (tested by event order; real smoke logs it). After all folds: stitched ledger → diagnostics → job `completed`.
Fold `status`: pending/running/completed/failed; `outcome`: `evaluated` | `no_candidate`; `stage` records training/frozen/test_opened/oos_done.

## 3. OOS state semantics
- **Independent fold**: F0.5 contract verbatim — cash start, boundary target from data ≤ `train_end`, first fill ≥ `test_start`, entry costs charged, metrics/equity only `test_start..test_end`. Re-running a fold directly from cash reproduces stored metrics exactly (tested).
- **Stitched**: one continuous ledger from cash at fold 0's first test session. Fold k's frozen primary takes over at its own boundary (= fold k-1 `test_end`), inheriting only fold k-1's actual OOS terminal state; its forced boundary target supersedes fold k-1's rule that day. No training replay (gate never evaluated before fold 0's boundary; tested). Requires identical costs/sizing/execution across folds, else `STITCHED_CONFIG_MISMATCH` → stitched `unavailable` (not approximated). Stitched is only built when every fold is completed.

## 4. Independent vs stitched
Stored separately (`series='fold'` vs `'stitched'`, `stitched_json` vs `oos_metrics_json`), every payload carries `evidence: independent_fold | stitched_secondary`, stitched carries `STITCHED_NOT_INDEPENDENT_EVIDENCE`. `/oos` and diagnostics contain no stitched numbers (only a labelled reference block in the result).

## 5. No-candidate behavior
`NO_STABLE_CANDIDATE` from the freeze rule is never rescued (`require_plateau` is not exposed; no IS-peak fallback). The fold is stored `no_candidate`, **no test data is loaded and no freeze is created**, equity is flat cash, cash metrics are flagged `cash_fold`, reasons kept verbatim (`freeze.reasons`, e.g. `["NO_STABLE_CANDIDATE","LABEL:isolated_peak"]`) with severity `NO_STABLE_CANDIDATE` (critical) + `FOLD_HELD_CASH`, plus the training job's severity. In **stitched**, a no_candidate fold **liquidates to cash at its boundary** (empty target, T+1, costs charged) and stays in cash; the previous fold's parameters are not carried over (`STITCHED_LIQUIDATION_AT_NO_CANDIDATE`, caution). Fold k+1 then starts from that cash.

## 6. Diagnostics (`summary_json`, `/result`)
IS-vs-OOS Sharpe/CAGR/MaxDD/turnover with deltas + Sharpe ratio; degradation summary; parameter stability (per-axis values/changes, primary and digest changes, region Jaccard between consecutive evaluated folds); plateau labels, IS-peak edge and **primary edge** per fold; cost stability (turnover, cost drag, returns); unresolved / partial-rebalance counts; per-fold severity (+ training severity); completeness (planned/completed/evaluated/no_candidate/failed, truncated final fold, dropped windows). Fold dispersion is `descriptive_only`, `statistical_inference: "not performed"`.
New codes: `PRIMARY_ON_SEARCH_EDGE` (caution; medoid on min/max of any axis with ≥3 values, incl. weight-group members; 2-value axes are not flagged since always at a bound), `FOLD_HELD_CASH`, `STITCHED_LIQUIDATION_AT_NO_CANDIDATE`, `STITCHED_NOT_INDEPENDENT_EVIDENCE`, `FOLD_DISPERSION_DESCRIPTIVE`. Also emitted: `PARTIAL_FOLD_TEST_WINDOW`, `PARTIAL_REBALANCE_USED`, `UNRESOLVED_ACTIONS_IN_WINDOW`, freeze warnings, classified engine warnings.

## 7. Persistence / API
Immutable via triggers: job definition (request, protocol, folds plan, strategy/version/definition hash, candidate rule, execution policy, publication mode, provenance); fold window columns; `train_job_id`/`freeze_id`/`candidate_digest`/`test_opened_at` write-once; a **completed fold is entirely immutable** (no update/delete); independent equity/trades append-only. Provenance also lists per-fold training job, freeze id/digest, universe spec, engine/contract versions, and a trial disclosure (other WF jobs for the same definition).
Routes: `POST jobs`, `GET jobs`, `GET jobs/{id}` (status/progress/current fold+stage/provenance), `POST …/cancel`, `POST …/resume`, `GET …/result`, `…/folds`, `…/folds/{k}`, `…/folds/{k}/equity|trades`, `…/oos`, `…/stitched`, `…/stitched/equity|trades`, `GET walkforward/contracts`.
Lifecycle: completed folds never recomputed; failure recorded on the fold, job `failed`, resumable; a fold whose test window was opened reuses its training job and freeze on resume (no new job, no revision chain — tested); restart marks jobs failed/running folds pending, completed folds untouched; cancel is cooperative (stops before the next stage, also cancels the in-flight training job), completed evidence intact.

## 8. Real smoke (formal DB, `momentum_trend`, 12-point grid, default protocol, `scripts/f1_real_smoke.py`)
Job with `data_end=2026-04-30` → 3 folds; total **243 s** (fold-0/1 training served from the run cache; fold-2 training ~2 min; each OOS ≈ 15–30 s + universe union). Earlier 2-fold job (`data_end` 2025-10-31): 176 s.
| Fold | Outcome | Detail |
|---|---|---|
| 0 (test 2024-11..2025-04) | evaluated, plateau | primary lookback252/top30/MA200 (on lookback edge; IS peak also); first fill 2024-11-01; 212 fills; OOS Sharpe −0.90, net −19.4% vs IS Sharpe 1.23 |
| 1 (2025-05..2025-10) | **no_candidate** (`isolated_peak`) | cash, no freeze, test panel never built (verified by event log) |
| 2 (2025-11..2026-04) | evaluated, plateau | same primary; first fill 2025-11-03; OOS Sharpe 3.31, net +86% vs IS 0.80 |
Checks passed: training window == fold train window; training fingerprint last quote ≤ `train_end`; `open_test_window` logged before each OOS panel build; fills_before_test_start = 0; first fill ≥ test_start; stitched has 0 fills before fold 0 test_start, `training_replay_used=false`; stitched fold 1 = liquidation of 29 inherited positions (30 fills), fold 2 starts from cash. Stitched net +53%, Sharpe 1.03 (secondary). Training severity is `critical` on every fold (real unresolved corporate actions in IS runs, carried through); OOS folds 0 and 2 had 0 unresolved, 0 partial rebalances (max severity caution). Fold dispersion across two evaluated folds is huge (Sharpe −0.9 vs 3.3) — descriptive only.

## 9. Tests (29)
Creation (4-fold default protocol, partial execution, validation, dedupe, strategy provenance/mismatch); order train→freeze→open→test load; training window; cash start/first fill/entry cost/independent metrics reproduce; full evidence persisted; no-candidate (cash, no freeze, no test load, verbatim reasons); primary-on-edge (pure + end to end); partial rebalance (real suspended stock → count>0 + flag); determinism across jobs; stitched separation, fold-0 segment ≡ independent, inheritance, no training replay, chain/config guards, no-candidate liquidation; diagnostics (incl. pure with cash/pending folds, no p-values); fold failure/resume; cancel/resume; restart recovery; immutability triggers; write-once links; provenance; test-window mutation (prices ×3 + corporate action) leaves all training/freezes/earlier OOS unchanged; publication mode guards; API lifecycle/errors.

## 10. Known limitations
- Stitched uses one universe (monthly union through the last test_end) while independent folds use union through their own test_end; real smoke: stitched fold-0 segment differs from independent fold 0 by up to 1.9% equity. Templates' own daily liquidity filter bounds the effect; not exact equality.
- Stitched needs every fold completed; a failed fold blocks it until resume. A no-candidate fold's stitched liquidation is a policy choice (documented, flagged).
- `oos.CONTRACT["stitched_oos"]["implemented"]` is still `False` (F0.5 test pins it); the F1 contract is `stitched.CONTRACT` (served by `/walkforward/contracts`).
- Orphan optimization job possible if the process dies between training-job creation and storing its id; the next resume creates a fresh one.
- Universe union/panel are rebuilt per fold (~15–30 s each); jobs run in one web-process thread.
- Nothing prevents a user from starting a new WF job with a wider space after seeing OOS; only disclosed via `trial_disclosure`. Survivorship / assumed-mode fundamentals limits from E1 apply.
- ≤4 folds: all cross-fold statistics are descriptive.

## 11. Readiness
**F1 real-validation gate: ready** — run the other four templates (fundamental ones in assumed mode; expect ~5–10 min each, 3–4 folds) and review no-candidate frequency and edge flags. **F2: ready** for a read-only UI once that gate passes; UI must show independent-fold evidence first, stitched as secondary, `no_candidate` folds and the critical training severity prominently.
