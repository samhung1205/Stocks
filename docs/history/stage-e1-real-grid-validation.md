# Stage E1 — Real-data Full-Grid Validation

Data: official `data/stocks.db`; window 2022-11-01 ～ 2026-09-18 (panel warm-up from 2021-09-22); fundamental templates in `assumed`; monthly universe union (47 months → **1,346 stocks**, first month 726); default search spaces, **no sampling** (`max_runs=60` ≥ every grid). Five jobs run sequentially, each followed by an identical cache-rerun job.
**No code changed** (no correctness bug found). Full suite still 127/127. Validation jobs remain in the real DB (`optimization_jobs`: 10 rows). Raw output: scratchpad `fullgrid.json`.

## Verdict

All checks pass. Two data/engine anomalies (below) do not change the conclusions but E2 must surface them. **E2 UI may start.**

## 1. Per-template results

| Template | total / done / failed / cached | Runtime | panel_loads | grid_fraction | Plateau label | Region / upper set | Peak drop (tol .25) |
|---|---|---:|---:|---:|---|---|---:|
| momentum_trend | 36 / 36 / 0 / 0 | 540 s | 1 | 1.0 | plateau | 9 / 9 | 0.224 |
| quality_growth_momentum | 36 / 36 / 0 / 0 | 409 s | 1 | 1.0 | plateau | 9 / 9 | 0.240 |
| revenue_growth_acceleration | 36 / 36 / 0 / 0 | 256 s | 1 | 1.0 | plateau | 9 / 9 | 0.108 |
| value_quality | 48 / 48 / 0 / 0 | 194 s | 1 | 1.0 | plateau | 12 / 12 | 0.156 |
| low_volatility_quality | 36 / 36 / 0 / 0 | 514 s | 1 | 1.0 | plateau | 9 / 9 | 0.241 |

Total ≈ 33 min. Each job: universe ≈ 17 s + panel ≈ 13 s + points (4–15 s each).

## 2. Objective dispersion, cost, data-quality (window-level, all points; Sharpe is the default objective)

| Template | Sharpe min / med / max (top set med) | CAGR % med | MaxDD % med | Turnover % min–max | Cost drag pts min–max | unresolved_action_count min / med / max | warnings min / med / max |
|---|---|---:|---:|---|---|---|---|
| momentum_trend | 0.50 / 0.85 / 1.13 (0.99) | 26.4 | −47.7 | 2,867–5,260 | 18.9–34.1 | 7 / 14 / 30 | 11 / 39 / 214 |
| quality_growth_momentum | 0.99 / 1.18 / 1.36 (1.31) | 35.2 | −39.2 | 2,427–3,775 | 14.3–29.8 | 13 / 20 / 35 | 19 / 47 / 69 |
| revenue_growth_acceleration | 0.72 / 1.06 / 1.29 (1.23) | 26.7 | −42.4 | 3,105–6,233 | 17.5–37.1 | 13 / 22 / 36 | 52 / 71 / 101 |
| value_quality | 0.47 / 0.93 / 1.25 (1.15) | 16.1 | −29.4 | 1,874–2,725 | 7.3–11.7 | 4 / 11 / 22 | 65 / 89 / 128 |
| low_volatility_quality | 0.90 / 1.15 / 1.62 (1.45) | 15.3 | −18.9 | 1,328–2,844 | 5.7–11.1 | 4 / 11 / 26 | 7 / 19 / 56 |

Objective↔turnover Spearman: −0.65 / −0.03 / −0.43 / −0.67 / −0.73 (higher Sharpe generally trades less; no case where the top set is the highest-turnover set). Every run in every job has unresolved actions > 0 (`runs_with_unresolved_actions` share = 1.0); in-window count ≈ total. Turnover/cost figures are large because they are cumulative over 3.9 years for monthly equal-weight rebalancing (≈ 9× per year traded notional for momentum); cost drag is total-return points, gross − net.

## 3. Objective direction semantics — verified on real rows

For all 10 objectives × 5 jobs: the returned `objective_value` sequence equals the sorted sequence in the declared direction, `exploration_rank` is exactly 1..n, and no nulls.

- Higher = better: sharpe, sortino, calmar, cagr, net_return, total_return ✓
- Lower = better: annualized_volatility, turnover, cost_drag ✓
- **max_drawdown** (stored negative, e.g. −44.07): higher-is-better, i.e. closest to 0 first. Every job: all values < 0; first row is the least negative (−44.07, −30.30, −38.22, −27.03, −12.31), last the most negative (−54.07, −45.07, −48.14, −34.33, −26.00) ✓

## 4. Structural checks

- **panel_loads = 1** in all five jobs ✓.
- **No silently dropped points**: DB run rows = expansion = grid size (36/36/36/48/36), all unique, and the set of `point_key`s equals `search_space.plan()` output exactly ✓. `skipped_invalid = 0`.
- **No winner**: results only expose `exploration_rank`/`objective_value`; API notice + `ordering: "exploration order by objective; not a recommendation"`; robustness never names a chosen parameter set.
- **Cache rerun** (5/5): all runs `cached`, `run_backtest` calls **0**, `build_panel` calls **0**, panel_loads 0, stored metrics byte-identical, ≈ 16 s (universe recomputation).

## 5. Plateau labels vs the real grid — independent re-derivation

I recomputed neighbours (distance 1, weight moves in 0.2 units), peak drop, upper set and connected region from the stored Sharpe values with separate code: **peak drop, region size and upper-set size match the API for all 5 templates**. Neighbour counts 3–7 per point (a fully populated 2–4 axis grid; no sparse-grid artefacts). Stable points: 36/36, 34/36, 36/36, 48/48, 35/36.

Interpretation caveats for E2 (not bugs):
- All five come out `plateau` with region = the whole upper set; three peak drops (0.224, 0.240, 0.241) sit just under the 0.25 tolerance. The rule is permissive on smooth surfaces; it has not produced a `scattered`/`isolated_peak` on real data, so those branches are only unit-tested.
- **The top point sits on a search-range edge in every template** (`edge_of_search_range`): `selection.top_n` upper bound 50 for momentum_trend, revenue, value_quality (Sharpe rises monotonically with top_n: e.g. momentum 0.71 → 0.83 → 0.97); lower bound 20 for quality_growth_momentum (Sharpe falls with top_n); `volatility.lookback` 252 for low-vol. A plateau there means "the range may need extending", not "stable optimum". E2 must display this flag next to the label.
- Axis step-sensitivity ranking: top_n most sensitive in momentum/qgm/revenue/value; volatility lookback in low-vol; weight groups least (0.08–0.18 of spread).

## 6. Parameter surface for E2 heatmaps

Default surface (first two axes; others aggregated by median, each cell returns n/min/max): momentum 3×2 (n=6), qgm 2×3 (n=6), revenue 3×3 (n=4), value 3×4 (n=4), low-vol 3×3 (n=4) — complete grids, no empty cells (cells = x×y everywhere). Explicit weight-key axes also work (e.g. qgm quality×growth 6 cells, value.value × earnings_yield 16 cells n=3, low-vol weight × lookback 12 cells). Sufficient for heatmaps; E2 should expose x/y/agg pickers (4-axis momentum default hides 2 aggregated axes; `aggregated_over` lists them) and show n/min/max.

## 7. Warnings / coverage anomalies

1. **quality_growth_momentum is in cash for the first ~6 months.** Growth needs 8 visible quarters; assumed-mode financials start 2021Q1, so signals are empty for 2022-11 → 2023-04 (`signals_empty` up to 5 of 47, `selected_min` = 0, first fill 2023-05-02). All 36 qgm points share this, so within-template comparison holds, but its metrics include a cash period and are not comparable to other templates over identical dates. Flagged `empty_signals` on the rows.
2. **A suspended holding blocks all rebalances (engine semantics, not changed).** Stock 5481 has no quotes 2025-05-20 → 2025-09-18 (one of only two such gaps in the union; real suspension or source gap). With `missing.execution = next_available` the whole target order is deferred while any held name lacks an execution price, so runs that hold 5481 were effectively frozen for ~4 months. This explains momentum_trend's bimodal warnings (11–40 vs 178–214, `execution price missing … deferred`, `valuation is N sessions stale`). Measured impact on two affected points, with vs without 5481: Sharpe 0.713 vs 0.701 (lookback 189, top 20) and 0.884 vs 0.884 (252, top 30). Small here, but it is a Stage B design choice that E2/next stages should be aware of; I did not treat it as an optimization bug and changed nothing.
3. `adjusted-price residual jump >25%` warning appears in runs (known reference-price mismatch, factor series suspect for one name).
4. Panel coverage notes empty; `stocks_with_quotes` 1,346/1,346; 1,214 sessions; `quote_days_min` 154 (new listings). `fills_before_window` > 0 for short-lookback points (portfolio inherited from warm-up), as documented in the E1 report.

## 8. Not verified / limits

Everything is in-sample over one 3.9-year window; neighbourhood stability ≠ out-of-sample validity. Turnover/cost magnitudes come from Backtest Core definitions and were checked only for plausibility, not re-derived. Robustness on grids with a genuinely spiky surface has not occurred on real data.
