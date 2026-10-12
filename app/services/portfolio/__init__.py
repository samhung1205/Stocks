"""Stage H0: Portfolio Lab data / capital contracts.

Independent from ``app.services.strategy`` (the backtest engine) and from
``app.services.cross_strategy`` (Stage G's descriptive analysis) — this package only
BUILDS and VALIDATES the exact, mark-to-market sleeve ledger and the pure netting /
readiness / leakage contracts a future Portfolio Lab allocation engine (Stage H1+)
would consume. No capital allocation algorithm, no UI, no persistence: every module
here is either a read-only DB boundary (``data.py``) or pure functions.

Modules:
- ``ledger.py``       — exact daily shares/cash/mark-to-market replay from fills +
                          corporate actions + closing prices (Task 1)
- ``data.py``          — read-only DB boundary (fills, corporate actions, daily
                          quotes, a job's initial cash)
- ``reconciliation.py``— compares the exact replay against the stored independent
                          OOS net_equity; never silently normalizes a mismatch (Task 1)
- ``contract.py``      — Portfolio sleeve contract v2 (Task 2)
- ``netting.py``       — cross-strategy stock-exposure aggregation + net order /
                          net transaction-cost contract (Tasks 3 and 6)
- ``baseline.py``      — no-candidate → cash baseline policy (Task 4)
- ``readiness.py``     — allocation-method capability contract (Task 5)
- ``leakage.py``       — point-in-time / no-future-leakage guards (Task 7)
"""
