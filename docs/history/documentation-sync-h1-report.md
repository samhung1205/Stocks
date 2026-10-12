# Documentation Sync Checkpoint DOC-1

Docs-only checkpoint after Stage H1. No runtime behavior, DB schema, API contract, or research logic was changed — confirmed by `git status` (zero `.py` files touched by this pass) and a clean full test run (323/323) both before and after.

## Created / updated files

**Created:**
- `AGENTS.md` — new, canonical cross-agent contract (architecture/research/strategy/portfolio invariants, known limitations, dev commands, Definition of Done)
- `docs/ARCHITECTURE.md` — module map, DB/service/UI boundaries, five research/portfolio flows, table inventory, API group overview
- `docs/RESEARCH_CONTRACTS.md` — 14 sections covering every currently-live research/portfolio contract, with two explicit *(superseded)* notes (F0 → F0.5 candidate-freeze rule; Stage G's entry-notional sleeve → H0's exact sleeve)
- `docs/PROJECT_STATUS.md` — completed stages A→H1, currently-usable features, known limitations, explicitly not-yet-started items

**Updated:**
- `README.md` — added Strategy Lab / Optimization / Walk Forward / Cross-Strategy / Strategy Portfolio sections, quick start, test commands, directory structure, research methodology principles, known limitations, current-status pointer; kept the existing fundamental-analysis content (framework mapping, data sources, backfill CLI, publication contract) rather than deleting it
- `CLAUDE.md` — trimmed to Claude-specific workflow only: points to `AGENTS.md` as the source of truth, repo-navigation cheat sheet, commands, a short "working in this repo" checklist. The full three-layer/service-layer architecture walkthrough that used to live here moved to `AGENTS.md` (invariants) and `docs/ARCHITECTURE.md` (structure) so it isn't duplicated in two places. Git Attribution Rules section left untouched (still Claude/AI-agent-specific, not a general coding-agent architecture fact).

## Stale information removed

- README/CLAUDE.md previously listed only five frontend pages (`index/screener/watchlist/journal/finance.html`) with no mention of `strategy.html` — the Strategy Lab/Optimization/Walk Forward UI added in Stage D–F2 was completely undocumented. Fixed in README's feature overview and directory structure.
- CLAUDE.md's Architecture section described only the pre-Stage-D product (fundamental analysis + financial planning) — no mention of Strategy Lab, Optimization, Walk Forward, Cross-Strategy, or Portfolio existed anywhere in it. Replaced with pointers to the new docs rather than re-duplicating the content in CLAUDE.md itself.
- No prior repo-level doc distinguished the two unrelated meanings of "portfolio" in this codebase: a single strategy's own position-sizing/weighting step (`portfolio.weighting`/`portfolio.cash_weight` inside one Backtest Config, Stage B/C) vs. a multi-strategy Strategy Portfolio (Stage H). `AGENTS.md`/`docs/ARCHITECTURE.md` now consistently use "Strategy Portfolio" / "Portfolio Engine" for the Stage H concept and never call the Stage B/C position-sizing step a "portfolio."
- No outdated roadmap or "future architecture" speculation was carried into any new/updated file — `docs/PROJECT_STATUS.md`'s "not yet started" list only states what is confirmed not started (H1.5 validation items, Portfolio UI, Financial Planner integration), not a speculative roadmap.

## Major contracts documented

`docs/RESEARCH_CONTRACTS.md` §1–14: publication-date/PIT, adjusted-price convention, universe, corporate actions, backtest execution, Optimization/plateau, candidate freeze (current version `wf-freeze/f0.5/2`, F0's original rule marked superseded), independent vs. stitched OOS, `NO_STABLE_CANDIDATE`, fold-boundary integrity, warning severity (`warning-severity/f0/1`), exact sleeve (`portfolio-sleeve/h0/2`, Stage G's `cross-strategy-sleeve/g/1` marked superseded/descriptive-only), Portfolio allocation/netting/no-candidate cash, provenance/immutable versions. Every contract-version string cited was grepped against the actual source (`RULE_VERSION`, `CONTRACT_VERSION`, `PORTFOLIO_SLEEVE_CONTRACT_VERSION`, etc.) rather than transcribed from a Stage report from memory.

## Validation performed

- Every backtick-quoted, directory-qualified path across the six changed/created docs was extracted with a script and checked against the filesystem; the only non-existent "paths" were contract-version strings (e.g. `wf-freeze/f0.5/2`), API route paths (e.g. `/api/strategy-lab/portfolios`), and one intentional negative reference (`docs/ARCHITECTURE.md` explicitly states `optimization.html`/`walkforward.html` do **not** exist as separate pages — correct, `strategy.html` covers all three tabs).
- Basename-only references (e.g. `ledger.py`, `strategy.js`, `finance.html` used as shorthand after the full path was already stated) were manually spot-checked — all resolve under the already-established parent directory; this repo's existing README already used this shorthand convention before this pass.
- All contract-version string literals were grepped against source (`app/services/walkforward/freeze.py`, `app/services/warning_contract.py`, `app/services/portfolio/contract.py`) rather than assumed.
- `git diff --check` on all six changed files: clean, no whitespace errors.
- `uv run python -m unittest discover -s tests -p 'test_*.py'`: 323/323, both before and after this pass (no change).
- `uv run python -m compileall app tests scripts`: clean.
- No real-data research was re-run (correctly out of scope for a docs-only checkpoint).

## Intentionally deferred documentation

- `PRODUCT.md` (untracked, impeccable-schema product doc) is now also stale — written 2026-09-19, before Stage D–H1 — but it was not in this checkpoint's requested deliverable list, so it was left untouched. Worth a separate pass.
- `spec/Strategy Lab-Backtesting Engine-Portfolio Lab.md` and `spec/個人財務狀況與 ETF 長期投資平台開發.md` (both gitignored, local-only) were read for context but not modified — they're the original product specs, not living documentation this checkpoint owns.
- Individual `docs/history/stage-*.md` reports were not edited or consolidated — they remain the point-in-time delivery record for their stage; `docs/RESEARCH_CONTRACTS.md` and `docs/PROJECT_STATUS.md` are the new "current state" layer on top of them, not a replacement.
- No changes were made to `web/` (UI) or any test/script file — this checkpoint is documentation-only as instructed; the `portfolio_lab.recover_interrupted()` gap and other known limitations found during this audit are recorded in `AGENTS.md`/`docs/PROJECT_STATUS.md` but were **not** fixed (that would be a runtime-behavior change, out of scope here).
