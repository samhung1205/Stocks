# Stage J: Final System QA + Research Integrity Audit + DOC-2 Sync

Date: 2026-09-23. Scope: full read-only correctness audit of the platform (data → research → portfolio → planning), a targeted set of fixes for genuine bugs found, and a documentation sync. No new strategies, allocation methods, research models, or UI features were added. No strategy parameters were changed because of any performance result.

## Overall verdict

**READY WITH RESTRICTIONS** for final UI polish to begin. Every research/portfolio/planner correctness contract in `docs/RESEARCH_CONTRACTS.md` (§1–16) was independently re-verified against the current source and matches. Two real API-layer bugs were found and fixed with regression tests. Several README.md/PRODUCT.md claims were stale (describing Stage H2/I1/I2 work as "not built yet" when it has shipped and works) and have been corrected. The "restrictions" are the pre-existing, already-disclosed limitations below (§ Remaining limitations) — none are new, none block starting UI polish, but polish work should not silently resolve them as a side effect.

## Correctness findings & fixes made

Two genuine bugs found, both in the API layer (not in any research/portfolio contract), both fixed with regression tests:

1. **`POST /api/journal/position-size`** (`app/api/routes.py`) — indexed `payload["entry"]`/`["stop"]`/`["max_loss"]` directly with no key-presence check and no `_lab()` wrapping. A request missing a field raised an uncaught `KeyError`; a non-numeric value raised an uncaught `ValueError`. Both fell through FastAPI's default handler as an unintended **500** instead of the **400** every sibling journal route (`create_plan`, `add_trade`) already returns for the same class of error. **Fixed**: explicit required-key check (matching the existing `add_trade` convention) + wrapped the float conversion through `_lab()`.
2. **`POST /api/finance/simulate`** — called `finance.simulate(payload)` directly, not through `_lab()`. `finance/service.py` does unguarded `float(profile.get(...))` coercion on request fields; a malformed payload (e.g. non-numeric `age`) raised `ValueError`, becoming a **500**. The adjacent `POST /finance/profiles` route already wraps the same failure mode correctly via `_lab()` → 400. **Fixed**: wrapped the call in `_lab()`, matching the existing sibling route's pattern exactly.

Both fixes verified live against the running dev server (missing/non-numeric input → 400, valid input still → 200) and covered by new regression tests in [tests/test_api_error_handling.py](../../tests/test_api_error_handling.py) (5 tests, all passing). No other correctness bug was found anywhere in the research/portfolio/planner contract surface, the DB layer, or the rest of the API layer — see the sections below for what was actually checked.

One data-hygiene observation, not fixed (out of scope, not a correctness bug): `app/services/strategy/panel.py`'s `build_panel` triggers a `ResourceWarning: unclosed database` during the test run (SQLAlchemy/sqlite3 connection not explicitly disposed before GC). It is cosmetic under `unittest` — the `with db_engine.connect()` block itself is correct, it's the underlying engine that's never `.dispose()`d in short-lived test fixtures — and does not affect production behavior (the app's engine lives for the process lifetime). Not touched, to keep this stage's scope to real correctness bugs.

## Research integrity (contracts §1–16, `docs/RESEARCH_CONTRACTS.md`)

Every clause was checked against the actual implementing code (not just test names — assertion bodies were read) by two independent audit passes:

- **§1 Publication date / PIT** — MATCHES. `actual(3) > observed_finmind(2) > assumed_deadline(1)` ranking enforced in `app/db/__init__.py`'s upsert conflict resolution; `publish_date < as_of` strict filtering in `panel.py`/`data_foundation.py`.
- **§2 Adjusted-price convention** — MATCHES. `adj_close` read only in `strategy/factors.py`; every fill/mark/cash path (`engine.py`, `portfolio/ledger.py`) uses raw close/open/`fill_price`.
- **§3 Universe** — MATCHES. `data_foundation.liquid_universe` (no fixed count, current `stocks` metadata caveat disclosed); monthly-union job coverage confirmed wired into both optimization and walk-forward job creation.
- **§4 Corporate actions** — MATCHES. `ratio` never used as a share multiplier; mixed dividends recorded `unresolved_*` with both fields `NULL`, never guessed; identical replay logic in the engine, exact-sleeve ledger, and Portfolio research engine.
- **§5 Backtest execution** — MATCHES. T+1 fill enforced by `ExecutionRule.__post_init__`; `costs.calculate` is the single shared cost function reused unchanged by the ledger and Portfolio engine; delisting/missing-valuation/invalid-multiplier are all `critical`.
- **§6 Optimization/plateau** — MATCHES. No `winner`/`best` field anywhere (a test recursively scans every response for the words); revision chain (`parent_job_id`/`root_job_id`/`revision_kind`) genuinely tested, including rejection of a revision after the test window opens.
- **§7 Candidate freeze `wf-freeze/f0.5/2`** — MATCHES. Medoid selection with the documented tie-break order; `NO_STABLE_CANDIDATE` when no plateau; `edge_axes` only ever surfaced as a warning, never auto-widened. No withdrawn F0 behavior found anywhere in source (grepped clean).
- **§8 Independent vs. stitched OOS** — MATCHES. `run_oos_fold` starts from cash and hard-asserts no fill before `test_start`; stitched series is always labeled `stitched_secondary` and forbids a training-replay initial state.
- **§9 `NO_STABLE_CANDIDATE`** — MATCHES. Recorded as `status=completed`/`outcome=no_candidate` with a flat-cash equity curve; test data for that fold is genuinely never loaded (verified via call-log assertion, not just a status field).
- **§10 Fold-boundary integrity** — MATCHES. Cross-strategy correlation/drawdown never bridges a fold boundary; `no_candidate` folds are excluded from correlation, never treated as a zero-return point.
- **§11 Warning severity contract** — MATCHES. Unclassified messages default to `caution`, never `info`.
- **§12 Exact sleeve `portfolio-sleeve/h0/2`** — MATCHES. Cash effect uses `fill_price`, never `reference_price`; reconciled against stored independent OOS `net_equity`, mismatches reported not normalized away.
- **§13 Allocation/netting/no-candidate cash** — MATCHES. Fixed aggregation order verified verbatim (allocation → no-candidate cash baseline → `max_strategy_weight` clamp → netting → `max_single_stock_weight` clamp → `min_cash` haircut); `redistribute_active`/any non-`cash_hold` policy raises unconditionally; constraints clamp-to-cash only, never redistribute to another component/stock; `risk_parity`/`minimum_variance`/`correlation_aware` raise `NotImplementedError`. Grepped `app/services/portfolio/` for hardcoded strategy/template branching — none found; the engine is genuinely generic over `component_keys`.
- **§14 Provenance/immutable versions** — MATCHES. Component's `strategy_version_id`/`walkforward_job_id` derived server-side from the job's own provenance, never accepted raw from the caller. DB-level write-once triggers on `walkforward_jobs`/`optimization_jobs` provenance columns confirmed present and catch `NULL → value` too, not just `value → value`.
- **§15/§16 Financial Planner investment source** — MATCHES, and independently confirmed live in the browser (see Frontend walkthrough): `evidence()` is read-only and never feeds `expected_return`; `validate_pin()` never resolves "latest version"; `manual`/`strategy_portfolio` never get the legacy `core_tw` role-based default return or classification; a Strategy Portfolio's `actual_value` is manual-or-NULL only, never derived from research-run equity or `journal.positions()`.

No superseded/dead contract remnants that could mislead a future agent were found (grepped for old `wf-freeze/f0` and `cross-strategy-sleeve/g/1`-as-allocation-input patterns — the latter is correctly never read by the Portfolio Engine, only by Stage G's own descriptive analysis, exactly as documented).

## Cross-layer provenance

Traced a real evidence chain end-to-end through the local DB (`data/stocks.db`, portfolio "H1 smoke: Core Five"):

```
strategy_portfolios.id=23 → strategy_portfolio_versions.id=29 (v1, equal_weight)
  → strategy_portfolio_components.id=63 (strategy_id=6, strategy_version_id=7, walkforward_job_id=9a3612...)
    → strategies.id=6 / strategy_versions.id=7  (both pins match)
    → walkforward_jobs.id=9a3612... (status=completed, provenance columns write-once-triggered)
      → walkforward_folds (4 rows, completed/evaluated) — frozen OOS evidence
  → portfolio_research_runs.id=442170ff... (portfolio_id=23, portfolio_version_id=29, completed)
    → portfolio_research_equity rows exist with real computed values
```

All five of this portfolio's components carry distinct explicit `(strategy_id, strategy_version_id, walkforward_job_id)` triples — no duplicates, no display-name lookups. `service._prepare_target`/`portfolio_source.validate_pin()` require an exact `strategy_portfolio_id` + `portfolio_version_id` and reject any mismatch; there is no "resolve latest version" code path anywhere in the traced chain. Editing strategy 6 or portfolio 23 today would append a new version row but cannot retroactively change component id=63's, job `9a3612...`'s, or run `442170ff...`'s stored results (insert-only version tables + DB-level write-once triggers on job provenance).

## DB / migration audit

Clean. `app/db/tables.py` is the sole schema source; `app/db/migrations.py`'s 5 versions apply in strict ascending order inside one transaction each, gated by a `schema_migrations` ledger check, and are safely re-runnable (verified idempotency both by dedicated tests and by re-running against a legacy-shaped fixture). Every migration statement is additive (`ADD COLUMN`/`CREATE ... IF NOT EXISTS`) — no destructive DDL anywhere in `app/`. Live `data/stocks.db` schema diffed column-for-column against `tables.py`: exact match, no orphan columns/tables, no duplicate schema definitions. All 11 documented immutability triggers exist and target the right tables/columns, including catching `NULL → value` provenance writes. Stage I1's 6 new `finance_targets` columns are genuinely nullable with zero backfill (confirmed on the live DB: 0 non-NULL rows for `source_type`).

One process gap, not a code bug: AGENTS.md requires a SQLite-backup-API backup before a destructive operation, but no backup helper or call site exists anywhere in `app/` or `scripts/` — the rule is currently followed by hand (several `data/stocks.*.db` snapshots exist from past stages) rather than enforced or assisted by tooling. Flagged as a known limitation (category B) below rather than fixed, since building backup tooling is a real feature addition outside this audit's scope.

## API consistency

Reviewed all 6 major route groups (strategy, optimization, walkforward, cross-strategy, portfolios, finance) against `app/api/routes.py` in full. Besides the two bugs already fixed above:

- Lifecycle transitions (queued→running→completed/failed/cancelled) for optimization/walkforward/portfolio-research-runs are structurally identical and correctly return 409 on cancel-after-terminal or resume-of-non-terminal state, 404 on a bad id checked *before* the state check (never masquerading as 409).
- No PUT/PATCH route touches an immutable version table; the only PATCH routes (`strategies/{id}`, `portfolios/{id}`) only update mutable metadata and append a version via `INSERT`, never `UPDATE`, when a versioned field changes.
- List endpoints are internally consistent (server-clamped `limit`, no `offset` at the job/run level; `offset` only appears one level down on trade/result sub-resources, and the frontend never sends it — not a break, just a two-tier design).
- Six dead (frontend-unused) GET routes were identified: strategy template detail, strategy version history/detail, `walkforward/contracts`... actually `GET /warning-contract`, `walkforward/jobs/{id}/oos`, `walkforward/jobs/{id}/stitched/trades`, `GET /admin/data-universe`. None are broken — all are exercised by backend tests — just currently unreached from any page. Not removed: they're small, harmless, and a couple (`data-universe`, `warning-contract`) are reasonable operator/debugging endpoints. Left as-is; worth revisiting only if UI polish wants to surface strategy version history in the Strategy Lab UI, which would turn "dead" into "used."
- No broken frontend→backend call was found (every `api(...)` call in `web/js/*.js` resolves to a real route).
- Spot-checked 4 response shapes (`optimization` job view, `portfolio` view, `strategy_lab` run result, `finance/portfolio-evidence`) against their frontend readers — all fields match on both sides.

## Frontend walkthrough

Live browser walkthrough against the real dev server and real local data (no mocks), console checked for errors at every step (all clean):

- **Dashboard**: loads with an honest stale-data warning ("資料可能過期... 若 09-22 是國定假日則忽略此提示"), no console errors.
- **Stock detail (2330)**: K-line/MA/volume render correctly; navigation from dashboard → stock → back (browser back button) preserves state correctly on the journal page with no breakage.
- **Strategy Lab**: strategy list shows real historical runs; a completed backtest result is clearly labeled `BACKTESTED · HISTORICAL`; the Walk Forward creation form states up front "Independent folds 是主要 OOS 證據；stitched OOS 只作次要路徑描述" — the IS/OOS honesty requirement is enforced in the UI copy itself, not just the API.
- **Optimization**: job list renders with template/objective/progress, "僅限樣本內參數穩健性研究" warning banner present, no "winner" language anywhere.
- **Strategy Portfolios**: portfolio detail page for a real completed research run shows the exact severity hierarchy required by §11 — an **INFO** banner for `no_candidate` explicitly states "未自動 redistribute", a **CRITICAL** banner for unresolved corporate actions, a **CAUTION** banner for assumed publication mode. Result is explicitly labeled "研究結果是歷史回測，並非預測。"
- **Financial Planner**: switching investment source to "Strategy Portfolio" reveals Portfolio → Version → optional Research Run pickers; selecting a pinned version shows "Pinned version ID 29；儲存後不會自動切換" (no silent drift). Selecting a real completed research run displays its evidence (CAGR 14.18%, Sharpe, drawdown, etc.) under a "Historical / OOS research evidence · Not a forecast of future returns" heading — **and the separate Planning Assumption's "Planning return (%)" field remained empty with placeholder "由你輸入，不自動採用 CAGR" even after the evidence loaded**, confirming live (not just by reading code) that CAGR never auto-fills the planning assumption. The known Step-2/target `actual_value` double-counting risk is disclosed inline exactly where a user would hit it ("若 Step 2 的股票／ETF 資產已含這筆市值，請避免重複輸入").
- **Cross-Strategy (API-only, no UI by design)**: exercised directly via `curl` against two real completed Walk Forward jobs; response correctly opens with "Descriptive cross-strategy comparison only. No ranking, winner, or capital allocation is computed here."
- Screener, watchlist, and journal pages load cleanly with no console errors.

No instance was found anywhere of the UI treating IS as OOS, stitched as independent, historical evidence as forecast, `no_candidate` as a system error, or a reporting approximation as a precise constraint — the opposite: every one of these distinctions is stated explicitly in the UI copy at the point where a user could otherwise be misled.

## Remaining limitations (as of Stage J)

**A. Correctness-sensitive (disclosed, not silently wrong):**
- Unresolved corporate actions still make Walk Forward training-window severity universally `critical` (upstream data-quality issue).
- No historical index-constituent list — universe selection always uses current `stocks` metadata (listed/delisted dates, industry never point-in-time).
- Fundamentals are `assumed`-mode only for financial statements (free sources don't supply a real `actual` publish date); only month-revenue can reach `observed_finmind`.
- `max_sector_weight` is reporting-only, never enforced (same current-snapshot sector-taxonomy limitation).
- Planner Step-2 vs. Strategy Portfolio `actual_value` double-counting is possible if the user isn't careful — disclosed inline, not structurally prevented.

**B. Scale / convenience (not correctness risks):**
- No automated DB-backup mechanism before destructive operations — currently a manual, undocumented-in-code discipline.
- Portfolio research-run cancel always fully discards and a resume always recomputes the whole window from scratch (no per-fold checkpoint) — acceptable given sub-second real run times.
- Portfolio holdings are rebalance-date snapshots only, not live/daily.
- `inverse_volatility` allocation is implemented but reports `insufficient_evidence` for the five real strategies today (QGM's early-fold evidence is too thin).
- Six API routes are currently unreached by any frontend page (listed above under API consistency) — harmless, not dead code in the sense of being untested.

**C. Future feature (not started, correctly not described as available):**
- `risk_parity`/`minimum_variance`/`correlation_aware` allocation — readiness-contract only, calling raises `NotImplementedError`.
- Portfolio rebalance frequencies beyond monthly.
- Financial Plan versioning (`finance_profiles`/`finance_targets` remain the pre-existing mutable "current settings" model).
- Final UI polish itself (performance, deployment-hardening, broader mobile support) — not started, out of scope for this stage.

## Documentation sync (DOC-2)

Corrected material staleness, left everything already-accurate alone:

- **README.md** — was describing the platform as "Stage A–H1 complete," "Strategy Portfolio has no UI," and "Financial Planner not yet integrated with Portfolio" — all three false as of Stage H2/I1/I2 (confirmed live in the walkthrough above). Updated the status line, the Strategy Lab section header, the Strategy Portfolio feature bullet, and the known-limitations list (also removed a second stale claim that `portfolio_lab` lacks `recover_interrupted()` — it has had it since Stage H1.5). Added the previously-undocumented `tests/smoke.py backtest` mode and corrected the test count (323 → 389). Updated the directory-structure listing to include `portfolio.html` and the JS files that were missing from it.
- **PRODUCT.md** — the "Financial Planner integration... has not started" bullet was rewritten to describe what Stage I0–I2 actually shipped (pin a version + optional run, read-only evidence, explicit separate planning-return confirmation, manual-or-NULL actual value).
- **CLAUDE.md** — updated the per-stage report range (A–H1 → A–J) and added the `backtest` smoke mode to match README.
- **docs/ARCHITECTURE.md** — bumped the "current as of" stamp to Stage J (no architectural change occurred in this stage; the bump reflects that it was re-verified against source, not that anything moved).
- **docs/RESEARCH_CONTRACTS.md** — no change; both audit passes confirmed every clause still matches source exactly.
- **docs/PROJECT_STATUS.md** — added the Stage J row to the completed-stages table; "Known limitations" and "Not yet started" sections were re-verified and left as-is (still accurate).
- **AGENTS.md** — no change; re-verified as accurate and canonical, nothing here was found stale or contract-violating.

## Tests

- `uv run python -m compileall app tests scripts` — clean, no errors.
- `uv run python -m unittest discover -s tests -p 'test_*.py'` — **389/389 passing** (384 pre-existing + 5 new regression tests for the two bugs fixed).
- `node --check` on every `web/js/*.js` file — clean.
- `git diff --check` — clean, no whitespace errors.
- `tests/smoke.py finance` and `tests/smoke.py backtest` (the two modes that are pure computation, no external API calls, safe to rerun) — both pass, corroborating the PIT/execution/corporate-action contracts independently of the unit tests.
- `sources`/`light`/`deep` smoke modes were **not** run (they hit real external data-source APIs) — not needed for this audit and would only add rate-limit risk with no correctness signal relevant to the research/portfolio/planner contracts under review.
- No expensive Optimization/Walk Forward research was re-run — not needed since no correctness bug requiring that kind of validation was found.

## Final UX Polish readiness: READY WITH RESTRICTIONS

Research/portfolio/planner correctness, provenance, DB integrity, and API consistency are all confirmed sound. The two real bugs found are fixed and regression-tested. Documentation now accurately reflects what's shipped. Final UI polish may begin. The restriction: polish work touching the Financial Planner (Step 2 vs. Strategy Portfolio market value) or the Portfolio holdings view should preserve the existing disclosure language verified above rather than remove it for a cleaner look — those disclosures are load-bearing for the "never show a reporting approximation as a precise constraint" product principle, not decorative copy.
