# Architecture Reference

Repo-level architecture map for Stage L1/L1.1 plus invitation-only accounts (2026-10-14 source). Account migration and rollout have not been performed on the daily/production DB; last verified Railway state is tracked separately in [railway-deployment.md](railway-deployment.md). Contract details live in [RESEARCH_CONTRACTS.md](RESEARCH_CONTRACTS.md), current scope in [PROJECT_STATUS.md](PROJECT_STATUS.md), and delivery history in `docs/history/stage-*.md`. If this file and the code disagree, fix the documentation in the same change that caused the drift.

## 1. Major modules

```
app/
  main.py                 FastAPI app + lifespan (cloud DB guard, recovery, scheduler)
  access.py               account/session/CSRF/admin middleware; explicit legacy Basic mode
  identity.py             trusted request/worker ContextVar identity + restricted system scope
  config.py                env vars (.env via a minimal self-written parser, no python-dotenv)
  scheduler.py              weekday bounded-poll daily auto-refresh tick (Stage L1), monthly
                              revenue (day 10-12), Sunday 03:00 deep-data cleanup
  api/routes.py             normal-app REST entry point; service delegation + legacy DB routes
  db/
    tables.py                table/column declarations (SQLAlchemy Core)
    __init__.py               engine, upsert_many(), query_all/query_one
    migrations.py             additive, transactional SQLite migrations + immutability triggers
    ownership.py              fixed private-read scopes + owner-preserving write triggers (v7)
  datasources/               twse / tpex / finmind / mops / mis_snapshot / fugle (+ base.py http client)
  services/
    accounts.py                                  invitations, Argon2id, sessions, assisted recovery
    research_queue.py                            one FIFO worker + per-user quota/identity capture
    market_daily.py, deep_fetch.py, stock_view.py, scoring.py,
    screener.py, alerts.py, journal.py             fundamental-analysis layer
    data_freshness.py                              Stage L1: daily auto-refresh lifecycle + freshness
                                                      status; orchestrates market_daily.py only, never
                                                      a second write path (daily/EOD light layer only)
    finance/                                        financial planning / ETF long-term investing;
                                                      portfolio_source.py (Stage I1) is its only link
                                                      to the Strategy Portfolio Engine, read-only
                                                      through portfolio_lab's existing functions
    strategy/                                        pure backtest calculations + panel.py DB adapter
    strategy_lab.py                                  Strategy Lab persistence/versioning/run lifecycle
    optimization/, optimization_lab.py               Optimization + robustness
    walkforward/, walkforward_lab.py                 Walk Forward / OOS
    cross_strategy/                                   cross-strategy descriptive analysis (read-only)
    portfolio/, portfolio_lab.py                      Generic Strategy Portfolio engine
web/                        native HTML/CSS/JS, vendored ECharts, no build step
tests/                      unittest (primary) + smoke.py (hits real external APIs / real pipelines)
scripts/                    real-data validation, consistent SQLite backup and Railway startup/import
docs/                       current references + deployment record; history/ for dated reports
```

## 2. DB / service / UI boundaries

- **Private Railway deployment**: `Dockerfile` installs the exact `uv.lock` dependencies; `.railway/railway.ts` uses the official IaC SDK to configure `/api/health`, one Singapore replica, no sleep and a persistent `/data` volume. Its npm package is infrastructure tooling only, not a frontend build step; install it with `npm ci --prefix .railway` before `railway config plan` / `apply`. `scripts/start-railway.sh` runs one Uvicorn worker. `STOCKS_DATA_DIR` / `STOCKS_DB_URL` point at that volume. `app/main.py` refuses a missing/invalid existing SQLite file in Railway before migrations/recovery start. `scripts/backup_sqlite.py` uses SQLite's backup API and verifies integrity for the initial transfer; backups and `.env` never enter the deployment archive/image. New Railway services cannot use the deprecated `railway.json` / `railway.toml` format (official docs verified 2026-10-05).
- **Account boundary**: default `STOCKS_AUTH_MODE=accounts` applies locally and on Railway. `AccountAccessMiddleware` validates opaque server-side sessions and active accounts per request, enforces admin-only routes, and requires a session-bound CSRF header plus same-origin checks for writes. Minimal login assets, health and login/token-completion POSTs are public; other pages/APIs require login. Sessions are hashed in SQLite; passwords use Argon2id. Cookie flags: HttpOnly session, Secure, SameSite=Lax; responses no-store/no-referrer. Explicit `basic` mode retains the old single-user behavior but supplies no multi-user isolation guarantee. There is no fallback from failed accounts initialization. See [ACCOUNTS.md](ACCOUNTS.md) for exact lifetimes, initialization, recovery and rollout.
- **Ownership boundary**: v7 adds owner metadata to 12 private root tables; 12 descendant tables inherit scope through existing parent ids. `query_all/query_one` prepend fixed CTEs that shadow private relations with the actor's rows (joins, subqueries, cache lookups included). All private SELECTs must use these helpers; direct Core/driver SELECT and schema-qualified access are reserved for the boundary/migrations, never services. DB triggers separately reject foreign/private INSERT/UPDATE/DELETE and owner changes. Admin has no private-read bypass. `system_scope()` is limited to trusted operator/startup recovery and shared-data maintenance. Existing immutable research rows retain their original fields/ids; only mutable watchlist/scores are rebuilt for per-user composite PKs.
- **Research dispatch**: account-mode top-level Backtest/Optimization/Walk Forward/Portfolio create/resume requests pass through `research_queue.submit()`: one process-local FIFO worker, one outstanding request per account, captured identity restored in the worker, and active-user check before execution. Internal Walk Forward optimization stays synchronous in that worker. Lab atomic claims and restart/cancel/resume contracts remain unchanged. Pure calculations do not import auth. Legacy mode retains BackgroundTasks.
- **Initial volume import**: Railway file operations need an active deployment. With `STOCKS_IMPORT_MODE=1`, `scripts/start-railway.sh` starts the separate `scripts/railway-import-server.py` health-only process. It returns `status: importing` at `/api/health`, rejects other GET requests, and never imports the app, opens SQLite, starts jobs or serves files. After the snapshot has uploaded and been verified, set import mode off and start the normal app. An import-mode health response never establishes application readiness.
- **Cloud scheduler timezone**: Railway sets both `TZ` and `AUTO_REFRESH_TIMEZONE` to `Asia/Taipei`. The existing `CronTrigger` instances default to the process-local timezone, so setting only the scheduler timezone would leave those triggers on the container's UTC clock. Verify the live triggers and next-run dates after deployment.

- **DB layer**: `app/db/tables.py` declares tables/columns; `app/db/migrations.py` applies transactional DDL and immutability triggers using that metadata (v7 rebuilds only mutable watchlist/scores). `app.config.DB_URL` / `DATA_DIR` centralize environment overrides and local defaults. Market-data writes use `upsert_many()` (partial-column-safe upsert). Private writes derive owner from trusted identity; private reads use the ownership boundary above. There is no ORM query layer.
- **Service layer**: the four `*_lab.py` modules own research persistence, versioning and run lifecycle. Their calculation cores are pure and tested with synthetic fixtures, but their packages are not entirely DB-free: `strategy/panel.py` reads DB facts into the PIT panel; `walkforward/freeze.py` reads training evidence and persists/opens immutable freezes; `cross_strategy/data.py` reads Walk Forward results through public accessors plus current sector metadata; `portfolio/data.py` loads exact-sleeve inputs. Keep DB access within those existing boundaries; calculation modules must not acquire DB dependencies. Cross-strategy analysis does not write or duplicate research data.
- **API layer**: `app/api/routes.py` is the normal-app REST entry point. New business logic belongs in services; routes should parse parameters, delegate and map errors. Existing stock search, realtime lookup, watchlist CRUD and admin-status routes contain direct DB access; these are existing exceptions, not a reason to add more. Health checks also query DB readiness directly. The shared `_lab()` helper maps `LookupError → 404`, `ValueError → 400`, `RuntimeError → 409` for Strategy Lab route families.
- **UI layer**: native JS, no framework. `web/js/common.js` provides the nav bar, an `api()` fetch wrapper, formatting helpers, and shared ECharts styling. `strategy.html` is a single page whose JS (`strategy.js` + `optimization.js` + `walkforward.js`) renders Strategy Lab, Optimization and Walk Forward as tabs/states of one app — there are no separate `optimization.html` / `walkforward.html` pages. Cross-Strategy analysis remains API-only. `portfolio.html` + `web/js/portfolio.js` provide the Strategy Portfolio list, builder, versioned detail, research/result and rebalance/holdings UI; `web/js/charts.js` renders compact portfolio charts from the existing read API. `finance.html` + `web/js/finance.js` retain the seven-step Planner and now select ETF, Manual, or a pinned Strategy Portfolio source; the latter reads `/api/finance/portfolio-evidence` and requires a separate user confirmation before setting a planning return.

## 3. Strategy research flow (Backtest core)

```
strategy definition (template_key + params + backtest overrides)
  -> strategy_lab.create_strategy / update_strategy   (strategy_versions: immutable, append-only)
  -> strategy_lab.create_run -> execute_run
       -> panel.build_panel()        point-in-time panel: bars, fundamentals (publish-date gated),
                                      valuations, corporate actions, benchmark
       -> strategy.run_backtest(panel, TemplateSignalRule, BacktestConfig)
       -> metrics.compute()           Sharpe/Sortino/CAGR/MDD/turnover/cost etc.
  -> backtest_runs / backtest_equity / backtest_trades persisted, run becomes immutable once completed
```

`app/services/strategy/` (`engine.py`, `signals.py`, `factors.py`, `rules.py`, `costs.py`, `metrics.py`, `panel.py`, `templates.py`, `types.py`) never imports `app.db` — `panel.build_panel` takes a DB engine argument explicitly from the caller, keeping the engine itself testable with synthetic data.

## 4. Optimization flow

```
search space + template + window
  -> optimization_lab.create_job                (job_hash caches identical requests; strategy/version
                                                  provenance pinned since Stage F0)
  -> optimization_lab.execute_job                runs the grid (optimization_runs, resumable, cache-aware)
  -> optimization/search_space.py                grid expansion, revision tracking
  -> optimization/robustness.py                  plateau / robustness-region detection over the surface
```

No `winner`/`best` field exists anywhere in this API — the deliverable is a robustness region (a plateau of parameter points with similar performance), not a single "optimal" point. A later job can be a disclosed *revision* of an earlier one (`parent_job_id`/`root_job_id`/`revision_kind`) when the search space is widened; it never silently redefines the original job's results.

## 5. Walk Forward flow

```
protocol (expanding/rolling, train/test lengths) + strategy definition
  -> walkforward_lab.create_job           freezes the fold plan (walkforward_folds, one row per fold)
  -> per fold, in order:
       training optimization (window = fold train window)
         -> walkforward/freeze.py           candidate freeze: plateau medoid, else NO_STABLE_CANDIDATE
         -> open_test_window                 (before any row dated after train_end loads)
         -> walkforward/oos.py               run_oos_fold: independent, cash-start, boundary-safe
         -> persist fold result               (one transaction; a completed fold is immutable — enforced
                                                by SQLite triggers in app/db/migrations.py)
  -> after all folds: walkforward/stitched.py builds the SECONDARY, non-independent stitched series
  -> walkforward/diagnostics.py                cross-fold descriptive summary (no significance claims)
```

Independent-fold OOS is the primary evidence; a `no_candidate` fold holds cash for its whole test window and is a normal, recorded outcome, not a failure. Completed folds and their `fold`-series equity/trades rows are append-only/immutable at the DB level.

## 6. Cross-strategy flow (Stage G, read-only)

```
POST /api/strategy-lab/cross-strategy/analyze  {"jobs": {strategy_key: walkforward_job_id, ...}}
  -> cross_strategy/data.py       re-reads existing walkforward_jobs/folds/equity/trades (read-only)
  -> behavior.py, correlation.py, overlap.py, exposure.py, drawdown.py, candidate_availability.py
       (all pure functions; correlation/overlap only compare REAL overlapping OOS dates, never bridge
       across a fold boundary)
  -> sleeve.py                     cross-strategy-sleeve/g/1: entry-notional approximate sleeve —
                                    descriptive only, NEVER used for capital allocation
```

Purely descriptive: no ranking, no "best strategy", no capital allocation weights. Nothing here is persisted — every call re-computes from the existing Walk Forward tables.

## 7. Generic Strategy Portfolio flow (Stage H0/H1)

```
H0 (contract only, no allocation algorithm):
  ledger.py         exact daily mark-to-market replay (fills + corporate_actions + daily_quotes)
  contract.py       portfolio-sleeve/h0/2: the exact sleeve, reconciled against stored OOS equity
  netting.py        aggregate_stock_exposure / net_orders / net_transaction_cost (pure)
  baseline.py       no_candidate -> cash, never redistributed
  readiness.py      per-allocation-method capability contract (ready/insufficient_evidence/unavailable)
  leakage.py        point-in-time guards (assert_point_in_time, prior_only_returns)

H1 (generic multi-strategy engine, built on the H0 contract):
  allocation.py     equal_weight / fixed_weight (real) + inverse_volatility (real, gated by readiness)
  constraints.py    max_strategy_weight / max_single_stock_weight (enforced) / min_cash (haircut);
                     max_sector_weight is reporting-only
  calendar.py        rebalance-date selection ("monthly" is the only implemented frequency)
  series.py          flattens a component's per-fold exact sleeves into one continuous date series
  target.py          per-rebalance-date target: allocation -> no-candidate cash baseline -> netting
                     -> constraint clamps
  research.py        the daily replay engine: corporate actions -> (on a rebalance date) trade toward
                     the aggregate target, net-then-cost, sells before buys, deterministic partial
                     execution on missing price / insufficient cash -> daily mark-to-market

portfolio_lab.py (DB-touching orchestration):
  strategy_portfolios / strategy_portfolio_versions / strategy_portfolio_components  (CRUD, versioned,
     each component pins strategy_id + strategy_version_id + walkforward_job_id)
  create_research_run -> execute_research_run
     -> loads each component's exact sleeve series, computes common trading dates, runs
        research.run_portfolio_backtest, persists portfolio_research_runs/equity/rebalances (sparse:
        one row per rebalance date, not per day) atomically -- ONLY on a full, uncancelled pass
  cancel_research_run / resume_research_run / recover_interrupted (Stage H1.5)
     -> same queued/running/completed/failed/cancelled lifecycle as the other three labs; cancel is
        cooperative (checked once per date inside run_portfolio_backtest's daily loop) and a
        cancelled/interrupted run is always fully discarded (no partial equity/rebalance rows --
        this module has no per-fold/per-point checkpoint), so resume always recomputes the whole
        window from scratch against the run's original pinned definition. Wired into app/main.py's
        lifespan like strategy_lab/optimization_lab/walkforward_lab.
```

The engine is generic over `component_keys: Sequence[str]` end to end — nothing under `app/services/portfolio/` branches on which strategies are in play. Stage H2 exposes this through `web/portfolio.html` using the existing API listed below. `scripts/h1_portfolio_smoke.py` remains a real-data validation script.

## 8. Daily auto-refresh flow (Stage L1, hardened in L1.1)

```
app.services.data_freshness (orchestration only -- never a second write path)
  attempt_refresh(trigger)   shared entry point for startup catch-up and the scheduler tick:
    -> twse.resolve_latest_trading_date()   ground truth "does the source have a newer
                                             trading day than we do" -- never a weekday guess;
                                             a holiday/not-yet-published afternoon just yields
                                             no newer date, same gate market_daily.update_daily()
                                             already used before Stage L1. ALWAYS re-resolved
                                             here (uncached) -- only status()'s read path caches.
    -> _missing_sessions_for_write()   the REAL missing-session count (Stage L1.1), not a raw
                                       calendar-day gap: prefers trading_calendar (can safely
                                       look back past local_max, real evidence); falls back to
                                       a weekday-candidate list bounded to (local_max, target]
                                       ONLY (never guesses backward past local_max -- that would
                                       flag out-of-scope history as false-positive holes)
    -> no missing sessions (and no prior 'partial' for target) / no baseline data (fresh
       install) / retry budget exhausted / locked
       -> return without writing anything (all no-ops are cheap and idempotent)
    -> refresh_lock() == history_backfill.runner_lock() -- LITERALLY the same lock file
       (Stage L1.1): both write daily_quotes/valuations, so a running 5-year historical
       backfill and daily auto-refresh/manual-update can never race each other
       -> <=1 missing session  -> market_daily.update_daily()        (unchanged pipeline call)
       -> >1 missing sessions  -> market_daily.backfill_history(days) (unchanged pipeline call,
                                 reused instead of only ever fetching "latest"; days span from
                                 the EARLIEST missing session, not a calendar-day guess)
    -> re-run _missing_sessions_for_write() AFTER the fetch -- MAX(date) reaching target is
       never enough; only "no session-level hole remains" earns 'completed'. Still missing
       (or, single-day path, under MIN_COMBINED_QUOTE_ROWS) -> 'partial', never silently 'fresh'
    -> daily_refresh_runs row: running -> completed/partial/failed (append-only log, one row
       per attempt). Watchlist deep-fetch fires only for trigger != 'manual' (scheduler/
       startup_catchup), matching the exact pre-Stage-L1 scheduler behavior -- a manual click
       never gained a new side effect it didn't have before; deep-fetch's own success/failure
       never feeds back into the recorded status either way.
  recover_interrupted()   a crash mid-attempt leaves a 'running' row stuck; probes the lock and
                          marks it failed if nobody actually holds it (same class of fix as the
                          four *_lab.py recover_interrupted() calls), called at startup and
                          defensively inside status()
  status()                read-side freshness contract: combines daily_refresh_runs with actual
                          MAX(date) per dataset -- never a single boolean (fresh/partial/stale/
                          failed/unknown + last success/attempt/current run/pending dates/error).
                          Uses _cached_target_trading_day() (Stage L1.1, TTL =
                          AUTO_REFRESH_STATUS_CACHE_SECONDS, default 60s) so a dashboard polling
                          every 20s does not turn into a TWSE request every 20s; a real attempt
                          (scheduler/startup/manual) always calls the uncached resolver. Internal-
                          hole detection here is LOCAL ONLY (trading_calendar, no network); when
                          the calendar has no coverage, 'internal_hole_check_source' reports
                          'unverified' rather than silently claiming a verified 'fresh'. Also
                          reports research_benchmark (see below) as a clearly separate block.

app.scheduler.start()   weekday CronTrigger, minute=*/AUTO_REFRESH_POLL_INTERVAL_MINUTES, hour
                        window derived from config (AUTO_REFRESH_CHECK_TIME .. + retry window) --
                        bounds POLLING cadence only; never decides freshness (that stays
                        data_freshness's real trading-day evidence)
app.main.py lifespan    data_freshness.recover_interrupted() then startup_catchup_async() on a
                        daemon thread, so an unreachable data source never blocks server startup

web/index.html          GET /api/admin/data-freshness polled every 20s; the banner is never
                        hidden for stale/partial/failed/running -- only 'fresh' + idle reads as
                        "已同步". Manual "立即更新今日行情" still calls POST /api/admin/update-daily
                        (data_freshness.run_manual_update(), same single-day market_daily.update_daily()
                        pipeline, serialized against auto-refresh via the same lock) -- unchanged
                        from pre-Stage-L1 behavior, just logged and lock-protected the same way.
```

Scope discipline: this is a daily/end-of-day light-layer refresher, not an intraday/live-quote service. `app/services/data_freshness.py` never imports `app.datasources.mis_snapshot`/`app.datasources.fugle`, and the only two functions it may call to actually write data are `market_daily.update_daily()` and `market_daily.backfill_history()` — seeing a Stage L2 (intraday) or later commit call anything else from this module to mark a date "completed" is a contract violation.

**Research benchmark is NOT covered by daily auto-refresh.** `benchmark_series` (`benchmark_id='TAIEX_TR'`, the Strategy Lab's point-in-time total-return series read by `app/services/strategy/panel.py`) is a separate table with a separate pipeline — the historical `history_backfill.py` pipeline writes it; `app/scheduler.py` does not currently schedule that pipeline periodically. `status()`'s `research_benchmark` block reports its own `latest_date` and `tracked_by_daily_auto_refresh: false` explicitly, so the global `freshness` field (light layer only: `daily_quotes`/`valuations`/`institutional_daily`/`market_summary`) is never misread as "the research benchmark is current too". A new Strategy Lab research run gets whatever `benchmark_series` coverage the last historical backfill left it — daily auto-refresh does not, and per Stage L1.1's decision, should not, extend that pipeline; see the L1.1 report for why extending it was rejected as out of minimal-fix scope.

**Catch-up limitations:** the multi-day path has no per-day checkpoint; a truly empty `daily_quotes` dataset requires manual first-time setup. Without calendar coverage, the write path estimates forward weekdays only; a holiday can consume bounded retries and end as failed. Do not add per-candidate network probes to this fast path: the L1.1 report records the resulting hang.

**Open freshness discrepancy (source audit 2026-10-13, not a runtime verification):** when calendar coverage is absent, `_local_hole_check()` returns `unverified`, and `status()` can still choose `fresh` from the maximum date and prior-attempt state. Its separate `internal_hole_check_source` discloses uncertainty; this is not proof of complete trading-session coverage and does not satisfy the root instruction's session-evidence requirement for `fresh`. Reproduce with a disposable fixture containing a later quote date, an earlier missing session and no calendar coverage, then decide API behavior explicitly; no behavior or contract change was made in this documentation update.

See `docs/history/stage-l1-daily-auto-refresh-report.md` (original design) and `docs/history/stage-l1-1-daily-refresh-hardening-report.md` (correctness hardening: session-accurate gap detection, shared cross-runner lock, benchmark-freshness truthfulness, status() upstream-traffic bound, scheduler-behavior parity) for the full retry/concurrency design and known limitations.

## 9. Major tables (by layer)

| Layer | Tables |
|---|---|
| Accounts | `users`, `auth_sessions`, `account_tokens`, `auth_attempts`, `account_audit` |
| Light (whole-market, daily) | `stocks`, `daily_quotes`, `valuations`, `institutional_daily`, `market_summary`, `month_revenue` |
| Deep (on-demand, per-stock) | `financials`, `margin_daily`, `dividends`, `deep_fetch_log` |
| Usage | `watchlist`, `scores`, `trade_plans`, `trades` |
| Financial planning | `finance_profiles`, `finance_targets` (Stage I1 added 6 nullable columns to `finance_targets` for the Strategy Portfolio investment source — see `docs/RESEARCH_CONTRACTS.md` §15) |
| Cross-cutting facts | `corporate_actions`, `benchmark_series`, `trading_calendar`, `backfill_jobs`, `backfill_tasks` |
| Daily auto-refresh (L1) | `daily_refresh_runs` (append-only attempt log; freshness is always derived from this + the light-layer tables above, never a separate mutable "current status" row) |
| Strategy Lab | `strategies`, `strategy_versions`, `backtest_runs`, `backtest_equity`, `backtest_trades` |
| Optimization | `optimization_jobs`, `optimization_runs` |
| Walk Forward | `wf_candidate_freezes`, `walkforward_jobs`, `walkforward_folds`, `walkforward_equity`, `walkforward_trades` |
| Strategy Portfolio (H1) | `strategy_portfolios`, `strategy_portfolio_versions`, `strategy_portfolio_components`, `portfolio_research_runs`, `portfolio_research_equity`, `portfolio_research_rebalances` |

`app/db/migrations.py` adds SQLite triggers that enforce immutability where it matters at the DB level (optimization job provenance, candidate freezes, completed Walk Forward folds, `fold`-series OOS rows). Strategy Lab and Strategy Portfolio version tables rely on service-layer discipline (never `UPDATE`, only `INSERT`) rather than a DB trigger — see `AGENTS.md` for why that's still a hard invariant.

## 10. API group overview

All routes are under `/api`. See `app/api/routes.py` for exact signatures.

| Prefix | Area |
|---|---|
| `/auth` | Login/logout/me/password, single-use invite/reset completion; no public registration |
| `/admin/accounts` | Admin-only invitation, reissue/reset/revoke, member enable/disable |
| `/market`, `/stocks`, `/realtime` | Dashboard, stock search, quotes |
| `/stocks/{id}/...` | Stock detail: kline, revenue, financials, chips, valuation, technical, alerts, score |
| `/screener` | Whole-market screener presets + run |
| `/watchlist` | Watchlist CRUD |
| `/journal` | Trade plans, trades, positions, performance |
| `/finance` | Financial planning: universe, simulate, profiles; `/finance/portfolio-evidence` (Stage I1, read-only Strategy Portfolio evidence for a Planner investment source) |
| `/admin` | Data maintenance: sync, update, backfill (incl. `/admin/backfill-for-backtest`, `/admin/data-universe`); `/admin/data-freshness` (Stage L1, read-only auto-refresh/freshness status contract) |
| `/strategy-lab/templates`, `/strategy-lab/strategies`, `/strategy-lab/runs` | Strategy Lab CRUD + backtest runs |
| `/strategy-lab/optimization/...` | Optimization jobs, search spaces, surface, robustness |
| `/strategy-lab/walkforward/...` | Walk Forward jobs, folds, independent OOS, stitched |
| `/strategy-lab/cross-strategy/analyze` | Cross-strategy descriptive analysis (POST only, read-only) |
| `/strategy-lab/portfolios`, `/strategy-lab/portfolio-research-runs` | Strategy Portfolio CRUD/versioning/clone/archive + research runs (incl. `.../research-runs/{run_id}/cancel`\|`/resume`) |

`GET /api/market/freshness` is a member-safe subset without runner logs/errors/config. Forced stock fetching is admin-only `POST /api/stocks/{stock_id}/deep`; GET `force=true` no longer performs it. `web/login.html` and `web/account.html` provide account flows; `common.js` adds the account nav, attaches CSRF to writes and redirects expired sessions to login. Hiding admin UI supplements backend enforcement.
