# AGENTS.md

Canonical, cross-agent (Claude Code, Codex, or any other coding agent working in this repo) source of truth for what must never break. Tool-specific workflow notes belong in that tool's own file (e.g. `CLAUDE.md`), not here — this file is the shared contract.

If you change architecture, a DB schema, an API contract, or a research/portfolio invariant listed here, update this file **and** `docs/ARCHITECTURE.md` / `docs/RESEARCH_CONTRACTS.md` (whichever applies) in the same change. Stale invariants here are worse than no documentation — a future agent will trust this file.

## What this is

Personal Taiwan-stock research platform: fundamental analysis + a full Strategy Lab research chain (Backtest → Optimization → Walk Forward/OOS → Cross-Strategy analysis → Generic Strategy Portfolio). Local-first, free data sources, single-file SQLite (`data/stocks.db`). Does not place trades. See `docs/ARCHITECTURE.md` for module map and `docs/RESEARCH_CONTRACTS.md` for the full contract catalog — this file only states the invariants an agent must not violate.

For a new task, start with `docs/README.md`: it separates current references from dated history. Use `README.md` for user operations, `docs/DEVELOPMENT.md` for environment/test/backfill commands, `docs/PROJECT_STATUS.md` to determine what is implemented, `docs/ARCHITECTURE.md` for code boundaries, `docs/RESEARCH_CONTRACTS.md` for research semantics, and `docs/railway-deployment.md` for the last verified deployment state. Reports under `docs/history/` are point-in-time evidence; their historical test counts and future-work statements do not supersede current code or these references.

## Architecture invariants

- **Railway deployment is private and single-process.** `app/access.py` protects the entire app (including static pages and API docs) with HTTP Basic credentials from `STOCKS_AUTH_USERNAME` / `STOCKS_AUTH_PASSWORD`; Railway cannot disable this protection and missing credentials fail closed. Only GET/HEAD `/api/health` is public and reveals no personal data. The deployment uses one worker/replica, no sleeping, and a volume mounted at `/data`. Existing SQLite is required before cloud startup; never silently initialize an empty cloud DB. Transfer a consistent SQLite backup, never a raw live WAL database copy or old backup directory.
- **First Railway import may use a separate health-only process**, `scripts/railway-import-server.py`, because Railway file commands require an active deployment. It never imports the app, opens a database, serves files or runs jobs; `STOCKS_IMPORT_MODE` must be off for normal operation. Import-mode health (`status: importing`) is not proof that the application/data is ready.
- **Railway scheduling uses Taiwan time.** Set both `TZ` and `AUTO_REFRESH_TIMEZONE` to `Asia/Taipei`; existing `CronTrigger` instances use the process-local timezone when created. Keep daily refresh and the other scheduled jobs on the same Taiwan clock.

- **SQLite local-first, SQLAlchemy Core (no ORM).** `app/db/tables.py` is the ONLY schema definition source. `DB_URL` / data directory always come from environment variables (`STOCKS_DB_URL`, `STOCKS_DATA_DIR`) — never a hardcoded local path, since a future Postgres/cloud move must not require code changes.
- **Native HTML/CSS/JS frontend + vendored ECharts.** No React/Vue/Angular/build step. Do not introduce a large frontend framework without an explicit product decision from the user.
- **Service / DB boundaries.** `app/api/routes.py` is the only REST layer and stays thin (parameter parsing + calling a service; no business logic). Business logic lives in `app/services/*.py` and `app/services/<subpackage>/`. A `*_lab.py` module (`strategy_lab.py`, `optimization_lab.py`, `walkforward_lab.py`, `portfolio_lab.py`) is the ONLY place that touches the DB for its layer; the pure-function core it wraps (`app/services/strategy/`, `app/services/optimization/`, `app/services/walkforward/`, `app/services/portfolio/`) must stay DB-free so it can be unit tested offline.
- **`app/db/__init__.py:upsert_many()`** is the shared write path for market-data tables: upsert by primary key, only update columns actually present in the batch (never NULL out a field a different source didn't supply this run).

## Research correctness invariants

- **Point-in-time only.** Any decision dated T may use only data knowable at or before T. Fundamentals follow the publication-date contract: `actual` > `observed_finmind` > `assumed_deadline`; `available_fundamentals(..., as_of=...)` only returns rows with `publish_date < as_of`.
- **Adjusted prices are for factor calculations only.** Execution, fills, P&L and accounting always use raw (unadjusted) close/open prices. Never let an adjusted price leak into a cash or cost calculation.
- **IS/OOS separation.** A Walk Forward candidate is frozen from its training window only; the independent-fold OOS series is the primary evidence, the stitched series is secondary and must always carry `evidence: stitched_secondary` / `STITCHED_NOT_INDEPENDENT_EVIDENCE` — never presented as equivalent to independent-fold evidence.
- **No test-data tuning.** Optimization searches a fixed, disclosed search space over the training window only; nothing downstream (Walk Forward, Portfolio) may adjust a strategy's parameters based on OOS results.
- **Disclosed research revisions.** Widening a search space or changing a protocol is a new, recorded revision (`revision_kind`, `parent_job_id`/`root_job_id`) — never a silent redefinition of an existing job.
- **`no_candidate` is valid evidence.** A fold or a portfolio component that cannot produce a stable candidate holds 100% cash and is recorded as `no_candidate`, not dropped, not treated as a zero-return data point, and never silently redistributed to another strategy.
- **No silent fallback, anywhere.** Insufficient evidence (thin sample, zero pairwise overlap, an unready allocation method) must return `insufficient_evidence` / `unavailable` and stop there. Never substitute a guess, a default, or a different method without saying so.

## Strategy invariants

- **`strategy_versions` rows are immutable.** Editing a strategy's definition appends a new version; a run always pins `version_id` and stores a self-contained snapshot of what it executed. Never `UPDATE` an existing version row.
- **Optimization finds robustness regions, not a winner.** There is no `winner`/`best` field anywhere in the Optimization API (a test enforces this by scanning every response). Output is exploratory, in-sample candidate regions only.
- **Walk Forward candidate freeze uses the current candidate-rule contract** (`wf-freeze/f0.5/2`: plateau medoid, else `NO_STABLE_CANDIDATE`). Don't hand-roll a different freeze rule inline.

## Portfolio invariants

- **The Portfolio Engine is generic — NEVER hard-code the current five strategies.** A component is always `{strategy_id, walkforward_job_id}` (optionally `target_weight` for `fixed_weight`); allocation/target/execution code must never branch on a template name or strategy identity. If you find yourself writing `if template == "momentum_trend"` anywhere under `app/services/portfolio/`, that is a contract violation.
- **A Strategy Portfolio component pins the exact strategy version AND the OOS evidence.** `strategy_version_id`/`strategy_version_no` are derived from the pinned `walkforward_job_id`'s own provenance at creation time — never accepted as raw user input — and the job must belong to the same `strategy_id`.
- **`strategy_portfolio_versions` rows are immutable**, same convention as `strategy_versions`: editing components, allocation method/weights, `no_candidate_policy`, rebalance config, constraints, or execution config appends a new version.
- **`portfolio-sleeve/h0/2` (the exact, daily mark-to-market sleeve) is the only sleeve contract a Portfolio allocation method may read.** Never use Stage G's `cross-strategy-sleeve/g/1` entry-notional approximation for anything that allocates capital — it is explicitly descriptive-only.
- **`no_candidate` default = `cash_hold`, and there is no automatic redistribution.** `redistribute_active` is a reserved-but-unimplemented policy name; any code path that would silently start redistributing a no-candidate component's capital to other components is a bug. Both `portfolio.baseline.apply_baseline(..., redistribute=True)` and `no_candidate_policy != "cash_hold"` must raise, not degrade gracefully.
- **Aggregate/net strategy exposures BEFORE execution.** Every stock gets exactly one aggregate target weight (`netting.aggregate_stock_exposure`) and exactly one net order per rebalance — never one order per contributing strategy.
- **Transaction costs apply to the net portfolio order only**, never summed per-component (`netting.net_transaction_cost`, never `naive_summed_cost`, in the real execution path).
- **Historical `portfolio_research_runs` must stay reproducible.** A run pins `portfolio_version_id` and a snapshot of its components; editing the portfolio afterwards must never change what an existing run's stored result shows.

## Daily auto-refresh invariant (Stage L1, hardened L1.1)

- **`app/services/data_freshness.py` is orchestration only and must never grow a second write path.** The only two functions it may call to actually write market data are `market_daily.update_daily()` and `market_daily.backfill_history()` — both daily/end-of-day, official-source pipelines. It must never import or call `app.datasources.mis_snapshot`/`app.datasources.fugle` (intraday/live-quote sources), never write a provisional/intraday price as a completed daily close, and never mark a `daily_refresh_runs` row `completed` from anything but those two functions' own result. A future intraday/live-quote feature (Stage L2+) is a **separate** service with its own tables — it must not be bolted onto this module or onto `daily_quotes`' existing meaning ("official daily close").
- **`completed`/`fresh` must be decided by actual missing trading sessions, never by `MAX(daily_quotes.date) >= target` alone.** A later date can be present while an earlier session is still missing (Stage L1.1's fixed bug); `_missing_sessions_for_write()` is the only place allowed to decide this, and completion is always re-verified against it *after* a fetch, not assumed from a raw calendar-day gap or from the fetch pipeline's own return dict.
- **`app.services.data_freshness.refresh_lock()` and `app.services.history_backfill.runner_lock()` share the same lock file on purpose (Stage L1.1)** — both write overlapping tables (`daily_quotes`, `valuations`). Do not give either one its own private lock file again; that reintroduces the exact cross-runner race this hotfix closed. If a new writer to those tables is added, it must join this same lock, not invent another one.
- **The Strategy Lab research benchmark (`benchmark_series` / `benchmark_id='TAIEX_TR'`) is never touched by daily auto-refresh** — only `history_backfill.py`'s periodic historical job writes it. `data_freshness.status()`'s global `freshness` field covers the light layer only (`daily_quotes`/`valuations`/`institutional_daily`/`market_summary`); its separate `research_benchmark` block must keep saying so explicitly (`tracked_by_daily_auto_refresh: false`) rather than letting a caller infer the benchmark is current from the light layer being "fresh".

## Financial Planner invariants (Stage I1)

- **Strategy Portfolio evidence and Planner planning assumptions are separate, and nothing may silently copy one into the other.** `app/services/finance/portfolio_source.evidence()` is a read-only view of a pinned, already-immutable `strategy_portfolio_id`/`portfolio_version_id`/`research_run_id`; `finance_targets.expected_return` is a separate column that only ever receives whatever value the caller explicitly sent. Never write a portfolio's CAGR/Sharpe/etc. into `expected_return` from inside `service.py`/`portfolio_source.py` — if you find yourself doing that, stop, it violates the same "no silent fallback" principle as the research contracts below.
- **A `finance_targets` row with `source_type='strategy_portfolio'` pins `strategy_portfolio_id` + `portfolio_version_id` (never just a name), and the `stock_id` column holds the opaque key `universe.portfolio_key(id)` ("portfolio:<id>")** — never a real ticker. `portfolio_source.validate_pin()` is the only place that may accept these ids from a caller; it never resolves "latest version" on the caller's behalf.
- **`manual` and `strategy_portfolio` sources never get a role-based default return, or a `core_tw`/TW-equity classification (Stage I1.1 hotfix).** `universe.meta(stock_id, source_type)` only takes the legacy `core_tw`/TW-equity custom-ticker fallback when `source_type` is `None`/`'etf'`; an explicit `source_type='manual'` gets its own `role='manual'`/`asset='other'`/`region='unspecified'` classification with `assumed_return=None` (same as `strategy_portfolio`'s dedicated role). `allocation.allocate()`'s `source_types: dict[str, str]` parameter is what threads this through — do not reintroduce a bare `universe.meta(stock_id)`/`default_return(stock_id)`/`default_yield(stock_id)` call anywhere in `app/services/finance/` for a `manual` or `strategy_portfolio` target; it will silently reproduce the misclassification this hotfix closed. A target with no priced return keeps its weight/money but is excluded from the weighted `expected_return` denominator — never treated as a 0% return.
- **A Strategy Portfolio's `actual_value` in `finance_targets` is always manual or `NULL` — never derived from `portfolio_research_runs` equity, and never from `journal.positions()`.** The platform never executes trades (see PRODUCT.md), so there is no real position data source for a Strategy Portfolio; a research run's equity curve is a backtest replay, not anyone's real holdings.
- **`finance_targets` new columns (Stage I1) are all nullable; `source_type IS NULL` means the pre-Stage-I1 ETF behavior, unchanged.** Do not backfill existing rows.

## Known limitations that affect coding decisions

- Portfolio rebalance: only `frequency: "monthly"` is implemented (`app/services/portfolio/calendar.py`). Any other frequency must raise `NotImplementedError`, not silently default to monthly.
- `inverse_volatility` allocation is fully implemented but currently reports `insufficient_evidence` for the five real strategies (QGM has too few early evaluated folds) — do not "fix" this by lowering the evidence floor without the user's explicit sign-off; the floors (`MIN_PAIRWISE_OVERLAP_DAYS`, `MIN_VOL_SAMPLE_DAYS`) are documented contract minimums, not statistical recommendations.
- `risk_parity` / `minimum_variance` / `correlation_aware` are readiness-only (`portfolio.readiness.covariance_readiness`); calling `allocation.resolve()` with any of them raises `NotImplementedError`. Do not implement them casually inside an unrelated change — that is a real feature addition with its own real-data validation burden.
- `max_sector_weight` is reporting-only, never enforced (`stocks.industry` is a current, non-point-in-time snapshot — enforcing on it would silently launder a known data-quality limitation into a "real" constraint).
- `portfolio_lab.py` now has `recover_interrupted()` (Stage H1.5), called from `app/main.py`'s lifespan like the other three labs: a server restart mid-run marks any stuck `queued`/`running` `portfolio_research_runs` row `failed` (resumable via `resume_research_run`), never left stuck and never silently re-executed twice (the atomic `status='queued' -> 'running'` claim in `execute_research_run` already prevents a duplicate run). `cancel_research_run`/`resume_research_run` exist too, but cancel is genuinely cooperative only in the sense of "checked once per date in the daily replay loop" — this module's engine (`portfolio/research.py:run_portfolio_backtest`) is a single monolithic pass with no per-fold/per-point persisted progress like optimization/walkforward, so a cancelled run is simply discarded (no partial equity/rebalance rows) and a resumed run always recomputes its whole window from scratch against its original pinned definition.
- Unresolved corporate actions still make Walk Forward training-window severity universally `critical` (an upstream data-quality issue, not a bug in the severity logic) — this is expected and documented, not something to "fix" by softening severity classification.
- Strategy Portfolio UI exists at `web/portfolio.html` (Stage H2). Stage I2 added source selection, evidence viewing, explicit planning assumptions, and relink to `web/finance.html`/`finance.js` using the Stage I1 read-only evidence API; the Planner's existing seven-step flow and calculation model remain in place.
- Daily auto-refresh (Stage L1/L1.1, `app/services/data_freshness.py`) fetches via `market_daily.update_daily()` when at most one real trading session is missing, or `market_daily.backfill_history()` when more than one is (chosen from the actual missing-session count, not a calendar-day gap — see L1.1). It has no per-day-in-a-multi-week-gap checkpoint; a genuinely fresh install (no `daily_quotes` rows at all) is deliberately never auto-bootstrapped — that stays the documented manual first-time-setup flow, not something auto-refresh does on its own. Without `trading_calendar` coverage, a real holiday sitting in a gap can cost a few harmless extra retries before settling into `failed` (never a false `fresh`) — this is an accepted, documented tradeoff, not a bug to "fix" by adding a per-candidate network probe back in (that caused a real hang in `attempt_refresh()`'s per-tick fast path; see the L1.1 report §1). See `docs/history/stage-l1-daily-auto-refresh-report.md` and `docs/history/stage-l1-1-daily-refresh-hardening-report.md` for the full retry/concurrency design.

## Development

```bash
# run
uv run uvicorn app.main:app --port 8787

# primary test suite (offline unittest; a handful of real-DB integration tests
# auto-skip via skipUnless when their fixture data isn't present)
uv run python -m unittest discover -s tests -p 'test_*.py'

# smoke tests that hit real external APIs or run a real research pipeline —
# be polite with these, don't loop them
uv run python tests/smoke.py [sources|light|deep|finance]

# syntax/compile check — there is no configured linter or type checker
uv run python -m compileall app tests scripts
```

- **Before editing a service**, read its module docstring and the corresponding test file (`tests/test_*.py`) — most services here document non-obvious invariants inline (see e.g. `app/services/portfolio/ledger.py`, `app/services/portfolio/baseline.py`).
- **Before changing a research or portfolio contract**, find the Stage report(s) in `docs/history/stage-*.md` and `docs/RESEARCH_CONTRACTS.md` that define it, and the tests that pin it. A contract version string (e.g. `portfolio-sleeve/h0/2`, `wf-freeze/f0.5/2`) existing in the code means changing its behavior needs a NEW version string, not an in-place edit, unless the user explicitly asks for a breaking change.
- **Never bypass an immutable/point-in-time/OOS-separation contract because a prompt asked for something that seems to require it.** If a request appears to need violating one of the invariants above, stop and say so rather than finding a workaround that technically satisfies the request while breaking the contract.
- **After finishing a task, run the relevant test file(s)** (not necessarily the whole suite for a small change, but always the full suite before calling research/portfolio work done) and `compileall` on anything you touched.
- **DB backups**: before a destructive operation or a data migration against the real `data/stocks.db`, back it up via the SQLite backup API — never `cp` the file directly while WAL mode is active (you will get a torn/incomplete copy).

### Definition of Done

- The relevant test suite passes (and the full suite passes before a research/portfolio change is called complete).
- `python -m compileall` is clean on anything touched.
- No invented API endpoint, script path, or command appears in any doc this change touches — every path/command mentioned was verified to exist.
- If architecture, a DB schema, an API contract, or a research/portfolio invariant changed: `AGENTS.md`, `docs/ARCHITECTURE.md` and/or `docs/RESEARCH_CONTRACTS.md` are updated in the same change, not deferred.
