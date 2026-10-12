# Research Contracts

Every contract listed here is **currently in force** unless explicitly marked *(superseded)*. This file exists so a future change doesn't have to reconstruct "which rule is actually live" by reading every Stage report in order. Each entry names the module that implements it and, where one exists, its contract-version string — grep that string before changing behavior; a version string existing means an in-place behavior change is almost always wrong (bump the version instead), see `AGENTS.md`.

## 1. Publication date / point-in-time (PIT)

Module: `app/publication.py`, consumed by `panel.build_panel()` and every fundamentals-reading path.

- Every `month_revenue` / `financials` row carries `publish_source` ranked `actual` (3) > `observed_finmind` (2) > `assumed_deadline` (1, lowest). `upsert_many()` never lets a lower-ranked source overwrite a higher-ranked one, even on a primary-key conflict.
- `actual`: a real, sourced publication date. Free sources currently used do not supply this for financial statements.
- `observed_finmind`: month-revenue `create_time` from FinMind — when FinMind's own pipeline observed the row, **not** the company's real filing date.
- `assumed_deadline`: MOPS aggregate data with no per-row date, and any older revenue/financials row missing a real date, uses the regulatory filing deadline as a stand-in (Q1 = same year 5/15, Q2 = 8/14, Q3 = 11/14, Q4 = next year 3/31; overridable per stock/period via `STOCKS_PUBLICATION_OVERRIDES`). This is a placeholder, not evidence the filing actually happened on that date.
- `available_fundamentals(..., as_of=...)` only returns rows with `publish_date < as_of` (strict; same-day publication is not usable same-day) and excludes `assumed_deadline` rows **by default**. Passing `allow_assumed=True` is an explicit, disclosed research assumption — a caller using it must not claim "strictly no look-ahead bias" for that run. Every Walk Forward job that uses assumed fundamentals is stamped `publication_mode=assumed` and carries an `ASSUMED_PUBLICATION_MODE` caution warning.
- Publication rules changing later never retroactively recompute already-stored rows on startup.

## 2. Adjusted-price convention

- Adjusted (`adj_close`) prices exist **only** for factor calculations inside `app/services/strategy/factors.py` — never for execution, fills, marks, cash, or accounting.
- Every fill, mark-to-market, and cash flow throughout the engine, `portfolio.ledger`, and `portfolio.research` uses the raw (unadjusted) close/open. Mixing the two would silently create fictitious P&L across a corporate action.

## 3. Universe (point-in-time, no historical constituent list)

Module: `app/services/data_foundation.py`.

- `liquid_universe(conn, as_of, min_avg_amount, min_observations)`: trailing-year average traded amount as of a given date, minimum observation count, no fixed stock count. `stocks` is a **current** snapshot — there is no historical index-constituent table, so a universe computed "as of 2023" still only knows today's `listed_date`/`delisted_date`/`industry`.
- A full backfill/research job uses the **monthly union** of each month's qualifying universe across the window, not just the endpoint's universe, so a stock that only qualified briefly is still covered — but a stock with too few observation days never enters regardless.

## 4. Corporate actions

Module: `app/corporate_actions.py`, tables `corporate_actions` (raw + derived columns).

- `ratio` is a **reference-price ratio**, never interpreted as a share-count multiplier.
- `share_multiplier` is only set from a trusted source's explicit statement of how share count changed; a mixed cash+stock dividend that can't be split cleanly keeps `share_multiplier`/`cash_dividend` as `NULL` and is recorded `resolution=unresolved_*` — never guessed.
- The engine (`strategy/engine.py`), the exact sleeve ledger (`portfolio/ledger.py`), and the Portfolio research engine (`portfolio/research.py`) all apply an action identically: only on a position currently held (`shares > 0`), `share_multiplier` scales shares, `cash_dividend` adds `held_shares * dividend` to cash. An action with both fields `NULL` (unresolved) is a natural no-op — no special-casing needed anywhere that replays it.
- An unresolved action inside a training or OOS window is *always* `critical` severity (`UNRESOLVED_CORPORATE_ACTION` / `UNRESOLVED_ACTIONS_IN_WINDOW`) — this is a known, disclosed upstream data-quality limitation (see Stage F1.5 finding), not something later stages have silently "fixed" by softening the severity rule.

## 5. Backtest execution

Module: `app/services/strategy/engine.py` + `types.py` (`ExecutionRule`, `MissingDataRule`, `CostConfig`).

- Fills execute no earlier than T+1 relative to the signal date; `missing.execution="partial"` vs `"cancel"` governs what happens when an execution price is unavailable (`PARTIAL_REBALANCE`/`EXECUTION_DEFERRED` caution, or `EXECUTION_CANCELLED` critical).
- Transaction cost (`costs.calculate`) applies deterministic slippage, a rounded commission (`round(max(notional*rate*discount, minimum), 0)`), and sell-side tax — the same pure function is reused, unchanged, by the exact sleeve ledger and the Portfolio research engine (never a second, parallel cost model).
- Delisting, missing valuation price, and invalid `share_multiplier` are all `critical` (see the Warning Severity Contract, §11) — a backtest with any of these present must not be shown as a clean result.

## 6. Optimization / plateau

Module: `app/services/optimization/`. Purpose: identify a **robustness region**, not a single best point. There is no `winner`/`best` field anywhere in the API — a dedicated test scans every response to enforce this.

- A grid run over the training window only; a later job can be a disclosed *revision* of an earlier one (widened search space) via `parent_job_id`/`root_job_id`/`revision_no`/`revision_kind` — it never silently redefines the original job.
- Plateau/robustness detection groups nearby parameter points with similar objective performance into a region; a region's medoid (not necessarily the single best in-sample point) becomes the frozen candidate — see §7.

## 7. Candidate freeze (current version: `wf-freeze/f0.5/2`)

Module: `app/services/walkforward/freeze.py`. *(Supersedes `wf-freeze/f0/*` — see the superseded note below.)*

- **Plateau found**: the whole region is stored; **primary = the region's medoid** (minimum sum of graph distances to the region's other points; ties broken by lower turnover, then better objective, then lower `point_key`). The in-sample peak (`is_peak_key`) is reported only for comparison, never used as the primary by default.
- **No plateau**: not freezable by default → `NO_STABLE_CANDIDATE` (see §9). `require_plateau=False` can force-freeze the single top in-sample point, but the result always carries `FROZEN_WITHOUT_PLATEAU` plus a structural label (`ISOLATED_PEAK` / `SCATTERED_SURFACE` / `FLAT_SURFACE` / `INSUFFICIENT_ROBUSTNESS_DATA`).
- `edge_axes`: axes where the in-sample peak sits on the search-space boundary. Recorded and surfaced (`EDGE_OF_SEARCH_RANGE`, caution) — **never auto-expanded**. Search spaces staying boundary-constrained is a disclosed finding (Stage F1.5), not something to silently widen.
- **Superseded — Stage F0's original freeze rule**: said the frozen candidate should be *replayed from `panel_start`* with pre-test positions carried into OOS, and that primary = the top in-sample point. **Both are withdrawn as of Stage F0.5** (the candidate is only knowable at `train_end`; a "replay" would fabricate fills that could never have existed). The code today only defines `RULE_VERSION = "wf-freeze/f0.5/2"` (`app/services/walkforward/freeze.py`) — do not resurrect the withdrawn F0 behavior under any version string.

## 8. Independent vs. stitched OOS

Module: `app/services/walkforward/oos.py` (independent, primary) / `stitched.py` (secondary).

- **Independent fold (primary evidence)**: OOS ledger starts from **cash** at the fold's first test session. After freeze, data ≤ `train_end` may produce exactly one *boundary target* (the frozen candidate's own rule evaluated at `train_end`, forced even off-schedule); later rebalances follow the candidate's normal schedule. No frozen-candidate fill may exist before `test_start` — `run_oos_fold` raises on any such leak. Metrics window strictly to `[test_start, test_end]`.
- **Stitched (secondary)**: fold *k>0* may start from the **terminal state of the previous actually-run OOS fold** (never a training replay — that state source is explicitly rejected). Always labelled `evidence: stitched_secondary` and carries `STITCHED_NOT_INDEPENDENT_EVIDENCE`; a stitched fold following a `no_candidate` fold with real inherited positions liquidates them on its first session (`STITCHED_LIQUIDATION_AT_NO_CANDIDATE`).
- Stitched metrics are a **different statistic** from independent-fold metrics (each independent fold carries its own entry cost/ramp-up) and must never be merged or presented as equivalent. UI/API always keeps the two under separate keys.

## 9. No stable candidate (`NO_STABLE_CANDIDATE`)

- A fold whose training surface has no plateau (by default) freezes nothing; the fold is recorded `outcome=no_candidate`, holds 100% cash for its entire test window, and is a **normal, valid result** — not an error, not silently dropped from diagnostics, not treated as a zero-return data point when computing cross-fold statistics (Stage G explicitly excludes `no_candidate` folds from correlation rather than showing a fabricated zero).
- The same principle extends into the Portfolio layer (§13): a `no_candidate` **component** on a rebalance date keeps its entire capital slice in cash.

## 10. Fold-boundary integrity

- Cross-strategy correlation/overlap/drawdown analysis (`app/services/cross_strategy/`) only ever compares dates that genuinely overlap within a single fold's test window; a strategy's per-fold return series is never bridged across a fold boundary into a fake continuous series (`test_fold_boundary_is_never_bridged`, `test_drawdown_never_crosses_fold_boundary` exist specifically to catch this).

## 11. Warning severity contract (`warning-severity/f0/1`)

Module: `app/services/warning_contract.py`. Three levels, ordered `info < caution < critical`:

- **info**: context the reader should know; the result remains usable as stated.
- **caution**: usable only after reading the warning (a substitution, heuristic fallback, boundary/coverage limit).
- **critical**: the affected number is unreliable, or an intended trade did not happen — must never be presented as a clean result.

Every warning maps to exactly one severity through an ordered regex/code table; a message no pattern recognizes is `UNCLASSIFIED`/caution, **never** silently `info`. Codes (not message text) are the stable thing later stages/UI key on.

## 12. Exact sleeve contract (`portfolio-sleeve/h0/2`)

Module: `app/services/portfolio/contract.py` + `ledger.py`. This is the **only** sleeve representation a capital-allocation decision may read.

- Built by `ledger.replay_sleeve()`: a real, daily mark-to-market replay using only already-stored `fill` trades, `corporate_actions`, and `daily_quotes` closes — cross-checked field-by-field against the engine's own internal accounting (H0). Cash effect of a fill uses `fill_price` (post-slippage), never `reference_price`/`reference_notional`.
- Every day's row satisfies `sum(stock_weight_by_stock) + cash_weight == 1` (±1e-6). A `no_candidate` fold is 100% cash, empty holdings, zero return, every day.
- **Reconciled against the stored independent OOS `net_equity`** on every real job used so far (`reconciliation.py`) — a mismatch is a reported correctness issue, never silently normalized away.
- **Superseded — `cross-strategy-sleeve/g/1` (Stage G)**: an earlier, purely **descriptive** sleeve using entry-notional weights (`reference_notional / gross_equity` at entry, never revalued). Explicitly *not* for capital allocation. Still used by Stage G's own descriptive holdings-overlap analysis, but the Portfolio Engine (H0/H1) never reads it.

## 13. Portfolio allocation / netting / no-candidate cash (current: H1)

Modules: `app/services/portfolio/allocation.py`, `target.py`, `netting.py`, `baseline.py`, `constraints.py`, `research.py`.

- **Allocation methods**: `equal_weight` (1/N over the FULL component roster of the portfolio version) and `fixed_weight` (user-specified per-component target weight, `sum <= 1`, remainder = strategic cash) are fully implemented. `inverse_volatility` is fully implemented but only produces a result when `readiness.inverse_volatility_readiness` (fed leakage-filtered prior returns) reports the whole method `ready`; otherwise it returns `insufficient_evidence`/`unavailable` and the caller **keeps the previous rebalance target** rather than falling back to any other method. `risk_parity`/`minimum_variance`/`correlation_aware` are readiness-contract-only — calling them raises `NotImplementedError`.
- **No-candidate policy**: `cash_hold` is the only implemented policy — a component reporting `no_candidate` on a rebalance date keeps its entire allocation slice in cash, **never** automatically handed to another component. `redistribute_active` is a reserved-but-unimplemented name; passing it (or any policy value other than `cash_hold`) raises explicitly, at both portfolio-creation time and research-run execution time.
- **Aggregation order** (`target.resolve_rebalance_target`, fixed sequence): allocation method → no-candidate cash baseline → `max_strategy_weight` clamp → `netting.aggregate_stock_exposure` (every strategy's contribution to the same stock summed into ONE target weight) → `max_single_stock_weight` clamp → `min_cash` proportional haircut.
- **Constraints never redistribute a breach** — `max_strategy_weight` and `max_single_stock_weight` clamp the offending component/stock and push the excess into cash only; they never hand it to another component/stock. `min_cash` is the one rule that does a whole-portfolio proportional haircut, and it's applied last, separately from the no-redistribution rules above.
- **Execution nets before costing**: a rebalance computes exactly one target weight, and therefore at most one order, per stock — never one order per contributing strategy — so transaction cost is paid once on the net order (`costs.calculate`, reused unchanged from §5), never summed as if each strategy traded independently.
- **Point-in-time**: any input to a rebalance-date decision (candidate status, historical returns for `inverse_volatility`, holdings/prices) must be strictly at-or-before that date; `leakage.assert_point_in_time`/`prior_only_returns` are the guard, and `series.prior_returns` already only includes `date < as_of` rows.
- **Rebalance frequency**: only `"monthly"` (first common trading date of each calendar month) is implemented (`portfolio/calendar.py`); any other value raises `NotImplementedError`.
- **`max_sector_weight`**: reporting only, never enforced — `stocks.industry` is a current, non-point-in-time snapshot (same limitation as §3/§10's universe/sector caveats), so enforcing on it would launder a known data-quality gap into a "real" constraint.

## 14. Provenance / immutable versions

- **`strategy_versions`**, **`strategy_portfolio_versions`**: rows are immutable by service-layer convention — a definition change always `INSERT`s a new version, never `UPDATE`s an existing one. A run/research-run always pins the exact version (and, for a Strategy Portfolio, the exact `walkforward_job_id` evidence per component) it was computed from.
- **`walkforward_jobs`**, **`optimization_jobs`**: provenance columns (`strategy_id`, `strategy_version_id`, `strategy_version_no`, `definition_hash`) are write-once at the DB level (SQLite triggers in `app/db/migrations.py` abort any later change, including `NULL -> value`).
- **`portfolio_research_runs`**: pins `portfolio_id`, `portfolio_version_id`, a full `components_json` snapshot (strategy/version/job ids + target weights), allocation method/config, `no_candidate_policy`, and constraints at creation time. Editing the portfolio afterwards (which appends a new version) never changes what an already-completed run's stored result shows — enforced by pinning the version id, not by a DB trigger (same convention as the version tables above).
- **Portfolio research-run lifecycle** (Stage H1.5, `app/services/portfolio_lab.py`): `queued -> running -> completed|failed|cancelled`, same shape as the other three labs. `execute_research_run` only ever persists `portfolio_research_equity`/`portfolio_research_rebalances` rows atomically, at the very end of a full, uncancelled pass — a cancelled or server-interrupted run therefore never leaves a partial or misleading result on the DB, and `resume_research_run` always recomputes the whole window from scratch against the run's original pinned definition (never a silently different one). The atomic `status='queued' -> 'running'` claim (unchanged since Stage H1) is what makes a stray duplicate `BackgroundTasks` invocation for the same `run_id` a no-op rather than a duplicate execution.
- **Archiving** (`strategies`/`strategy_portfolios`, `archived=1`) is always a soft delete: versions, runs, and research history are never deleted by it.

## 15. Financial Planner investment source (Stage I1)

Modules: `app/services/finance/portfolio_source.py` (evidence, pin validation), `app/services/finance/service.py` (`_prepare_target`, `simulate`), `app/services/finance/allocation.py` (`source_types`), `app/services/finance/universe.py` (`portfolio_key`/`is_portfolio_key`, `meta(stock_id, source_type)`, the `strategy_portfolio`/`manual` pseudo-roles). This is the only place the Financial Planner (`app/services/finance/`) and the Strategy Portfolio Engine (`app/services/portfolio*`) touch — through `portfolio_lab`'s existing read-only functions only, never a new write path in either direction.

- **`finance_targets` gained six nullable columns** (`source_type`, `strategy_portfolio_id`, `portfolio_version_id`, `research_run_id`, `assumption_source`, `assumption_evidence_ref`; migration `schema_migrations` v5). `source_type IS NULL` is the pre-Stage-I1 row shape and behaves identically to `source_type='etf'` — no backfill, no behavior change for existing data.
- **Evidence vs. assumption, absolutely separated.** `portfolio_source.evidence(strategy_portfolio_id, portfolio_version_id, research_run_id)` is a read-only view built entirely from already-immutable data (`strategy_portfolio_versions`, `portfolio_research_runs` — see §14): CAGR/Sharpe/MaxDD/volatility, cash behavior, turnover/cost drag, warnings, research window. It never writes anything. `finance_targets.expected_return` is a separate column populated only by whatever value the caller of `save_profile()` explicitly sends — there is no code path from `evidence()`'s return value into `expected_return`, in either `service.py` or `portfolio_source.py`. `research_run_id=NULL` returns `{"available": false, "reason": "no_research_run_selected"}`, never a 500.
- **A `strategy_portfolio` target pins, never guesses.** `source_type='strategy_portfolio'` requires an integer `strategy_portfolio_id` + `portfolio_version_id`; `portfolio_source.validate_pin()` calls the existing `portfolio_lab.get_version`/`get_research_run` and rejects (ValueError -> 400) a version that doesn't belong to the portfolio or a `research_run_id` that doesn't belong to that exact version — it never resolves "the latest version" on the caller's behalf. `stock_id` is derived server-side as `portfolio_key(id)` ("portfolio:<id>"), never trusted from the caller, and that prefix is reserved (rejected on any other source type).
- **No role-based default return — or classification — for `manual`/`strategy_portfolio`** (see §16 for the Stage I1.1 hotfix that closed the classification half of this gap). `universe.ROLE_DEFAULTS["strategy_portfolio"]["assumed_return"]` is `None`; `universe.meta(stock_id, source_type)`'s `"manual"` branch gives `manual` targets the same `assumed_return=None` treatment, plus its own `role='manual'`/`asset='other'`/`region='unspecified'` classification — never `core_tw`/TW-equity. A legacy, un-typed custom ETF ticker (`source_type IS NULL`) keeps its pre-existing `core_tw` fallback unchanged, both for return AND for role/asset/region (see backward compatibility above). A target with no priced return is excluded from the weighted `expected_return` denominator (its weight/money is unaffected) and produces an `{"level": "assumption", ...}` warning naming it — never silently treated as 0%.
- **`assumption_source='research_informed'` requires all three**: `source_type='strategy_portfolio'`, an explicit `expected_return` in the same payload, and a `research_run_id` whose evidence is `available`. `assumption_evidence_ref` is an audit snapshot (`portfolio_version_id`, `research_run_id`, `snapshot_cagr`, `snapshot_asof`) computed server-side at save time from `evidence()`'s own result — it is never re-derived later and never feeds back into `expected_return`; the two are allowed to disagree (the snapshot records what the user *saw*, not what they *chose*).
- **Cash: Model A.** A Strategy Portfolio is one investment asset in the Planner (one `finance_targets` row); its internal strategic/no-candidate/execution-residual cash (§13) is never decomposed into the Planner's own cash fields. `actual_value` for a `strategy_portfolio` target is manual-or-`NULL` only — never populated from `portfolio_research_runs` equity (a backtest replay, not a real position) and never from `journal.positions()` (a Strategy Portfolio is never a real, executed position on this platform).
- **Portfolio state changes never mutate an existing Planner pin.** A new `strategy_portfolio_versions` row leaves every `finance_targets` row pinned to its old `portfolio_version_id`/`research_run_id` untouched (same immutability §14 already guarantees); `archived=1` still returns full evidence with an `archived: true` flag, never an error.
- **Scope after Stage I2**: Stage I1 was backend only; the Planner source-selection and evidence/assumption UI arrived in I2 (`web/finance.html` / `web/js/finance.js`). Still not built: a version-history system for `finance_profiles`/`finance_targets` themselves (they remain mutable "current settings"; only pinned Strategy Portfolio ids refer to immutable versions), or statistical/Monte-Carlo use of a portfolio return series (the Planner's scalar scenario-rate model remains unchanged). See `docs/history/stage-i1-planner-backend-integration-report.md` and `docs/history/stage-i2-planner-ui-integration-report.md` for the dated delivery boundary.

## 16. Manual investment source classification (Stage I1.1 hotfix)

Stage I1 correctly blocked `manual` from getting a role-based *default return* (§15), but its reporting *classification* still went through `universe.meta(stock_id)` with no way to tell it apart from a legacy, un-typed custom ETF ticker — so a `manual` target's role/asset/region silently fell back to `core_tw`/TW-equity, exactly the misclassification the Stage I0 contract said must not happen.

- **`universe.meta(stock_id, source_type=None)`** now takes an optional `source_type`. Passing `"manual"` returns a dedicated classification: `role="manual"`, `asset="other"`, `region="unspecified"`, `assumed_return=None` (via `ROLE_DEFAULTS["manual"]`) — never `core_tw`/`TW`/a numeric return. **Every existing call site that doesn't pass `source_type` is completely unaffected** (default `None` reproduces the exact pre-I1.1 behavior) — this is what makes the fix backward compatible without an explicit legacy branch.
- **Only an explicit `source_type='manual'` takes this path.** A legacy row (`source_type IS NULL`) or an explicit `'etf'` row goes through the same `core_tw`/TW-equity custom-ticker fallback as before Stage I1 — unchanged, on purpose, because that fallback is what existing saved profiles already depend on.
- **`allocation.allocate()`'s `no_role_default: set[str]` parameter (Stage I1) was replaced by `source_types: dict[str, str]`** (a superset — it now drives classification, not just the return fallback) so the role-weight grouping (`by_role`), not just the per-target `expected_return` fallback, stops lumping `manual` targets in with real `core_tw` ETFs when distributing the rule-engine's suggested weights.
- **`universe.default_return()`/`default_yield()`/`asset_of()` all gained the same optional `source_type` parameter**, closing a second leak Stage I1 missed: `service.py`'s portfolio-level dividend-yield aggregation (`port_yield`) called `default_yield(stock_id)` directly, bypassing the row's already-resolved role and picking up the legacy 3% `core_tw` yield for a `manual` target. `service.simulate()` now threads a `{key: source_type}` map through both call sites.
- **Money/return calculations are untouched**: `expected_return`, `initial_amount`/`monthly_amount`, `health`, `rebalance` all read the same fields they did before this hotfix; only `role`/`asset`/`region`/`dividend_yield` (the reporting/exposure side) changed for `manual` targets specifically.
