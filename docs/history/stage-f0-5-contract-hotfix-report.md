# Stage F0.5 — Contract Hotfix

Status: complete. No Walk Forward engine, fold loop, stitching implementation or UI. Suite **173/173** (162 + 11 new/reworked in `tests/test_walkforward_contract.py`). Formal DB backed up first (`data/stocks.pre-stage-f0-5.db`); smoke added rows only (migration v3, one strategy, one job, one freeze).

## 1. OOS initial-state contract (`app/services/walkforward/oos.py`)

F0 said "replay the frozen candidate from `panel_start` and carry pre-test positions into OOS". **Withdrawn**: the candidate is only decided at `train_end`, so a training replay produces fills that could never have existed and would hand OOS an inherited portfolio with the entry cost already paid.

**Independent fold (the only implemented mode)**
- OOS ledger starts from **cash** at the fold's first test session.
- After the freeze, data ≤ `train_end` may produce **one boundary target**: the frozen candidate's own rule evaluated at `train_end` (forced even if that day is not a rebalance day; later rebalances follow the candidate's own schedule).
- The target executes under the existing engine rule: earliest T+1 = first test session. All OOS costs are charged (entry included).
- **No frozen-candidate fill may exist before `test_start`** — `run_oos_fold` raises `LEAK` if one does; equity before `test_start` is flat cash.
- Metrics windowed to `test_start..test_end`. Requires `gap_sessions == 0` and `train_end` = session immediately before `test_start` (a gap would fill inside the embargo; refused, not shifted).

**Stitched OOS (contract only; `implemented: false`)**
- Fold k>0 may start from the terminal state of the **previous actually-run OOS fold** (`source="prior_oos_terminal"`, `from_fold=k-1`, `as_of=previous test_end`). `training_replay` is rejected (`TRAINING_REPLAY_STATE_FORBIDDEN`); `run_oos_fold` refuses any non-cash state.
- Stitched metrics are a different statistic from independent-fold metrics and must be stored/labelled separately; independent-fold metrics are the primary OOS evidence (each includes its own entry cost/ramp-up).

Engine untouched: the gate is a `SignalRule` wrapper plus a `dates` rebalance calendar. Contract is also returned by `GET /api/strategy-lab/walkforward/protocol` (`oos_state_contract`).

## 2. Plateau primary selection (rule `wf-freeze/f0.5/2`)

- Plateau → region saved in full; **primary = region medoid**: minimise the sum of graph distances (BFS over the region-induced neighbour graph) to the other region points; ties → lower turnover (missing = worst) → better objective (direction-aware) → lower `point_key`. The IS peak is no longer special (`is_peak_key` is reported only for comparison).
- Non-plateau: not freezable by default (`NO_STABLE_CANDIDATE`). With `require_plateau=False` a single top IS point is frozen and the surface warning is attached: `FROZEN_WITHOUT_PLATEAU` + `ISOLATED_PEAK` / `SCATTERED_SURFACE` / `FLAT_SURFACE` / `INSUFFICIENT_ROBUSTNESS_DATA` (two new structural codes: `FLAT_SURFACE`, `FROZEN_WITHOUT_PLATEAU`, both caution). Warnings are derived from the stored label/edge axes, so no schema change; they appear in every freeze view.
- Edge contract unchanged: `edge_axes` = axes where the **IS peak** (robustness's own definition) sits on the search boundary; recorded, surfaced as `EDGE_OF_SEARCH_RANGE`, never auto-expanded. Note it does not describe the medoid; F1 may want a separate "primary on edge" check.

## 3. Real-data smoke (`scripts/f0_5_real_smoke.py`, formal DB)

Fold 0 from the real calendar: train 2022-11-01..2024-10-30, test 2024-11-01..2025-04-30 (10-31 was a market holiday). `momentum_trend`, `missing.execution="partial"`, 12-point grid (lookback {126,189,252} × MA {100,200} × top_n {20,30}), objective sharpe, linked to a new strategy version.

| Check | Result |
|---|---|
| Job | completed 12/12, 1 panel load, ~81 s total |
| Window | `research_end` = 2024-10-30; fingerprint's last quote date 2024-10-30 ≤ `train_end` |
| Provenance | strategy 3 / version 1 stored; `definition_hash` = version's hash; `root_job_id` = job, `revision_no` 0 |
| Surface | `plateau`, region 3, edge `momentum.lookback` (IS peak at the lookback=252 boundary) |
| Freeze | rule f0.5/2 (strict, no `require_plateau=False` needed); primary = `{lookback 252, top_n 30, MA 200}` = also the IS peak — here it is the region's geometric center (distance sum 2 vs 3 for the other two), so medoid and peak coincide on this real surface; warning `EDGE_OF_SEARCH_RANGE` |
| Digest | recomputed independently from the job's stored results = stored digest (`f472a6f4…`); re-freeze idempotent |
| `open_test_window` | stamped |
| Revision after open | `TEST_WINDOW_ALREADY_OPENED`; 0 child jobs exist |

Severity on the job: `critical` because 18 runs carry `UNRESOLVED_CORPORATE_ACTION` (1 in-window) — a real data limitation the new severity contract now surfaces; F1 must not present such folds as clean. Also 2 `STALE_VALUATION` (caution). The smoke did not run an OOS fold on real data; the test window is now marked opened for this freeze (irreversible by design). The smoke strategy/job/freeze remain in the DB (freezes are write-once).

## 4. Tests added (173 total)

- **Selection (rule v2):** medoid ≠ peak (peak moved to a1/a5/a3/a2 on a chain → primary stays a3, region intact), deterministic under input reordering (same digest), tie-break order turnover → objective (both directions) → key → missing turnover worst, centrality beats turnover, non-plateau forced freeze keeps label warnings, edge warning unchanged, digest content-sensitivity.
- **Freeze leakage:** test-window mutation leaves primary key/params/digest identical (cache defeated), rule version recorded.
- **OOS state / accounting:** cash start with flat pre-test equity (and contrast: a plain replay *does* fill before test_start), first fill = first test session for both a rebalance-day and a non-rebalance-day boundary, entry commission > 0 and gross > net, cash conservation across all fills, mutating test-window quotes cannot change the boundary target, state-source validation (`training_replay`/wrong fold/no prior fold rejected, stitching refused), gap and non-adjacent boundary refused.

## 5. Stage F1 readiness

**Ready.** F1 obligations (replace F0 item 3): per fold run the frozen **primary** via `run_oos_fold` (cash start, boundary target, full costs); report the region as a set and independent-fold metrics as primary; keep stitched OOS separate and only from prior OOS terminal state; propagate freeze warnings and job severity (unresolved corporate actions) to fold results; handle `NO_STABLE_CANDIDATE` folds explicitly; consider a "primary on edge" check.
