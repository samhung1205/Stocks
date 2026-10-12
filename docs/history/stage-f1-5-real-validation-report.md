# Stage F1.5 — Contract Cleanup + Five-Template Real Walk-Forward Validation

Status: complete. No UI / Strategy Compare / Portfolio Lab; search spaces, templates and strategy parameters untouched. No correctness bug was found in the real runs (the two known consistency issues were fixed first). Full suite **206/206** (202 + 4 new). Raw per-template output: `output/f1_5/<template>.json` (script `scripts/f1_5_real_validation.py`). This report validates the Walk Forward contract and typical outputs; it does **not** rank or compare strategies.

## Part 1 — fixes
**1. Stitched universe.** Each fold's rule in the stitched ledger now receives a context (bars, history, fundamentals, valuations, securities) restricted to *that fold's own point-in-time universe* U_k (monthly union train_start..test_end_k, exactly what the independent fold uses). U_0 ⊆ U_1 ⊆ … only grow. The panel holds the superset; execution/marks stay shared (held names are always in U_k); templates' own daily liquidity filters run unchanged inside the restricted context. Stitched no longer recomputes a universe (saves ~17 s) — it reuses the stored per-fold universes. Real result: stitched fold-0 vs independent fold-0 equity **max relative difference 0.0** (was 1.9%) on every template whose fold 0 was evaluated.
Regression tests (`StitchedUniverseTests`): universes nested and point-in-time (a stock that only qualifies in October never appears in fold 0's universe); stitched fold-0 ≡ independent fold-0 and never trades the future member in fold 0, yet does trade it later; **negative control** — with the restriction removed fold 0 *does* differ (test has teeth); `UniverseView` restricts every context field.
**2. Contract single truth.** `oos.CONTRACT` no longer contains any stitched section (the stale `implemented: false` is gone). The only combined contract is `walkforward/contracts.get()` (`wf-contracts/f1.5/1`: protocol, candidate rule, evidence hierarchy, `independent_fold`, `stitched_oos` = `wf-stitched/f1.5/1`, `implemented: true`). `GET /walkforward/contracts` returns it; `GET /walkforward/protocol` returns `contracts` = the identical object (test asserts equality; old `oos_state_contract` / `stitched_contract` keys removed). `run_oos_fold` says it is independent-only. Two F0.5 assertions that pinned the stale state were updated.

## Part 2 — validation setup
Window 2022-11-01..2026-09-18, default protocol (expanding, 24M train, 6M/6M, final ≥3M kept), default search spaces, objective Sharpe, `assumed` publication, `missing.execution=partial`, `wf-freeze/f0.5/2`. Run sequentially, formal DB, each linked to its own strategy version. All jobs: **4 planned / 4 completed / 0 failed folds**; provenance in every job = `assumed`, `partial`, `wf-freeze/f0.5/2`, `wf-protocol/f0.5/1`. All four expanding train windows start 2022-11-01; tests: 2024-11-01..2025-04-30, 2025-05-02..2025-10-31, 2025-11-03..2026-04-30, **2026-05-04..2026-09-18 (truncated, ~4.5 mo, `PARTIAL_FOLD_TEST_WINDOW` present, handled normally on all five)**.

## A. Jobs
| Template | Grid | Runtime | Runs (cached) | Panel loads | Evaluated / no_candidate |
|---|---:|---:|---:|---:|---|
| momentum_trend | 36 | 2112 s | 144 (36) | 4 | 4 / 0 |
| quality_growth_momentum | 36 | 1192 s | 144 (0) | 4 | 1 / 3 |
| revenue_growth_acceleration | 36 | 720 s | 144 (0) | 4 | 2 / 2 |
| value_quality | 48 | 649 s | 192 (0) | 4 | 4 / 0 |
| low_volatility_quality | 36 | 1577 s | 144 (0) | 4 | 1 / 3 |
Momentum's 36 cached runs came from earlier F0.5/F1 smoke points with identical hashes.

## B. Per fold (E = evaluated, NC = no_candidate; params in template axis order; IS→OOS Sharpe / CAGR% / MaxDD%)
**momentum_trend** (lookback, skip, top_n, MA) — every fold: plateau, region 9, first fill = test_start
| F | Primary | IS peak≠medoid | IS peak edge | Primary on edge | IS → OOS | Turn/cost drag OOS | unres / partial | Sev (OOS / train) |
|---|---|---|---|---|---|---|---|---|
| 0 | 252,0,50,200 | no | lookback, top_n | lookback, top_n | 1.41/42/-21 → -0.98/-37/-37 | 438 / 0.66 | 0 / 0 | caution / critical |
| 1 | 252,21,50,200 | yes | lookback, top_n | lookback, top_n | 0.79/20/-44 → 1.79/49/-8 | 409 / 0.94 | 4 / 4 | critical / critical |
| 2 | 189,0,50,200 | yes | lookback, top_n | top_n | 0.87/23/-43 → 3.67/302/-13 | 353 / 1.12 | 0 / 0 | caution / critical |
| 3 | 189,0,50,200 | yes | lookback, top_n | top_n | 1.37/46/-43 → -0.21/-23/-44 | 434 / 0.62 | 4 / 0 | critical / critical |
**quality_growth_momentum** — F0–F2 NC (`isolated_peak`, IS peak on `top_n` edge; reasons `["NO_STABLE_CANDIDATE","LABEL:isolated_peak"]`), F3 E: plateau, region 9, primary (weights .2/.4/.4, lookback 252, top_n 20), IS peak ≠ medoid, primary edges top_n + growth weight, IS 1.48/45/-33 → OOS 0.43/10/-30, turnover 345 / cost 0.52, unres 2 / partial 0, critical / critical.
**revenue_growth_acceleration** — F0, F1 NC (`isolated_peak`, IS peak on `acceleration_window` edge). F2 E: plateau, region 9, (window 6, weights .4/.6, top_n 50), peak≠medoid, primary edge window+top_n, IS 1.06/25/-40 → 1.93/67/-11, unres 0 / partial 1, caution. F3 E: same primary, IS 1.20/30/-40 → 1.02/34/-23, unres 8 / partial 1, critical.
**value_quality** (top_n, ..., weights) — all E, plateau, primary edge = `top_n` (=50) + factor weights every fold. F0 region 4, peak≠medoid, IS 2.10/34/-12 → -0.51/-18/-30, partial 1; F1 region 11, peak≠medoid, 1.26/26/-29 → 0.78/10/-3, unres 4; F2 region 9, peak = medoid, 1.27/25/-29 → 0.38/5/-7; F3 region 12, peak = medoid, 1.16/22/-29 → 1.84/42/-11, unres 3. OOS turnover 266–331, cost drag 0.40–0.62.
**low_volatility_quality** — F0 E: plateau, region 9, (weights .6/.4, top_n 30, vol lookback 252), peak≠medoid, IS peak edge top_n+lookback, primary edge lookback, IS 2.55/25/-7 → -0.33/-8/-15, unres 0 / partial 0, caution. F1–F3 NC (`isolated_peak`).
First OOS fill = test_start in every evaluated fold; `fills_before_test_start = 0` everywhere.

## C. Cross-fold (descriptive; ≤4 folds)
| Template | NC freq | IS-peak edge | Primary on edge | Digest changes | Region Jaccard (consecutive evaluated) | Folds with unresolved OOS | with partial rebalance | Mean ΔSharpe (OOS−IS) |
|---|---|---|---|---:|---|---|---|---:|
| momentum_trend | 0/4 | 4/4 | 4/4 | 3 | 0.64, 0.5, 0.64 (primary changed in 2 of 3 pairs) | 2/4 | 1/4 | -0.04 |
| quality_growth_momentum | 3/4 | 1/1 | 1/1 | 0 | n/a (1 evaluated) | 1/1 | 0/1 | -1.05 |
| revenue_growth_acceleration | 2/4 | 2/2 | 2/2 | 1 | 0.64 (F2→F3) | 1/2 | 2/2 | +0.35 |
| value_quality | 0/4 | 4/4 | 4/4 | 3 | 0.00, 0.67, 0.62 | 2/4 | 1/4 | -0.82 |
| low_volatility_quality | 3/4 | 1/1 | 1/1 | 0 | n/a | 0/1 | 0/1 | -2.88 |
Degradation is mixed in sign and huge in dispersion (e.g. momentum OOS Sharpe ranges −0.98…3.67): with 1–4 folds these are descriptions, not estimates. The digest also moves when only the region/values change, so digest changes ≥ primary changes (momentum and VQ: primary changed in 2 of 3 consecutive pairs, digest in 3).

## D. Independent vs stitched (all secondary)
- Fold-0 alignment: max rel. diff **0.0** on momentum, value_quality, low_volatility_quality (the other two have NC fold 0 → cash, stitched fold 0 flat, verified 0 fills).
- Stitched has 0 fills before fold 0's `test_start`, `training_replay_used = false`, `independent = false`, `evidence = stitched_secondary`, and always carries `STITCHED_NOT_INDEPENDENT_EVIDENCE`.
- No-candidate liquidation with real positions (**low_volatility_quality F1**): 30 inherited positions from F0's terminal state → 30 sells, all on the first session of the window, equity constant afterwards; subsequent NC folds start from cash (0 fills). QGM/RGA NC folds preceded any position, so they verify "stay in cash" only. `STITCHED_LIQUIDATION_AT_NO_CANDIDATE` present exactly where an NC fold exists after a position was possible.
- State hand-over: fold k>0 `state_source = prior_oos_terminal`, inherited positions 49–50 for momentum/VQ (top_n 50), 50 for RGA F3 (RGA F2 started from cash after NC folds).

## Special checks
- **QGM fundamentals vs first training/fold:** financial publications (assumed deadline) exist from 2021-05, but in every QGM training window the first ~5 monthly signals are empty (180 = 5 × 36 runs) and the first fill is **2023-05-02** (other fundamental templates fill on 2022-11-01). Training therefore runs ~6 months in cash at the start of each window (`EMPTY_SIGNALS` caution). Cause not root-caused here (consistent with the growth factor needing ~2 years of quarterly history from a 2021-05 data start). It is a coverage limit inside the *training* window only — fold 0's OOS (from 2024-11) is unaffected and no future data enters. It does depress/reshape QGM IS surfaces and may contribute to its NC rate.
- **Fundamental templates assumed only:** all four report `publication_mode = assumed`; strict is rejected at creation (tested). `ASSUMED_PUBLICATION_MODE` (caution) appears on QGM training severity.
- **Final truncated fold:** normal on all five (evaluated or NC as usual, flag present).
- **Critical severity source:** every critical *data* signal (training and OOS) is `UNRESOLVED_ACTIONS_IN_WINDOW` / `UNRESOLVED_CORPORATE_ACTION`; all 36–48 training runs per fold carry them (unresolved actions crossed by held names in the training window). OOS critical = unresolved actions in folds 1 & 3 (momentum), QGM F3, RGA F3, VQ F1 & F3; OOS `critical` on NC folds comes from `NO_STABLE_CANDIDATE` by design. No `DELISTING_EXIT_UNAVAILABLE`, `NO_VALUATION_PRICE`, `EXECUTION_CANCELLED` or `INVALID_SHARE_MULTIPLIER` anywhere. Training severity is `critical` on 100% of folds, so it carries no discriminating information until unresolved corporate actions are resolved upstream.
- **Habitual NO_STABLE_CANDIDATE:** yes for QGM (3/4), LVQ (3/4), RGA (2/4); none for momentum and value_quality. **Every** NC is `isolated_peak` (never scattered/flat/insufficient), with the IS peak sitting on a search-range edge in all of them.
- **Primary stuck on search edge:** **12 of 12 evaluated folds** across the five templates have the frozen medoid on ≥1 edge axis (momentum 4/4, VQ 4/4, RGA 2/2, QGM 1/1, LVQ 1/1); `top_n = 50` (grid max) recurs for momentum, RGA, VQ. IS peak differs from the medoid in 8 of 12 evaluated folds. The default search spaces look boundary-constrained; per instructions they were not widened (a widening would have to be a new, disclosed trial).

## Findings for F2 / next steps (no code changed for these)
1. Contract behaves as specified on real data: order, cash start, first fill ≥ test_start, no-candidate without test load, stitched separation, resume-safe persistence, exact fold-0 alignment.
2. UI must show, up front: NC frequency, primary-on-edge, and that training `critical` is universal (corporate-action data quality) so fold severity should be read from the OOS part.
3. Open data issue worth its own task: unresolved corporate actions inflate every training run; and QGM's 6-month empty-signal head.
4. Edge-bound default spaces: decide (separately, before any OOS use) whether to define wider default spaces as a new research version.

## Readiness
Backend Walk Forward contract: **validated on real data for all five templates — ready for F2 (read-only UI)**, subject to the display requirements above.
