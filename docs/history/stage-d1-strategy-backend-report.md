# Stage D1 — Strategy Lab Backend / Persistence / API

Status: complete. No UI, optimization, walk-forward, OOS or Portfolio Lab work.
Engine, metrics, factors, templates and template defaults are untouched.

## Files

| File | Change |
|---|---|
| `app/db/tables.py` | +5 tables: `strategies`, `strategy_versions`, `backtest_runs`, `backtest_equity`, `backtest_trades` (additive; created by existing `metadata.create_all`, verified idempotent on a copy of the real DB) |
| `app/services/strategy_lab.py` | New service layer (persistence, run lifecycle, cache, coverage) |
| `app/api/routes.py` | +16 thin routes under `/api/strategy-lab/*` (errors: LookupError→404, ValueError→400, RuntimeError→409) |
| `app/main.py` | Startup calls `recover_interrupted()` |
| `tests/test_strategy_lab_backend.py` | 24 tests (offline, temp SQLite). Full suite: 58 pass |

## Persistence model

- **Strategy** = name/description/template_key/archived. **Version** = immutable snapshot `(template_key, params overrides, backtest overrides, definition_hash)`. Editing params/backtest appends a new version only if the definition hash changed; rename/description edits and re-saving an identical definition do not.
- **Delete = archive** (soft). Versions and runs stay, so historical results remain reproducible. Archived strategies reject edits and new runs (409).
- **Run** pins `version_id` and stores a self-contained `request_json` snapshot (resolved template config, backtest config, window, explicit stock ids, universe spec, publication mode, benchmark, version no., definition hash). Execution reads only this snapshot, never the mutable strategy row or current template defaults.
- **Result** stored in the run row (`metrics`, `warnings`, `coverage`, `metadata`, open positions, ending cash) plus `backtest_equity` (net/gross/benchmark per day) and `backtest_trades` (`fill` / `closed`, JSON payload per row).
- NaN/inf (e.g. infinite profit factor) are stored as `null` so stored JSON is always valid.

## config_hash / reproducibility

`config_hash = sha256(engine_version, data_fingerprint, template_key, resolved template config, backtest config, start, end, stock_ids, publication_mode, benchmark_id)`.

- `data_fingerprint` = per-table aggregates over the rows the run would read (quote count/sums, valuations, corporate actions, benchmark, calendar, stocks metadata hash, publications by `publish_source`). It is a cheap change detector, **not** proof of byte-equality: an edit that preserves every aggregate would go unnoticed.
- `repro_json` stores engine version, Python/platform, data fingerprint. `ENGINE_VERSION` (`strategy-core/stage-b+c/1`) must be bumped manually when engine/metrics/factor semantics change.
- If data changes between queueing and execution, the run re-fingerprints, updates its hash and flags `data_changed_since_queued`.
- **Cache scope**: same `version_id` + same hash + status in completed/queued/running. Completed preferred; an active identical run is deduplicated (returned instead of double-queued). Failed runs are never reused. `force: true` always creates a new run.

## Run lifecycle

`POST /strategies/{id}/runs` validates and creates a `queued` row (or returns the cache hit) → `BackgroundTasks` calls `execute_run` (same pattern as existing backfill/deep-fetch; no Celery/Redis).
`execute_run`: atomic `queued→running` claim (single-shot; failed/completed runs can't be re-run) → build panel → `run_backtest` → verify engine's publication mode equals the request → **one transaction** writes equity, trades and flips to `completed` last. Any exception (engine, panel, persistence) rolls back and records `failed` + error; no partial rows, no metrics. `get_result/equity/trades` return 409 unless `completed`.
Startup `recover_interrupted()` marks leftover queued/running rows failed ("interrupted"), since the in-process worker died with the server.

## Publication mode / warnings / coverage

- `publication_mode` is a column, in `request`, in engine `metadata`, and in `coverage`; it's part of the hash (strict and assumed never share a cache entry).
- `warnings` are the engine's list saved verbatim (includes panel warnings and the assumed-mode warning). Test compares stored vs. engine output.
- `coverage` is computed from the panel actually used: sessions, stocks requested/with quotes/without quotes, quote-day min/median/max, visible publications by dataset×source, valuation days/observations, benchmark days, corporate actions, plus human-readable `notes` (e.g. no strict-mode fundamentals, benchmark missing). Notes are kept separate from engine warnings.

## API

Base `/api/strategy-lab`:

| Method | Path | |
|---|---|---|
| GET | `/templates`, `/templates/{key}` | catalog metadata + default config |
| GET/POST | `/strategies` | list (`?include_archived`) / create |
| GET/PATCH/DELETE | `/strategies/{id}` | read / edit (new version if definition changed) / archive |
| GET | `/strategies/{id}/versions`, `/versions/{vid}` | immutable versions |
| POST | `/strategies/{id}/runs` | body: `start`, `end`, `stock_ids` **or** `universe{as_of,min_avg_amount,min_observations}`, `publication_mode` (strict default), `benchmark_id`, optional `version_id`, `force`. Returns `{run_id,status,cache_hit,queued,config_hash}` |
| GET | `/strategies/{id}/runs` | run history with metrics |
| GET | `/runs/{id}` | status, error, request snapshot, reproducibility |
| GET | `/runs/{id}/result`, `/equity`, `/trades?kind=fill\|closed&limit&offset` | 409 unless completed |

Validation: unknown template/params/backtest keys are rejected (typos would otherwise be silently ignored); `backtest.rebalance` is rejected (the template's `params.rebalance` owns it); template can't change after creation. Universe without `stock_ids` uses `data_foundation.liquid_universe` at `as_of` (default `start`) and stores the resulting ids; empty universe → 400.

## Tests (24)

Versions (edit appends, old run snapshot/version unchanged, pinning, invalid definitions) · cache (identical reuse, force, window/mode/stocks/benchmark/backtest-config changes → new hash, data change invalidates, failed never reused, active dedupe) · persistence (full result, warnings & mode verbatim, inf→null) · failure (engine/panel/persist failure → `failed`, zero result rows, 409 on result; restart recovery; single-shot; snapshot execution) · API (templates, CRUD/archive, full lifecycle, failed run).

## Known limits / notes for D2

- Backtests run in the web process's threadpool; a long run holds a worker thread and is lost (marked failed) on restart. No cancel endpoint.
- Real DB currently has ~7 months of full-market quotes and sparse valuations/financials (see integration audit); runs on the real universe will surface this via `coverage.notes` and warnings rather than blocking.
- `universe` from the current `stocks` table has survivorship limits (noted in the stored `universe.note`).
- Equity endpoint returns the full series unpaged; fine at daily granularity for years.
- Real-data end-to-end run through the live server was not performed; tests use synthetic DB data.
