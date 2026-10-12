# Project Status

Working-tree status as of 2026-10-06. The v1.0.0 tag is the Stage K2 checkpoint; Stage L1/L1.1 and the Railway deployment are later working-tree work, not part of that tagged release. This file states what is implemented and what remains open. The documentation map is [README.md](README.md); dated stage reports live in `docs/history/`, and research semantics live in [RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md).

## Completed stages (A → L1.1)

| Stage | Delivered |
|---|---|
| A / A+ | Data correctness foundation: publication-date contract, adjusted-price separation, corporate-action resolution, trading calendar, historical backfill job system |
| B | Backtest core engine (pure functions): panel, signals, execution, costs, metrics |
| C | Five strategy templates (quality_growth_momentum, revenue_growth_acceleration, momentum_trend, value_quality, low_volatility_quality) |
| D1 / D2 | Strategy Lab backend (strategies/versions/runs persistence) + UI (`strategy.html`) |
| E1 / E2 | Optimization + robustness-region backend and UI (no "winner" field) |
| F0 / F0.5 | Walk Forward / OOS research contract (candidate freeze, independent-fold state) — F0.5 is a hotfix that supersedes two F0 rules (see `docs/RESEARCH_CONTRACTS.md` §7) |
| F1 / F2 | Walk Forward backend (fold loop, independent + stitched OOS) + UI |
| G | Cross-strategy descriptive analysis (correlation, holdings/sector overlap, drawdown complementarity, candidate availability) — read-only, no UI |
| H0 | Portfolio Lab data/capital contract: exact daily mark-to-market sleeve, netting, no-candidate baseline, allocation-method readiness, leakage guards — contract only, no allocation algorithm, no UI |
| H1 | Generic Strategy Portfolio engine: arbitrary multi-strategy portfolios, equal/fixed/inverse-volatility allocation, constraints, monthly rebalance, net execution, full CRUD/versioning/research-run API — backend only, no UI |
| H1.5 | Portfolio reliability + real-validation gate: `portfolio_lab.recover_interrupted()` + cancel/resume, combined-constraint/fold-boundary/execution-accounting regression tests against real data, H2 API-readiness audit — see `docs/history/stage-h1-5-portfolio-validation-report.md` |
| H2 | Strategy Portfolio UI (`web/portfolio.html`): list, six-step builder, versioned detail, research lifecycle/results, aggregate holdings, rebalance diagnostics, mobile and keyboard verification — see `docs/history/stage-h2-portfolio-ui-report.md` |
| I0 | Financial Planner × Strategy Portfolio integration *contract* (no code): investment source model, research-evidence-vs-planning-assumption separation, provenance, cash semantics — see `docs/history/stage-i0-planner-integration-contract-report.md` |
| I1 | Financial Planner × Strategy Portfolio *backend* integration: `finance_targets` gains a `strategy_portfolio` investment source (pinned version + optional research run), a read-only evidence API, and a strict evidence/assumption separation guard — backend only, no UI — see `docs/history/stage-i1-planner-backend-integration-report.md` |
| I1.1 | Hotfix: `source_type='manual'` no longer inherits the `core_tw`/TW-equity custom-ticker classification (only the return fallback was fixed in I1) — new `manual` role (`asset='other'`, `region='unspecified'`), legacy `source_type IS NULL` behavior unchanged — see `docs/history/stage-i1-1-manual-source-hotfix-report.md` |
| I2 | Financial Planner UI integration: ETF/Manual/Strategy Portfolio source selection, separate research evidence and explicit planning assumption, pinned version/relink, fallback disclosure, mobile and keyboard checks — see `docs/history/stage-i2-planner-ui-integration-report.md` |
| J | Final system QA: research-integrity audit (contracts §1–16 all confirmed matching source), cross-layer provenance trace, DB/migration audit, API consistency audit, full frontend walkthrough. Two real bugs found and fixed (`/journal/position-size`, `/finance/simulate` leaked 500s on bad input instead of 400) with regression tests; README/PRODUCT.md corrected for several stale "not built yet" claims about Strategy Portfolio UI and Planner integration that Stage H2/I1/I2 had already shipped — see `docs/history/stage-j-final-system-audit-report.md` |
| K1a / K1b / K2 | Design system + visual rollout + final visual QA across the whole app (Academic Research Desk C+) — see `docs/history/stage-k1a-design-system-report.md`, `docs/history/stage-k1b-design-rollout-report.md`, `docs/history/stage-k2-final-visual-qa-report.md`. v1.0.0 tagged after this stage. |
| L1 | Daily auto refresh + data freshness status (post-v1.0.0 working tree): `app/services/data_freshness.py` orchestrates the existing `market_daily.update_daily()`/`backfill_history()` pipeline only (no second write path) — startup catch-up on a background thread, bounded-retry weekday scheduler tick, single-runner lock shared with manual update-daily/backfill, append-only `daily_refresh_runs` log, unified `/api/admin/data-freshness` status contract (fresh/partial/stale/failed/unknown, never a boolean), and a dashboard banner that never hides stale/partial/failed. Daily/end-of-day only — explicitly not an intraday/live-quote service. See `docs/history/stage-l1-daily-auto-refresh-report.md`. |
| L1.1 | Correctness hardening on L1 (no new features): catch-up strategy now keyed off the actual missing-trading-session count instead of a raw calendar-day gap, so an internal hole (a later date present, an earlier session still missing) can no longer be reported `fresh`; `refresh_lock()` and `history_backfill.runner_lock()` now share one lock file (they write overlapping tables); the Strategy Lab research benchmark (`benchmark_series`/`TAIEX_TR`) is now reported as its own untouched-by-daily-refresh field instead of being conflated with the light-layer `market_summary` date; `status()` caches the resolved trading day (default 60s TTL) so a 20s dashboard poll no longer means a 20s TWSE poll; watchlist deep-fetch after a completed refresh restored to auto-driven triggers only, matching the exact pre-Stage-L1 scheduler behavior. See `docs/history/stage-l1-1-daily-refresh-hardening-report.md`. |

Each stage's own report (`docs/history/stage-<n>-*.md`) is the detailed, point-in-time record of what was built and validated in that stage; this file is the rolled-up "what's true right now."

## Deployment state (last verified 2026-10-06)

- A private Railway Hobby deployment is live with one Uvicorn worker/replica, a persistent `/data` volume, Taiwan-time scheduling, and HTTP Basic protection for the app, static files and API docs. Only GET/HEAD `/api/health` is public. The owner confirmed login and normal use on 2026-10-06. See [Railway deployment record](railway-deployment.md) for evidence and setup details.
- Local and Railway SQLite files are separate; changes are not synchronized. The deployed account is shared access to one dataset, not a multi-user system.
- At the last data verification on 2026-10-05, daily refresh was `partial`: 2026-09-29 and 2026-09-30 sessions remained missing; valuation and research benchmark dates lagged. No later data check is recorded here. Read the live freshness response before claiming the cloud data is current.

## Currently usable features

- **Fundamental analysis platform**: dashboard, stock detail (kline, revenue, financials, chips, valuation, technical, scorecard, alerts), whole-market screener, tiered watchlist with near-real-time quotes, trade journal, financial/ETF planning (`finance.html`) — all with a working UI.
- **Strategy Lab** (`strategy.html`): create/version/run any of the five strategy templates against the real local dataset; view equity curves and trades.
- **Optimization**: grid search + robustness-region detection over a strategy's training window, from the same UI.
- **Walk Forward / OOS**: run a full walk-forward protocol, inspect independent-fold and (secondary) stitched results, per-fold severity and diagnostics.
- **Cross-Strategy analysis**: `POST /api/strategy-lab/cross-strategy/analyze` — descriptive comparison across multiple completed Walk Forward jobs. API only; no dedicated UI (must be called directly, e.g. via a script or an API client).
- **Strategy Portfolios**: full CRUD/versioning/clone/archive + research-run lifecycle (create/cancel/resume, crash-recoverable at startup) under `/api/strategy-lab/portfolios` and `/api/strategy-lab/portfolio-research-runs`. Real portfolios exist in the local dev DB today (`H1 smoke: Core Five`, `H1 smoke: Momentum + Value`, `H1 smoke: Custom Mix`, built from `scripts/h1_portfolio_smoke.py`) with completed research runs. Available in `web/portfolio.html` through the existing API.
- **Daily auto refresh + freshness status (Stage L1)**: the light-layer dataset (`daily_quotes`/`valuations`/`institutional_daily`/`market_summary`) self-heals on server startup and on a bounded weekday poll — no manual daily click needed unless the retry budget is exhausted or the DB has no baseline yet. `GET /api/admin/data-freshness` is the unified status contract; the dashboard banner (`web/index.html`) reflects it live and never hides stale/partial/failed/running states. Daily/end-of-day only, not intraday.

## Known limitations

- **Portfolio holdings are rebalance-date snapshots only.** The UI labels them as such; it does not show live or daily holdings.
- **Financial Planner ↔ Portfolio Engine integration (Stage I2)** is available in `finance.html`: a Planner target can pin a Strategy Portfolio version and optional completed research run, view read-only evidence, then separately confirm a planning return. Selecting a Portfolio or run alone never fills the return.
- **Portfolio rebalance**: only `monthly` frequency is implemented.
- **`inverse_volatility` allocation**: implemented, but the current five real strategies' Walk Forward evidence is `insufficient_evidence` for it (QGM has too few early evaluated folds) — no real portfolio has run with this method producing an actual allocation yet.
- **`risk_parity` / `minimum_variance` / `correlation_aware` allocation**: not implemented (readiness-contract only); calling them raises `NotImplementedError`.
- **`max_sector_weight` constraint**: reporting only, never enforced (current, non-point-in-time sector taxonomy).
- **Portfolio research-run cancel is coarse**: a cancelled run is always fully discarded (no partial equity/rebalance rows — this module has no per-fold/per-point checkpoint like optimization/walkforward) and a resumed run always recomputes its whole window from scratch, deterministically, against its original pinned definition. Given real windows finish in well under a second, this is an accepted, disclosed simplification (Stage H1.5), not a bug.
- **Unresolved corporate actions** still make Walk Forward training-window severity universally `critical` — a known upstream data-quality issue, not yet resolved.
- **Search spaces are boundary-constrained** in every real optimization/freeze run so far (the in-sample peak sits on a search-range edge in 12/12 evaluated Stage F1.5 folds) — widening them would be a new, disclosed research revision, not something to do silently.
- **No historical index-constituent list** — universe selection always uses current `stocks` metadata (listed/delisted dates, industry), never a point-in-time constituent snapshot.
- **Fundamentals are `assumed`-mode only for financial statements** — free sources don't supply a real `actual` publication date for financial statements; only month-revenue can reach `observed_finmind`.
- **Daily auto refresh (Stage L1/L1.1) catches up via `update_daily()` when at most one real trading session is missing, or reuses the existing multi-day `backfill_history()` when more than one is** (chosen from the actual missing-session count, not a calendar-day gap — see L1.1). A brand-new empty DB is never auto-bootstrapped — first-time setup stays the documented manual flow. Without `trading_calendar` coverage, a holiday sitting in a gap can cost a few harmless extra retries before settling `failed` (never a false `fresh`) — an accepted tradeoff, not a bug. It is not an intraday/live-quote service (see `docs/history/stage-l1-daily-auto-refresh-report.md`, `docs/history/stage-l1-1-daily-refresh-hardening-report.md`).
- **The Strategy Lab research benchmark (`benchmark_series`/`TAIEX_TR`) is never updated by daily auto-refresh** — only the periodic Strategy Lab historical backfill (`history_backfill.py`) does. `GET /api/admin/data-freshness`'s `research_benchmark` block reports this explicitly and separately from the light-layer `freshness` field.

## Not yet started / not in scope so far

- **H2+**: real-data validation of `inverse_volatility` once evidence improves; any decision about whether/how to implement `risk_parity`/`minimum_variance`/`correlation_aware`; rebalance frequencies beyond monthly; `max_sector_weight` enforcement (would need a point-in-time sector taxonomy first).
- **Financial Plan versioning**: `finance_profiles`/`finance_targets` remain the pre-existing mutable "current settings" model (in-place overwrite on save) — a known, pre-Stage-I1 gap Stage I1 did not attempt to fix. Only the pinned Strategy Portfolio ids inside a target row are immutable-by-reference (they point at data that itself never changes).
- **Further polish**: long-running cloud research workload and continued backup/monitoring have not been verified end to end; broader mobile improvements remain optional future work. Initial private deployment hardening is complete, as recorded above.
- **Intraday/live-quote auto refresh (Stage L2)**: not started, and explicitly out of Stage L1's scope. Stage L1 only ever writes official daily/end-of-day closes; a live-quote refresher would be a separate service with its own tables, never bolted onto `data_freshness.py`/`daily_quotes`. See "Stage L2 intraday readiness" in `docs/history/stage-l1-daily-auto-refresh-report.md`.

Nothing in this file should be read as "in progress" unless stated — an item is either done (above) or not started (here). Do not claim a not-yet-started item as complete in this file or in a PR/commit description.

## GitHub publication privacy boundary (2026-10-12)

The original Stage K2 `v1.0.0` checkpoint remains local. Its unpushed history includes UI screenshots with financial figures, so GitHub main is published from a clean snapshot based on the existing remote main, excluding those screenshots and runtime data. The original checkpoint/tag is not pushed or moved. Daily-refresh, private-deployment and current documentation sources are included; `.env`, databases/backups, local Railway configuration, browser output and research output remain local.
