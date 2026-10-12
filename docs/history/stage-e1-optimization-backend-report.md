# Stage E1 — Optimization + Robustness Backend

Status: complete. No UI, Walk Forward/OOS, Portfolio Lab. Backtest Core (engine, metrics, factors, templates, panel) is unchanged; D1 code is only imported.
Outputs are **exploratory, in-sample research candidate regions** — not forecasts, not recommendations. No "winner"/"best" field exists anywhere in the API (a test scans every response for such keys).

## 1. Architecture

| File | Role |
|---|---|
| `app/services/optimization/search_space.py` | Pure. Allowed parameters/ranges per template, default spaces, grid/explicit expansion, seeded budget sampling |
| `app/services/optimization/robustness.py` | Pure. Neighbour graph, plateau classification, axis sensitivity, dispersion, diagnostics, surface |
| `app/services/optimization_lab.py` | Service: job creation, lifecycle, cache, monthly universe union, windowed metrics, read side |
| `app/db/tables.py` | +`optimization_jobs`, `optimization_runs` (additive, via `create_all`) |
| `app/api/routes.py`, `app/main.py` | 9 thin routes; startup `recover_interrupted()` |
| `tests/test_optimization_backend.py` | 47 tests |

Flow of one job (`BackgroundTasks`, same as D1; no Celery/Redis):
1. **create** — validates, expands the search plan, writes the job and all run rows as `pending` (progress/resume are plain SQL). Identical active job is deduplicated.
2. **execute** — resolves the **monthly universe union** (first trading day of every month in the window → `data_foundation.liquid_universe` → union; per-month counts stored) and freezes it; computes one data fingerprint; then for each pending point: resolve config → run hash → cache lookup → (lazily) build the panel **once** → `run_backtest`.
3. Panel = `panel_start 2021-09-22 → 2026-09-18` (warm-up for lookbacks) with price-only `adj_close` for Momentum/MA/Volatility; execution and accounting stay on raw prices (engine untouched). Fundamentals use `assumed` mode; `strict` is rejected for fundamental templates.
4. **Windowing**: the engine runs over warm-up + window; metrics are recomputed with the existing `metrics.compute` over `research_start`-onward equity/fills/trades so points with different lookbacks are compared on the same dates. `evaluate_point` wraps the rule in a pass-through recorder (selection counts) and never alters signals, config or ledger. A test proves stored metrics equal a plain `run_backtest` when the window equals the panel, and that the engine receives the same `BacktestConfig` a normal run would build.

Each stored result carries: windowed metrics, `unresolved_action_count` (engine value verbatim) + in-window count + first 10 items, `warnings_count` + first 50 warnings, per-run coverage (fills, first fill, distinct stocks, signals / empty signals / selected min-median-max), job-level panel coverage and universe info.

## 2. Search spaces

Only parameters in `ALLOWED` (with ranges) can be searched; anything else is rejected. Lookbacks are capped at 252 (warm-up limit). Weight groups are enumerated as **normalized discrete combinations** (integer units summing to 1/step, each ≥ min weight; step 0.2/min 0.2 → 2 keys: 4 combos, 3 keys: 6). `momentum.lookback > skip_recent` is enforced (invalid points counted in `skipped_invalid`).

| Template | Default axes | Points |
|---|---|---:|
| momentum_trend | lookback {126,189,252} × skip {0,21} × MA {100,200} × top_n {20,30,50} | 36 |
| quality_growth_momentum | lookback {126,252} × top_n {20,30,50} × factor_weights (6 combos) | 36 |
| revenue_growth_acceleration | acceleration_window {2,3,6} × top_n {20,30,50} × revenue.weights (4) | 36 |
| value_quality | top_n {20,30,50} × factor_weights (4) × value.weights (4) | 48 |
| low_volatility_quality | vol lookback {63,126,252} × top_n {20,30,50} × factor_weights (4) | 36 |

`rebalance.frequency` (monthly/weekly), quality weights, growth weights, `value.max_valuation_age_days` are searchable on request but not in defaults (weekly costs ~4× runtime — measured 62s vs 15s per run). Users may supply their own `search_space` (`axes` + `weight_groups`, validated) or explicit `combinations`.

## 3. Run budget

`max_runs` default 60, hard cap 200. Grid > `max_runs` → `budget_policy: sample` (default; seeded `random.Random(seed)`, returned in grid order, `sampled: true` reported) or `reject` (400). Explicit combinations over budget are rejected, never truncated. `seed` default 0; only sampling is random, so the same request + seed + data gives the same points and results (tested, cache defeated).

## 4. Objective / robustness / plateau

**Objectives** (exploration ordering only): sharpe, sortino, calmar, cagr, net_return, total_return, max_drawdown, annualized_volatility, turnover, cost_drag (direction-aware, missing last, ties by run_index). Every result row returns CAGR, Sharpe, Sortino, MaxDD, Calmar, net/gross return, cost drag, turnover, volatility, unresolved count, warnings and coverage regardless of the objective. Field is `exploration_rank`, not "rank 1 = best".

**Neighbours**: axis index distance + (L1 of weight-unit vectors)/2; neighbours = distance exactly 1 (a weight move of one unit from one key to another counts as 1).

**Plateau rule** (`robustness.RULE`, echoed in every response; `score` = objective sign-flipped if lower is better, `spread` = max−min):
1. `insufficient_data`: < 6 valid points, or the top point has no neighbour.
2. `flat`: spread ≈ 0.
3. `isolated_peak`: (score(top) − median score of its neighbours) / spread > 0.25.
4. `plateau`: connected component (neighbour edges) of the top `max(3, ceil(25% · n))` points containing the top point has ≥ 3 points.
5. otherwise `scattered`.
Also returned: per-point neighbour median/min/max/mean-abs-diff/local drop, per-axis step sensitivity and marginal means, whether the top point sits on a search-range edge, dispersion (min/p25/median/p75/max/std/IQR/CV) of CAGR, Sharpe, Sortino, MaxDD, Calmar, returns, cost drag, turnover, unresolved and warning counts for the top set and the peak region, parameter concentration in the top set, and job-level diagnostics (turnover/cost/unresolved distributions, share of runs with unresolved actions, objective↔turnover Spearman).

**Surface**: `x`(+`y`) projection with `agg` ∈ median/mean/min/max/count; each cell always includes n/min/max so aggregation cannot hide spread; full point list included.

## 5. Persistence / API (`/api/strategy-lab/optimization/…`)

`GET search-spaces` · `POST jobs` · `GET jobs` · `GET jobs/{id}` (status, progress, cached, panel_loads, universe, coverage) · `POST jobs/{id}/cancel` · `POST jobs/{id}/resume?retry_failed=` · `GET jobs/{id}/results` · `GET jobs/{id}/surface` · `GET jobs/{id}/robustness`. Errors: 400/404/409 as in D1.

- **Lifecycle**: queued → running → completed | failed | cancelled. A point failure is recorded on its row and the job continues (`partial: true`); a job is `failed` only if the universe/panel fails or no point succeeded.
- **Cache**: per-point `run_hash` = sha256(opt/engine version, data fingerprint, template config, backtest config, windows, universe hash, mode, benchmark). Cross-job; identical points are copied (`cached_from_job`), and a fully cached job never loads the panel. Data change ⇒ new hash (tested).
- **Resume**: completed points are never recomputed; `running` points return to pending; `retry_failed` re-queues failed points; universe stays frozen. Server restart marks active jobs `failed` (resumable).
- **Cancel**: implemented cooperatively (safe with in-process threads): queued → immediate; running → stops before the next point, the in-flight point finishes (engine cannot be interrupted); resumable.

## 6. Performance (real DB copy, momentum_trend, 12 of 36 sampled points)

- Universe union: 47 months → 1,346 stocks (~17 s). Panel: ~13 s, built **once** (`panel_loads = 1`).
- ≈ 14 s per monthly point (value_quality ≈ 4 s, weekly ≈ 60 s). Whole job 205 s. Full 36-point default ≈ 9 min.
- Identical rerun: 12/12 cached, 0 panel loads, 17 s (universe recomputation dominates).
- Not verified: a full default grid on all 5 templates on real data; runs in the web process threadpool.

## 7. Tests (47; full suite 127 pass)

Search space: expansion size/order/constraint, weight normalization, defaults valid for all 5 templates, rejection of non-searchable/out-of-range, explicit validation, seeded budget, reject policy. Robustness: plateau, isolated peak, flat, insufficient, scattered, direction flip, edge flag, weight-group neighbours, axis sensitivity, dispersion, surface, diagnostics. Jobs: panel loaded once, cache reuse (no panel/engine calls), cache invalidated by data change, budget/seed, partial failure, all-failed, cancel→resume (no recompute), queued cancel, job-level failure→resume, restart recovery, retry_failed, determinism, accounting unchanged, windowing, unresolved-action propagation, results carry risk/cost, robustness/surface, validation, dedupe, explicit combos, monthly-union (later qualifiers included; frozen; used for the panel), API lifecycle/errors, no "best/winner" keys.

## 8. Known limitations

- Everything is **in-sample** over one 3.9-year window; plateau ≠ out-of-sample validity. Multiple-testing/selection bias is not corrected.
- Sampled grids (or explicit lists) give sparse neighbourhoods → mostly `insufficient_data`; the response reports `grid_fraction_evaluated`.
- Windowing: short-lookback points may hold positions at `research_start` inherited from warm-up trading; costs before the window are excluded.
- Unresolved corporate actions are reported, not excluded (in-window count provided); adjusted price is factor-only.
- Assumed-mode fundamentals (all filing dates are deadlines; holding-company early-look-ahead), sector-neutral falls back to global, survivorship from the current `stocks` snapshot.
- Equity curves per point are not stored (re-run through D1 for one). Universe is recomputed on each new job (~17 s).
- Jobs run in one web-process thread; no parallelism.
- Plateau thresholds are fixed heuristics (echoed in output), not statistically calibrated.

## 9. E2 UI readiness

**Ready** for a read-only/exploratory UI: create/status/progress/cancel/resume, results table with risk+cost columns, surface heatmap data, robustness panel (label, rule, dispersion, diagnostics). UI must always show the in-sample notice, the plateau `rule`, `grid_fraction_evaluated`, and never sort-and-highlight a single row as a pick. Recommended before E2: run one full default grid per template on real data to check runtimes and typical plateau labels.
