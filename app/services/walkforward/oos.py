"""OOS initial-state contract (Stage F0.5) and a thin single-fold runner for testing it.

Independent-fold runner and initial-state guard (no fold loop / persistence here).

Why the contract exists
-----------------------
A frozen candidate is only *decided* at ``train_end``.  Replaying it over the training period
would create fills/holdings that never could have existed, and carrying them into the test window
would hand the OOS run an inherited portfolio (and skip its entry cost).  Therefore:

INDEPENDENT FOLD (the only mode implemented)
  * OOS ledger starts from **cash** (``initial_cash``) at the fold's first test session.
  * After the freeze, data dated <= ``train_end`` may produce ONE boundary target (the frozen
    candidate's own rule, evaluated at ``train_end``, whether or not that day is a rebalance day).
  * That target is executed by the ordinary engine rule: earliest T+1 = first test session.
    Its trading costs are charged like any other OOS trade.
  * Later rebalances follow the candidate's own schedule inside the test window.
  * There are NO frozen-candidate fills before ``test_start``; ``run_oos_fold`` raises if any exist.
  * Metrics are windowed to ``test_start..test_end`` (equity before ``test_start`` is flat cash).
  * Requires ``gap_sessions == 0`` (train_end is the session right before test_start); with a gap the
    boundary order would fill inside the embargo, which is refused rather than shifted.

STITCHED OOS
  Not defined here.  The single authoritative stitched contract is ``walkforward.stitched.CONTRACT``
  and the only combined view served to clients is ``walkforward.contracts.get()``.  This module keeps
  ``validate_initial_state`` (state-source guard used by both) and the independent-fold runner.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services import optimization_lab as opt
from app.services.strategy import rules, run_backtest
from app.services.strategy.types import BacktestConfig, RebalanceRule

CONTRACT_VERSION = "wf-oos-state/f0.5/1"
CONTRACT = {
    "version": CONTRACT_VERSION,
    "independent_fold": {
        "initial_state": "cash", "boundary_target": "candidate rule evaluated at train_end using data <= train_end",
        "first_execution": "earliest T+1 = first test session", "costs": "all OOS transactions charged",
        "fills_before_test_start": 0, "metrics_window": "test_start..test_end", "requires_gap_sessions": 0},
}
ALLOWED_STATE_SOURCES = ("cash", "prior_oos_terminal")


def validate_initial_state(state: Mapping | None, fold: Mapping, previous_fold: Mapping | None = None) -> None:
    """Pure guard for the (future) stitched mode; also used to reject anything but cash today."""
    if state is None or state.get("source") == "cash":
        return
    src = state.get("source")
    if src == "training_replay":
        raise ValueError("TRAINING_REPLAY_STATE_FORBIDDEN: a frozen candidate cannot be replayed over "
                         "its own training period and carried into OOS")
    if src != "prior_oos_terminal":
        raise ValueError(f"unknown initial-state source: {src}")
    if previous_fold is None or state.get("from_fold") != fold.get("index", 0) - 1 \
            or state.get("as_of") != previous_fold["test_end"]:
        raise ValueError("stitched state must be the terminal state of the immediately preceding "
                         "actually-run OOS fold")


class OOSGate:
    """SignalRule wrapper: silent before the boundary; evaluates the candidate rule at the boundary
    date and then on its own schedule.  Records the boundary target for leakage checks."""

    def __init__(self, rule, boundary: str):
        self.rule, self.boundary = rule, boundary
        self.boundary_signal: Any = None
        self.calls: list[str] = []

    def __call__(self, context):
        if context.date < self.boundary:
            return None
        self.calls.append(context.date)
        signal = self.rule(context)
        if context.date == self.boundary:
            self.boundary_signal = signal
        return signal


def oos_config(config: BacktestConfig, sessions: list[str], boundary: str) -> BacktestConfig:
    """Rebalance calendar = the candidate's own schedule from the boundary on, plus the boundary day."""
    keep = {d for i, d in enumerate(sessions)
            if d >= boundary and rules.is_rebalance_day(sessions, i, config.rebalance)}
    keep.add(boundary)
    from dataclasses import replace
    return replace(config, rebalance=RebalanceRule("dates", frozenset(keep)))


def run_oos_fold(panel, rule, config: BacktestConfig, fold: Mapping, *,
                 initial_state: Mapping | None = None) -> dict:
    """Independent OOS fold from cash.  ``rule``/``config`` are the frozen candidate's."""
    source = (initial_state or {}).get("source", "cash")
    if source == "training_replay":
        validate_initial_state(initial_state, fold)          # raises TRAINING_REPLAY_STATE_FORBIDDEN
    if source != "cash":
        raise ValueError("run_oos_fold is independent-fold only (cash start); stitched OOS is run by "
                         "walkforward.stitched.run_stitched")
    if fold.get("gap_sessions", 0) != 0:
        raise ValueError("OOS contract requires gap_sessions == 0")
    sessions = [d.date for d in panel.days if d.is_trading_session]
    boundary = fold["train_end"]
    if boundary not in sessions or not any(s >= fold["test_start"] for s in sessions):
        raise ValueError("panel must contain train_end and the test window")
    if [s for s in sessions if boundary < s < fold["test_start"]]:
        raise ValueError("train_end must be the session immediately before test_start")
    gate = OOSGate(rule, boundary)
    result = run_backtest(panel, gate, oos_config(config, sessions, boundary))
    early = [f for f in result["fills"] if f["date"] < fold["test_start"]]
    if early:
        raise AssertionError(f"LEAK: {len(early)} fill(s) before test_start {fold['test_start']}")
    test_curve = [p for p in result["equity_curve"] if fold["test_start"] <= p["date"] <= fold["test_end"]]
    if not test_curve:
        raise ValueError("no equity points inside the test window")
    first_fill = next((f["date"] for f in result["fills"]), None)
    upto = lambda rows, key: [r for r in rows if r[key] <= fold["test_end"]]  # noqa: E731
    metrics = opt.windowed_metrics(
        {**result, "equity_curve": upto(result["equity_curve"], "date"), "fills": upto(result["fills"], "date"),
         "closed_trades": upto(result["closed_trades"], "exit_date")},
        fold["test_start"], config.risk_free_rate)
    return {
        "contract": CONTRACT_VERSION, "mode": "independent_fold", "initial_state": "cash",
        "boundary_date": boundary, "boundary_signal": gate.boundary_signal,
        "first_fill_date": first_fill, "fills_before_test_start": 0,
        "metrics": {k: v for k, v in metrics.items() if not k.startswith("_")},
        "result": result,
    }
