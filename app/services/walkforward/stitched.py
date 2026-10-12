"""Stitched OOS: ONE continuous OOS ledger over consecutive test windows (secondary evidence).

Semantics (contract ``wf-stitched/f1/1``; extends ``wf-oos-state/f0.5/1``)
------------------------------------------------------------------------
* The ledger starts from **cash** at fold 0's first test session.  Nothing before fold 0's
  boundary is ever traded (no training replay).
* Fold k > 0 inherits the **terminal portfolio of the previous OOS fold** natively: it is the same
  ledger run.  ``validate_initial_state`` is asserted for every hand-over.
* From fold k's boundary (= its ``train_end`` = fold k-1's ``test_end``) the frozen primary of fold
  k alone decides targets.  The boundary target is forced (like the independent contract) and
  supersedes whatever fold k-1's rule would have done on that same day.  Fold k-1's frozen rule never
  acts after its own ``test_end``; fold k's rule never acts before its boundary.
* A ``no_candidate`` fold **liquidates to cash** at its boundary (one empty target, executed T+1,
  costs charged) and then stays in cash for the whole window.  No parameter that was not frozen for
  that fold may trade in it, and the previous fold's parameters are not carried over.
* **Universe** (F1.5): fold k's rule sees ONLY fold k's own point-in-time universe U_k (monthly union
  train_start..test_end_k, identical to the independent fold), so U_0 <= U_1 <= ... only ever grows
  with what was knowable then.  The panel holds the superset, but every rule call receives a context
  (bars, history, fundamentals, valuations, securities) restricted to U_k; fold 0 therefore sees exactly
  what its independent run sees.  Execution/marks stay on the shared panel (held names are always in U_k).
  Templates' own daily liquidity filters are untouched (they run inside the restricted context).
* Costs, sizing, execution and missing-data policy must be identical across evaluated folds
  (only the rebalance schedule may differ); otherwise stitching is refused rather than approximated.

Stitched metrics are a different statistic from independent-fold metrics (they include inherited
positions and no repeated entry ramp-up) and are always stored/labelled separately.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from app.services.strategy import rules, run_backtest
from app.services.strategy.types import BacktestConfig, PanelDay, RebalanceRule, Signal, SignalContext
from app.services.walkforward import oos as wf_oos

CONTRACT_VERSION = "wf-stitched/f1.5/1"
CONTRACT = {
    "version": CONTRACT_VERSION, "implemented": True, "kind": "secondary_evidence",
    "initial_state": "cash at fold 0 first test session",
    "state_hand_over": "fold k inherits the terminal portfolio of the previous OOS fold (same ledger)",
    "forbidden_state_sources": ["training_replay"],
    "boundary": "fold k frozen primary evaluated at its own train_end; supersedes fold k-1's rule that day",
    "universe": "fold k rule context restricted to U_k = its own point-in-time monthly union; U_0 <= U_1 <= ...",
    "no_candidate_fold": "liquidate to cash at the boundary, stay in cash for the window",
    "metrics": "stored and labelled separately from independent-fold metrics",
}


class UniverseView:
    """Restricts a SignalContext to one fold's universe (cached, filtered PanelDays)."""

    def __init__(self, ids):
        self.ids = frozenset(ids)
        self._days: dict[int, PanelDay] = {}

    def release(self) -> None:
        self._days.clear()

    def _day(self, i: int, d: PanelDay) -> PanelDay:
        got = self._days.get(i)
        if got is None:
            ids = self.ids
            got = PanelDay(d.date, {k: v for k, v in d.bars.items() if k in ids},
                           tuple(p for p in d.publications if p.stock_id in ids),
                           tuple(a for a in d.actions if a.stock_id in ids), d.benchmark, d.is_trading_session,
                           {k: v for k, v in d.valuations.items() if k in ids})
            self._days[i] = got
        return got

    def context(self, ctx: SignalContext) -> SignalContext:
        ids = self.ids
        history = tuple(self._day(i, d) for i, d in enumerate(ctx.history))
        return SignalContext(
            ctx.date, ctx.index, history[-1].bars if history else {}, history,
            {ds: {sid: rows for sid, rows in stocks.items() if sid in ids} for ds, stocks in ctx.fundamentals.items()},
            {k: v for k, v in ctx.valuations.items() if k in ids},
            {k: v for k, v in ctx.securities.items() if k in ids}, ctx.publication_mode)


class StitchedGate:
    """SignalRule: routes each date to the fold that owns it (with that fold's universe)."""

    def __init__(self, segments: Sequence[Mapping]):
        # segment = {index, boundary, test_end, rule | None, universe: ids | None}
        self.segments = sorted(segments, key=lambda s: s["boundary"])
        self.views = {s["index"]: UniverseView(s["universe"]) for s in self.segments
                      if s.get("universe") is not None}
        self.calls: list[tuple[str, int]] = []
        self._active: int | None = None

    def owner(self, date: str) -> Mapping | None:
        best = None
        for seg in self.segments:
            if seg["boundary"] <= date <= seg["test_end"]:
                best = seg           # later boundary wins on the shared hand-over day
        return best

    def __call__(self, context):
        seg = self.owner(context.date)
        if seg is None:
            return None
        self.calls.append((context.date, seg["index"]))
        if seg["index"] != self._active:              # free the previous fold's filtered history
            for k, view in self.views.items():
                if k != seg["index"]:
                    view.release()
            self._active = seg["index"]
        if seg["rule"] is None:                       # no_candidate: cash
            return Signal(candidates=()) if context.date == seg["boundary"] else None
        view = self.views.get(seg["index"])
        return seg["rule"](view.context(context) if view else context)


def stitched_config(configs: Sequence[BacktestConfig], sessions: list[str],
                    segments: Sequence[Mapping]) -> BacktestConfig:
    """Shared config with a rebalance calendar = union of each fold's own schedule inside its
    window, plus each fold's forced boundary day."""
    if not configs:
        raise ValueError("no configs")
    base = configs[0].to_dict()
    base.pop("rebalance")
    for c in configs[1:]:
        other = c.to_dict()
        other.pop("rebalance")
        if other != base:
            raise ValueError("STITCHED_CONFIG_MISMATCH: folds differ in costs/sizing/execution, "
                             "a single ledger cannot honour both")
    seg_config = {s["index"]: s["config"] for s in segments if s["config"] is not None}
    keep: set[str] = set()
    for seg in segments:
        keep.add(seg["boundary"])
        cfg = seg_config.get(seg["index"])
        if cfg is None:
            continue
        for i, d in enumerate(sessions):
            if seg["boundary"] <= d <= seg["test_end"] and rules.is_rebalance_day(sessions, i, cfg.rebalance):
                keep.add(d)
    # a fold's rule never acts after its test_end: the hand-over day belongs to the next fold
    return replace(configs[0], rebalance=RebalanceRule("dates", frozenset(keep)))


def validate_chain(folds: Sequence[Mapping]) -> None:
    """Consecutive, gap-free, ordered folds with cash start and prior-OOS hand-over only."""
    for k, fold in enumerate(folds):
        if fold["index"] != k:
            raise ValueError("stitching needs the contiguous prefix of folds starting at fold 0")
        if k == 0:
            wf_oos.validate_initial_state({"source": "cash"}, fold)
            continue
        prev = folds[k - 1]
        if fold["train_end"] != prev["test_end"]:
            raise ValueError(f"fold {k} boundary {fold['train_end']} is not fold {k-1}'s test_end "
                             f"{prev['test_end']}; a contiguous single ledger is impossible")
        wf_oos.validate_initial_state(
            {"source": "prior_oos_terminal", "from_fold": k - 1, "as_of": prev["test_end"]}, fold, prev)


def run_stitched(panel, folds: Sequence[Mapping], rules_by_fold: Mapping[int, Any],
                 configs_by_fold: Mapping[int, BacktestConfig], base_config: BacktestConfig,
                 universes_by_fold: Mapping[int, Sequence[str]] | None = None) -> dict:
    """``rules_by_fold[k]`` is fold k's frozen-primary rule, or absent/None for a no_candidate fold.
    ``universes_by_fold[k]`` = fold k's point-in-time universe ids (None/absent: no restriction)."""
    universes_by_fold = universes_by_fold or {}
    validate_chain(folds)
    sessions = [d.date for d in panel.days if d.is_trading_session]
    first = folds[0]
    if first["train_end"] not in sessions:
        raise ValueError("panel must contain fold 0's train_end")
    segments = [{"index": f["index"], "boundary": f["train_end"], "test_end": f["test_end"],
                 "rule": rules_by_fold.get(f["index"]), "config": configs_by_fold.get(f["index"]),
                 "universe": universes_by_fold.get(f["index"])}
                for f in folds]
    evaluated = [configs_by_fold[f["index"]] for f in folds if configs_by_fold.get(f["index"]) is not None]
    config = stitched_config(evaluated or [base_config], sessions, segments)
    gate = StitchedGate(segments)
    result = run_backtest(panel, gate, config)
    start, end = first["test_start"], folds[-1]["test_end"]
    early = [f for f in result["fills"] if f["date"] < start]
    if early:
        raise AssertionError(f"LEAK: {len(early)} stitched fill(s) before fold 0 test_start {start}")
    if gate.calls and gate.calls[0][0] < first["train_end"]:
        raise AssertionError("LEAK: stitched rule evaluated before fold 0 boundary")
    return {"contract": CONTRACT_VERSION, "result": result, "start": start, "end": end,
            "gate_calls": gate.calls, "config": config}
