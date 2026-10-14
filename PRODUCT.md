# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is the owner, working on a desktop computer to research Taiwan-listed stocks, test investment ideas, assess risk, plan asset allocation, and document decisions. Two or three trusted friends may join through administrator-issued invitations. Each account owns its financial planning, holdings, notes and research; market facts are shared. There is no public registration.

## Product Purpose

Build a durable, disciplined, and verifiable personal investment decision process rather than merely finding stocks quickly. The intended workflow connects market observation, individual-stock analysis, strategy signals, backtesting, performance and risk evaluation, capital allocation, and decision records.

The interface should help the user answer four questions quickly:

1. What is happening now?
2. Why is it happening or why does the system believe it matters?
3. Where is the risk?
4. What should be watched or investigated next?

Success means that research and decisions become repeatable, explainable, reviewable, and less vulnerable to impulsive judgment.

## Positioning

This is a local-first Taiwan-stock research and personal financial planning workspace organized around a complete decision discipline. It combines market context, stock-level evidence, quantitative scoring, qualitative judgment, watchlist states, position sizing, transaction journaling, and long-term ETF planning while keeping the reasoning and provenance visible. It supports decisions but never places trades.

Alongside discretionary, stock-by-stock research, the product also has a systematic side: a Strategy Lab research chain (rule-based backtesting → parameter optimization/robustness search → walk-forward out-of-sample validation → descriptive cross-strategy comparison → multi-strategy Strategy Portfolios). This is not a "find me the best strategy" tool. It is a research discipline for building, stress-testing, and combining rule-based strategies while keeping every claim honest: robustness regions instead of a single winning parameter set, out-of-sample evidence instead of in-sample fit, disclosed revisions instead of quiet redefinitions, and diversification/complementarity across strategies instead of a ranked leaderboard. Where the evidence is not there yet, the system says so explicitly rather than guessing.

## Operating Context

- The product is used primarily on a desktop browser for focused research, backtesting, and review.
- Core information pages must remain comfortable to read on a phone, but advanced analysis does not need feature parity on mobile.
- The current workflow covers a market dashboard, stock search and analysis, market screening, a staged watchlist, transaction journaling, personal finance and ETF planning, and a Strategy Lab (backtesting, optimization/robustness, walk-forward OOS validation) — all with a working UI at `strategy.html`. Cross-Strategy analysis remains API-only. Strategy Portfolios now have a dedicated `portfolio.html` interface.
- Initial and ongoing market-data maintenance is performed inside the dashboard. Official end-of-day light-layer data is checked by an in-process scheduler, with startup catch-up and a visible freshness state. Missing sessions can still remain `partial` or `failed` and require investigation.
- Orders are executed separately in a brokerage application.
- The application runs locally and as a private, single-process Railway deployment with a persistent SQLite volume. The two databases are independent. The current source defaults to invitation-only accounts (admin/member, server sessions, assisted password recovery and per-user private data). Local and Railway v7 migration and account rollout completed on 2026-10-14, preserving the original owner login and private data; HTTPS login/logout were verified. See `docs/ACCOUNTS.md` and `docs/railway-deployment.md`.

## Capabilities and Constraints

- The interface and primary content use Traditional Chinese.
- The system performs analysis, research, backtesting, risk assessment, financial planning, and decision support only. Direct or automated brokerage execution is out of scope.
- Prefer data sources that are free, public, and legally obtainable.
- Data correctness and source transparency take priority over feature volume.
- Historical observations, real-time or delayed market data, model estimates, user-supplied assumptions, and strategy signals must be clearly distinguished.
- Backtests must prevent look-ahead bias and data leakage. Important assumptions, source data, transformations, and performance calculations must be traceable.
- Desktop is the primary interaction target. Core information views require sound responsive behavior and must remain readable on mobile; advanced desktop workflows may be reduced on mobile.
- The current implementation is a FastAPI application with a framework-free HTML/CSS/JavaScript frontend, vendored ECharts, SQLAlchemy Core, and a local SQLite database.
- Railway requires an existing valid SQLite database. The new accounts mode protects private pages/APIs and publishes only health plus minimal login/token-completion surfaces. Import-mode health is not evidence that the full application is ready.
- Automated refresh covers official daily/end-of-day data only. The Strategy Lab total-return benchmark is outside that refresh pipeline; delayed or live quotes remain separate from official daily closes.
- The data architecture uses an always-available whole-market light layer, an on-demand stock-level deep layer, and retention cleanup for inactive deep data.
- Current real-time quotes use Fugle when configured and otherwise fall back to the TWSE MIS snapshot source. The fallback must not make the overall quote experience fail when a key is absent or invalid.
- **Strategy research (implemented, has a UI).** A Strategy is a named, versioned definition — a template plus its parameters — never hard-coded to a fixed list; editing a strategy appends a new version rather than overwriting it, so a past run always stays traceable to exactly what produced it. On top of a strategy sit three further, already-implemented research steps: backtesting a single definition; optimization, which searches a disclosed parameter space for a robustness region rather than reporting a single "winning" combination (there is no "best" or "winner" concept anywhere in this product); and walk-forward out-of-sample (OOS) testing, which is the primary evidence for whether a strategy holds up outside the window it was tuned on. A "stitched" convenience series exists for illustration but is always labeled as secondary, non-independent evidence — it is never shown as equivalent to genuine OOS results. A window with no stable, robust candidate is a normal, disclosed outcome (`no stable candidate`), not an error and not something the system hides.
- **Cross-Strategy analysis (implemented, API only).** A descriptive comparison across several already-validated strategies — correlation, holdings/sector overlap, drawdown complementarity — with no ranking and no "best strategy" claim. It exists to help the user see how strategies relate to each other, not to pick a winner for them.
- **Strategy Portfolios (backend and UI).** A Strategy Portfolio is a user-named, independent, versioned container for an arbitrary number of strategies — never a fixed bundle. The user can create any number of portfolios; a portfolio can contain any number of strategies; the same strategy can be reused across multiple portfolios at once. Every edit to a portfolio (its components, allocation method, weights, rebalancing, or risk constraints) creates a new immutable version rather than overwriting the old one, and each component pins both the exact strategy version and the specific out-of-sample evidence it was built from — so a portfolio's recorded history never silently changes just because the underlying strategy was later edited. The product supports the full lifecycle through `portfolio.html` and the existing backend: listing, creating, cloning, and archiving portfolios; full version history; running a portfolio's research (with cancel and resume); and reading back its equity curve, cash position, combined holdings across strategies, each strategy's contribution to those holdings, turnover and trading cost, and any warnings or constraint breaches. The UI marks holdings as last-rebalance snapshots and keeps historical research pinned to its original version.
- **Allocation methods within a portfolio.** Equal weight and fixed (user-specified) weight are available today. Inverse-volatility weighting is implemented but currently reports insufficient evidence for the five real strategies on hand (they don't yet have enough out-of-sample history for it) — the UI discloses that readiness may be insufficient and never substitutes another method. Risk-parity, minimum-variance, and correlation-aware allocation are not implemented; they must not be described or presented as available product capabilities.
- **No-candidate handling.** When a strategy inside a portfolio has no stable candidate at a given point in time, its capital allocation simply stays in cash for that period. It is never automatically redistributed to the portfolio's other strategies — an honest cash position is preferred over a fabricated substitute exposure.
- **Financial Planner integration.** `finance.html` can pin a Strategy Portfolio (an exact version, plus an optional completed research run) as one investment source among ETF and manual entries. The Planner shows that run's historical/OOS evidence (CAGR, Sharpe, drawdown, cash behavior, warnings) clearly labeled as historical, never a forecast; the user must separately and explicitly set a planning return — nothing auto-fills it from the evidence, and selecting a portfolio or run alone never changes a plan's numbers. A Strategy Portfolio's market value in the plan is manual-entry-or-blank only, never derived from the backtest's equity curve or treated as a real position.
- Public launch, self-service registration/email recovery, MFA and general-user onboarding are outside the invitation-only small-group scope. Account rollout was verified on 2026-10-14; the owner issues friend invitations when needed.

## Brand Commitments

- The product is currently named 「台股分析」 / 「台股投資分析平台」.
- Product language should be professional, clear, mature, and restrained.
- The experience must favor comprehension and evidence over dense data display.
- Avoid a generic AI or SaaS-template character, excessive card grids, neon gradients, and motion without a functional purpose.
- Existing investment terminology and the workflow mapped to the documented Taiwan-stock investment analysis framework should remain consistent.

## Evidence on Hand

- `README.md` is the Traditional Chinese user manual: access, data preparation, page operations, research workflow, planning, and result interpretation. `docs/DEVELOPMENT.md` preserves the development commands, data-source configuration, backfill tooling, and original feature-to-framework mapping.
- `AGENTS.md` / `CLAUDE.md` document the engineering invariants, service boundaries, and workflow rules behind both the fundamental-analysis and strategy-research sides of the product.
- `docs/README.md` is the documentation map. `docs/ARCHITECTURE.md`, `docs/RESEARCH_CONTRACTS.md`, and `docs/PROJECT_STATUS.md` are the current-state engineering references; `docs/railway-deployment.md` records the verified deployment and its known data limitations. `docs/history/` contains dated stage and audit records, including the Strategy Portfolio engine (`stage-h1-generic-portfolio-engine-report.md`) and its reliability follow-up (`stage-h1-5-portfolio-validation-report.md`).
- `web/` contains the implemented dashboard, stock view, screener, watchlist, transaction journal, financial-planning, and Strategy Lab (`strategy.html`: backtesting, optimization, walk-forward) interfaces. Cross-Strategy analysis remains API-only; Strategy Portfolios use `portfolio.html`.
- `app/` contains the implemented data-source, analysis, scoring, alert, journal, market-maintenance, financial-planning, and strategy-research (backtest/optimization/walk-forward/cross-strategy/portfolio) services.
- Real Strategy Portfolios and research runs existed in the local validation database and were present in the Railway import snapshot. They demonstrate the backend on real data; their current state must be checked in the relevant database rather than assumed from this historical observation.
- `tests/test_*.py` is the primary offline `unittest` verification path; `tests/smoke.py` and selected `scripts/` workflows exercise external sources or real-data pipelines.
- The application includes no testimonials, public customer evidence, audited performance claims, or guarantees of investment returns. Future product work must not fabricate them.

## Product Principles

1. **Explain the decision, not just the number.** Show what changed, why it matters, the evidence behind it, and the next question to investigate.
2. **Make discipline repeatable.** Connect observation, analysis, signals, risk, sizing, action, and retrospective review into one traceable workflow.
3. **Keep provenance visible.** Identify source, freshness, latency, assumptions, and whether a value is observed, estimated, or generated by a strategy.
4. **Treat correctness as a feature.** Prevent biased backtests, keep calculations auditable, and prefer a smaller trustworthy result over a larger ambiguous one.
5. **Stay private and focused.** Preserve the local-first model and the private single-process deployment; any broader sharing requires an explicit decision about accounts and data isolation.
6. **Prefer honest uncertainty over false confidence.** Report a robustness region instead of a single "best" parameter set, out-of-sample evidence instead of in-sample fit, a disclosed research revision instead of a quiet redefinition, and diversification/complementarity across strategies instead of a ranked "best strategy." When the evidence isn't there yet (a thin sample, an unready allocation method, no stable candidate), say so and stop — never substitute a guess or a fallback.

## Accessibility & Inclusion

Accessibility is practical rather than certification-driven. Interfaces should maintain adequate contrast, keyboard operability, visible focus states, semantic structure, readable typography, and understandable status or error communication. Full WCAG 2.2 AA certification is not currently a product requirement.
