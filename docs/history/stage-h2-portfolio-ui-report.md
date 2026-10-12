# Stage H2 — Strategy Portfolio UI

## Information architecture

The existing top navigation now links to `web/portfolio.html`. The page moves from an active/archived Portfolio List to Create/Builder, Portfolio Detail, Research Runs/Result, and Version History. List rows show only name, component count, allocation method, latest version, latest research status, and update time. Name search and incremental loading keep the archived list usable. There is no portfolio ranking or permanent sidebar.

## Builder

The six steps are Basics → Components → Allocation → No-Candidate Policy → Constraints → Review. Components come from persisted Strategies and completed Walk Forward jobs; the selected job fixes its Strategy version and independent OOS evidence. Equal Weight and Fixed Weight are usable. Fixed Weight shows the total and Strategic Cash, and blocks totals above 100% before backend validation. Inverse Volatility discloses readiness and insufficient-evidence behavior, without fallback. Risk Parity, Minimum Variance, and Correlation-aware are explicitly unavailable. Cash Hold is fixed and never redistributes a no-candidate component. Advanced sector weight is labelled reporting-only.

## Portfolio detail and result

Detail has Overview, Components, Allocation, Research, Holdings, and History tabs. Research shows queued/running/completed/failed/cancelled states, cancel/resume controls, pinned versions, and historical performance. Result shows CAGR, Sharpe, Max Drawdown, Total Return, Turnover, Total Cost, Cost Drag, equity, drawdown, and cash-weight series. Charts have a complete numeric table fallback. Rebalance disclosures separate Strategic Cash, No-Candidate Cash, other target cash, and the actual-minus-target execution residual; the residual is labelled as a difference that can include costs, missing prices, partial execution, and price effects. Warning text carries severity and provenance, including insufficient evidence, no candidate, unresolved corporate action, partial execution, constraint application, and assumed publication.

## Holdings and contribution

Aggregate Holdings renders one row per stock using the final portfolio exposure at the last rebalance. A row expands to its Strategy contributions; contribution weights are explicitly labelled as target exposures, which can differ from actual post-execution weight. The page says “Holdings as of last rebalance” and does not imply live or daily holdings. Rebalance details expand target allocations, cash categories, constraint diagnostics, aggregate orders, costs, and missing-price/partial-execution diagnostics.

## Versioning

Create New Version appends to the same Portfolio after a versioned setting changes. Clone Portfolio creates a new independent Portfolio at v1. Existing runs display their own pinned version. The UI prevents an unchanged definition from being submitted as a supposed new version.

## Browser verification

Real browser checks covered the three retained portfolios: Core Five (five components, Equal Weight), Momentum + Value (two, Fixed Weight), and Custom Mix (three, Fixed Weight). Their lists, details, completed results, charts, warnings, holdings, and rebalance rows rendered without browser console errors or warnings. A temporary UI portfolio used one completed OOS Strategy outside Core Five's five component identities, demonstrating arbitrary count and identity. The browser flow exercised create, 110% Fixed Weight rejection, 60% weight/40% Strategic Cash, research completion, a failed run and resume, a v2 update with old v1 research still pinned, clone to an independent v1, and archive. On a queued test run, the browser Cancel action produced `cancelled`; Resume subsequently completed against the same pinned v2. Both temporary portfolios were archived after verification.

The complete unittest suite passed: 344 tests. `python -m compileall app tests scripts`, JavaScript syntax checks, and `git diff --check` passed.

## Responsive and accessibility

Desktop and 390px mobile views had no document-level horizontal overflow. Wide result and holdings tables scroll inside their own containers. The six-step builder, research, and holdings views were inspected at 390px. Tabs have tab/tabpanel relationships, selected state, and arrow-key navigation; a keyboard ArrowRight moved focus and selection from Research to Holdings. Controls have visible focus styles, validation errors use alert text, numeric fields use tabular figures, and charts expose ARIA descriptions plus numeric tables.

## Known limitations

Holdings remain rebalance-date snapshots. Inverse Volatility can report insufficient evidence; the three covariance-based methods are unavailable. Only monthly rebalance is implemented. Sector weight is reporting-only because historical sector membership is not point-in-time. Real research runs complete very quickly, so cancelling a running run may race completion; queued cancel was verified. The builder reads the latest 200 Walk Forward jobs allowed by the existing list endpoint, so older completed jobs beyond that API window are not selectable in this UI yet. A cancelled run has no partial results, and resume recomputes its full original window.

## Stage I readiness

The Portfolio UI presents pinned versions, research results, cash, and holdings through existing APIs. Financial Planner remains separate; Stage I can decide whether and how to consume these research results without changing Portfolio research semantics.
