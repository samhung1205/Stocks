# AGENTS.md

Shared instructions for every coding agent in this repository. Maintain common rules here; `CLAUDE.md` is only an `@AGENTS.md` compatibility import, not a second rule source.

When architecture, a DB schema, an API contract, or a research/portfolio invariant changes, update this file and the relevant `docs/ARCHITECTURE.md` and/or `docs/RESEARCH_CONTRACTS.md` in the same change.

## Project and references

Personal Taiwan-stock analysis, strategy research, journal and financial planning platform. Local-first SQLite; no order placement. Local and private Railway databases are separate and do not synchronize automatically.

Choose references according to the task; `docs/README.md` is the documentation map:

- `README.md` / `PRODUCT.md`: user operations and product boundaries.
- `docs/DEVELOPMENT.md`: environment, test/backfill commands, local specifications and datasource notes.
- `docs/PROJECT_STATUS.md`: implemented features, limitations and dated real-data findings.
- `docs/ARCHITECTURE.md`: modules, DB/service/API boundaries and lifecycle details.
- `docs/RESEARCH_CONTRACTS.md`: research semantics, current contract versions and superseded rules.
- `docs/railway-deployment.md`: last verified deployment state; verify live state before claiming it is current.

Reports under `docs/history/` are dated evidence. Their test counts, future-work statements and superseded rules do not override current code or current references.

## Architecture and data boundaries

- **Invitation-only accounts are the default locally and on Railway:** `app/access.py` enforces server sessions, active-user checks, admin permissions and CSRF. Anonymous access is limited to GET/HEAD health and minimal login assets, plus login/token-completion POSTs. Missing initialization fails closed; bootstrap/recovery is local-CLI-only (`scripts/accounts.py`). `STOCKS_AUTH_MODE=basic` is explicit legacy single-user compatibility, never an automatic fallback or a multi-user solution. The last verified Railway deployment still uses Basic; new account migration/deployment is pending. See `docs/ACCOUNTS.md`.
- **Private Railway deployment:** one worker/replica, no sleeping, persistent volume at `/data`. Cloud startup requires an existing valid SQLite database; never silently initialize an empty cloud DB. Transfer a consistent SQLite backup, never a raw live WAL database or an old backup directory.
- **Private data follows its authenticated owner:** 12 private roots and 12 parent-scoped descendants are declared in `app/db/tables.py`. Private SELECTs must use `query_all/query_one` with fixed ownership CTEs; never bypass them with direct Core/driver SELECT or schema-qualified private tables. SQLite triggers enforce owner-preserving writes. Admin is not permission to read others' private records, pins, results or caches. Ignore client-supplied owner/role. Migration v7 reserves old data for owner id=1, rebuilds only mutable watchlist/scores PKs and preserves immutable research payloads/ids. `system_scope()` is restricted to operator initialization, startup recovery and shared-data maintenance, never HTTP handlers. Pure calculation code stays auth-free.
- **Account-mode research dispatch:** `app/services/research_queue.py` uses one FIFO worker, one outstanding top-level job per user, and captures caller identity for execution. Walk Forward's internal training remains in that worker. Never dispatch private work without its identity or reuse another owner's cache/results. Preserve existing pinned-definition, restart, cancel and resume contracts.
- **Import mode:** `scripts/railway-import-server.py` is health-only: no app import, DB access, file serving or jobs. Keep `STOCKS_IMPORT_MODE` off in normal operation; `status: importing` is not application/data readiness. Setup details are in `docs/railway-deployment.md`.
- **Taiwan scheduling:** set both `TZ` and `AUTO_REFRESH_TIMEZONE` to `Asia/Taipei` on Railway. Existing `CronTrigger` instances use process-local time when created; keep all scheduled jobs on the same clock.
- **SQLite + SQLAlchemy Core, no ORM:** table/column declarations live in `app/db/tables.py`; migration DDL and triggers live in `app/db/migrations.py`. Use `app.config.DB_URL` / `DATA_DIR`, which support `STOCKS_DB_URL` / `STOCKS_DATA_DIR` overrides and local defaults; never hardcode another machine-specific path.
- **Native HTML/CSS/JS + vendored ECharts:** no frontend framework or build step. Introducing a large frontend framework requires an explicit product decision.
- **Service/DB boundaries:** normal-app REST endpoints live in `app/api/routes.py`; new business logic belongs in services, with routes parsing parameters and calling services. Existing search, realtime, watchlist and admin-status routes contain direct DB access; do not extend those exceptions or refactor them in an unrelated task. The four `*_lab.py` modules own research persistence/versioning/lifecycle. Existing DB adapters are `strategy/panel.py`, `walkforward/freeze.py`, `cross_strategy/data.py` and `portfolio/data.py` under `app/services/`; calculation cores remain DB-free. Do not add DB access to a pure calculation module. See `docs/ARCHITECTURE.md` for adapter responsibilities.
- **Shared market-data writes:** use `app/db/__init__.py:upsert_many()`, upserting by primary key and updating only supplied columns. A sparse source must never NULL out a field supplied by another source.

## Research correctness

- **Point-in-time:** a decision at T uses only data knowable at or before T. Publication quality is `actual` > `observed_finmind` > `assumed_deadline`; `available_fundamentals(..., as_of=...)` requires `publish_date < as_of`, not same-day availability. Assumed dates remain disclosed assumptions (see research contracts §1).
- **Raw-price accounting:** adjusted prices are for factors only. Execution, fills, marks, P&L, cash and costs use raw close/open prices.
- **IS/OOS separation:** freeze candidates from training data only. Independent-fold OOS is primary evidence; stitched output is secondary and always carries `evidence: stitched_secondary` and `STITCHED_NOT_INDEPENDENT_EVIDENCE`. Never present the two as equivalent.
- **No OOS tuning:** search a fixed, disclosed training-window space; Walk Forward and Portfolio must not retune strategy parameters from OOS results. A changed search space/protocol is a disclosed new research revision with provenance (`revision_kind`, `parent_job_id` / `root_job_id`), never a silent redefinition.
- **Valid no-candidate outcome:** record `no_candidate`, hold the fold/component's capital in cash, and never silently drop the outcome or redistribute its allocation. Cash accounting may have 0% return; do not insert that as an evaluated strategy-return sample in cross-strategy/cross-fold statistics.
- **No silent research fallback:** thin samples, zero overlap or unready allocation must be disclosed as `insufficient_evidence` / `unavailable`; do not fabricate evidence or substitute another method. Unimplemented methods raise explicitly. This does not prohibit the separately documented Fugle-to-MIS quote fallback.
- **Unresolved corporate actions:** preserve `critical` severity for affected research windows; do not soften it to hide an upstream data-quality limitation. Whether particular real jobs remain affected must be verified, not inferred from an old Stage report.

## Strategy and Portfolio

- **Immutable strategy versions:** editing a definition appends `strategy_versions`; never UPDATE an existing version. Runs pin `version_id` and a self-contained executed snapshot.
- **Optimization explores robustness regions:** output is in-sample only; no `winner` / `best` response field (the test scans responses).
- **Candidate freeze:** use `wf-freeze/f0.5/2`, never an inline replacement. Default: plateau medoid, otherwise `NO_STABLE_CANDIDATE`. The existing explicit `require_plateau=False` exception must retain its `FROZEN_WITHOUT_PLATEAU` warning and structural label; see research contracts §7.
- **Generic Portfolio Engine:** components are `{strategy_id, walkforward_job_id}` with optional `target_weight` for `fixed_weight`. Allocation, target and execution code must not branch on strategy/template identity or assume a fixed component count.
- **Pinned component provenance:** derive `strategy_version_id` / `strategy_version_no` from the pinned Walk Forward job, never raw user input; verify the job belongs to the same `strategy_id`.
- **Immutable portfolio versions:** editing components, method/weights, no-candidate policy, rebalance, constraints or execution appends `strategy_portfolio_versions`; never UPDATE an existing version.
- **Exact sleeves only:** capital allocation reads `portfolio-sleeve/h0/2`. Stage G's `cross-strategy-sleeve/g/1` entry-notional approximation remains descriptive-only.
- **No-candidate cash:** only `cash_hold` is implemented, with no automatic redistribution. `redistribute_active` is reserved; non-`cash_hold` policies and `portfolio.baseline.apply_baseline(..., redistribute=True)` must raise.
- **Net before execution/cost:** aggregate contributions into one target and at most one net order per stock per rebalance. Charge costs once on the actual net order using the shared `strategy.costs.calculate()` model, never summed per-component. `netting.net_transaction_cost()` is an analytical helper, not a required call in the replay path.
- **Historical results:** each `portfolio_research_runs` pins `portfolio_version_id` and its component snapshot. Later portfolio edits must not change stored results.
- **Lifecycle:** startup recovery marks interrupted queued/running portfolio runs failed and resumable; preserve the atomic queued-to-running claim against duplicate execution. Persist results only after a full uncancelled pass. Resume recomputes using the original pinned definition; cancellation/checkpoint details are in `docs/ARCHITECTURE.md`.
- **Implemented limits:** only monthly rebalance; other frequencies raise `NotImplementedError`. `risk_parity` / `minimum_variance` / `correlation_aware` are readiness-only and `allocation.resolve()` must raise; implementing them is a separate feature with real-data validation. `max_sector_weight` is reporting-only because `stocks.industry` is not point-in-time.
- **Evidence floors:** do not lower `MIN_PAIRWISE_OVERLAP_DAYS` / `MIN_VOL_SAMPLE_DAYS` without explicit user sign-off. These are contract minimums, not statistical recommendations. Real-job readiness belongs in dated status/validation records.

## Daily auto-refresh

- **Market-data orchestration only:** `app/services/data_freshness.py` may write lifecycle metadata, but its only market-data write calls are official EOD `market_daily.update_daily()` / `market_daily.backfill_history()`. Never import/call `mis_snapshot` or `fugle`, or write provisional/intraday prices as completed daily closes. Intraday refresh requires a separate service and tables.
- **Missing-session evidence:** `completed` / `fresh` must be decided by missing trading sessions, never by `MAX(daily_quotes.date) >= target` alone. Write-path completion is decided by `_missing_sessions_for_write()` and rechecked after fetch, together with pipeline-result validation, never from calendar-day gaps or the result dict alone. **Open discrepancy:** current read-side `status()` can emit `fresh` with `internal_hole_check_source: unverified` when calendar coverage is absent. This does not satisfy the session-evidence requirement; see `docs/ARCHITECTURE.md` §8. Do not relax the invariant or change API behavior during an unrelated documentation task.
- **Shared writer lock:** `data_freshness.refresh_lock()` and `history_backfill.runner_lock()` intentionally use the same lock file for overlapping `daily_quotes` / `valuations` writes. Do not split them; new writers must join it. Existing deep-fetch overlap is covered by SQLite `busy_timeout`, not this lock; do not mistake that exception for permission to add another unlocked writer.
- **Separate research benchmark:** daily refresh never writes `benchmark_series` / `TAIEX_TR`; the historical backfill pipeline does, with no current scheduler guarantee of periodic execution. Global `freshness` covers the light layer only; keep the separate `research_benchmark` block and `tracked_by_daily_auto_refresh: false`.
- **Catch-up limits:** no automatic bootstrap of an empty `daily_quotes` dataset; use documented manual setup. Do not reintroduce per-candidate network probes in the tick fast path. Multi-day catch-up, calendar gaps and retry/checkpoint details are in `docs/ARCHITECTURE.md` §8 and the L1/L1.1 reports indexed there.

## Financial Planner

- **Evidence is not a planning assumption:** `finance/portfolio_source.evidence()` is read-only for pinned portfolio/version/run evidence. Only caller-supplied values populate `finance_targets.expected_return`; never copy CAGR/Sharpe or other research metrics into it automatically.
- **Explicit pins:** a `strategy_portfolio` target requires `strategy_portfolio_id` + `portfolio_version_id`, optionally `research_run_id`; validate their consistency through `portfolio_source.validate_pin()`, never resolve latest on behalf of the caller. Derive `stock_id` as `universe.portfolio_key(id)` (`portfolio:<id>`), never a ticker or caller-provided substitute.
- **Source-aware classification:** `manual` / `strategy_portfolio` targets get no ETF role-based return or `core_tw`/TW-equity classification. Manual means `role='manual'`, `asset='other'`, `region='unspecified'`, `assumed_return=None`; Portfolio has its dedicated role. Preserve the legacy custom-ticker fallback only for NULL / `etf` sources. Thread `source_types` through target calculations and source-aware `meta` / `default_return` / `default_yield` calls; do not restore bare calls that misclassify a manual target.
- **Unpriced returns:** keep the target's weight/money but exclude it from the weighted expected-return denominator; missing is not 0%.
- **Actual holdings:** a Portfolio target's `actual_value` is manual or NULL, never research equity or `journal.positions()`. The platform does not execute trades.
- **Nullable compatibility:** Stage I1's six added target columns remain nullable; NULL `source_type` retains legacy ETF semantics. Do not backfill existing rows.

## Working rules and completion

- Before editing a service, read its module docstring and relevant tests (not necessarily a one-to-one filename).
- Before changing a research/portfolio contract, read its current research-contract entry, defining Stage reports and pinned tests. Behavior changes need a new contract version unless the user explicitly requests a breaking change; that exception never permits bypassing immutable/PIT/OOS-separation safety. If a request requires such a violation, explain it before proceeding.
- Before editing stock analysis/scoring/screener, Planner, or Strategy Lab/Portfolio, consult the corresponding local spec when present; `docs/DEVELOPMENT.md` lists the three ignored `spec/` files. For datasource changes, consult its datasource notes and applicable tests. Specs/memory may be absent in another checkout; report missing context instead of inventing it.
- Before a destructive operation or migration on the real DB, create a consistent backup using SQLite's backup API (`scripts/backup_sqlite.py`); never copy a live WAL DB directly.

Definition of Done:

- For code changes, relevant tests pass; research/portfolio behavior changes require the full unittest suite. Offline tests are not necessarily read-only: fixture-gated Portfolio/Planner integration tests write to the configured DB. Check the test setup and use an isolated DB copy when real fixtures are needed; commands and smoke-test side effects are in `docs/DEVELOPMENT.md`.
- Run `compileall` on changed Python code. Documentation-only changes require verified references/commands and a clean diff, not application tests or Python compilation.
- Every endpoint, script path and command introduced into documentation is checked against current source/configuration. Do not reuse historical test counts as current verification.

## Git attribution

- Use the repository owner's existing Git identity; never change `git user.name` / `git user.email`.
- Before committing, verify identity from the repository's existing Git configuration (`git config user.name`, `git config user.email`); never copy personal email addresses into documentation.
- Never add an AI assistant as author, committer or co-author, or add AI-generated `Co-Authored-By` trailers. Preserve legitimate human contributors/co-authors.
- Assistance with add/commit/push does not change human attribution. Perform commits/pushes only within the user's authorized scope.
