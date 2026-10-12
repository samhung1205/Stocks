# Product Documentation Sync (post Stage H1.5)

Docs-only. No runtime/DB/API/tests/research-logic changes. Only `PRODUCT.md` was edited; schema (`Platform`/`Users`/`Product Purpose`/`Positioning`/`Operating Context`/`Capabilities and Constraints`/`Brand Commitments`/`Evidence on Hand`/`Product Principles`/`Accessibility & Inclusion`) preserved as-is.

## What changed

- **Positioning**: added the systematic research side (Strategy Lab → Optimization → Walk Forward/OOS → Cross-Strategy → Strategy Portfolios) alongside the existing discretionary/fundamental workflow, framed explicitly as "not a find-the-best-strategy tool."
- **Operating Context**: workflow bullet now names the Strategy Lab UI (`strategy.html`) and states Cross-Strategy analysis + Strategy Portfolios are backend-complete, API-only (no dedicated UI yet).
- **Capabilities and Constraints**: replaced the stale "backtesting is confirmed direction but not documented as complete" line with five concrete bullets: Strategy research (robustness regions, no "winner"; OOS as primary evidence, stitched labeled secondary; `no stable candidate` as a normal disclosed outcome); Cross-Strategy analysis (descriptive, no ranking); Strategy Portfolios (arbitrary components, reusable across portfolios, immutable versions pinning exact strategy version + OOS evidence, full backend lifecycle incl. cancel/resume — **explicitly marked no UI yet**); allocation availability (equal/fixed available, inverse_volatility implemented-but-insufficient-evidence, risk_parity/minimum_variance/correlation_aware **not implemented**); no-candidate → cash, never redistributed; Financial Planner integration **not started**.
- **Evidence on Hand**: added `docs/ARCHITECTURE.md`/`RESEARCH_CONTRACTS.md`/`PROJECT_STATUS.md`/stage reports as references, noted `strategy.html` in `web/`, and noted the 3 real permanently-kept Strategy Portfolios in the local DB as working evidence (API-only).
- **Product Principles**: added principle 6 — prefer honest uncertainty (robustness over winners, OOS over in-sample fit, disclosed revisions, diversification) over false confidence.

## What did not change

No new top-level sections, no schema/DB/API/code-path detail, no claims of a completed Portfolio UI or working risk_parity/minimum_variance/correlation_aware/inverse_volatility-in-production. `git diff --check` clean. No test suite run (docs-only, not required).
