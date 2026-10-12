"""Small, serializable contracts shared by the Stage B backtest modules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal, Mapping, Sequence

PublicationMode = Literal["strict", "assumed"]


@dataclass(frozen=True)
class Bar:
    stock_id: str
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None
    # Price-only adjusted close for FACTORS (see strategy.adjustment). Never used
    # for execution, fills, marks or equity.
    adj_close: float | None = None


@dataclass(frozen=True)
class Security:
    stock_id: str
    listed_date: str | None = None
    delisted_date: str | None = None
    market: str | None = None
    industry: str | None = None
    security_type: str | None = None
    # ``stocks.industry`` is a current snapshot, not a historical taxonomy.
    # Synthetic/imported panels may explicitly mark a historically valid value.
    industry_source: str | None = None


@dataclass(frozen=True)
class Valuation:
    """A dated valuation observation supplied by :mod:`strategy.panel`."""

    stock_id: str
    date: str
    pe: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None


@dataclass(frozen=True)
class CorporateAction:
    """Explicit corporate-action facts; ``ratio`` is metadata only.

    The Stage A+ ``ratio`` is a reference-price ratio and is intentionally not
    interpreted as a share multiplier.  Share quantities change only when a
    trusted source supplies ``share_multiplier`` explicitly.
    """

    stock_id: str
    date: str
    kind: str
    cash_dividend: float | None = None
    stock_dividend: float | None = None
    ratio: float | None = None
    share_multiplier: float | None = None
    source: str | None = None
    # Stage D+ resolution code (app/corporate_actions.py); ``unresolved_*`` means
    # cash/share effect is unknown and P&L across the event is unreliable.
    resolution: str | None = None


@dataclass(frozen=True)
class Publication:
    dataset: Literal["month_revenue", "financials"]
    stock_id: str
    publish_date: str
    publish_source: str
    period: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class PanelDay:
    date: str
    bars: Mapping[str, Bar]
    publications: tuple[Publication, ...] = ()
    actions: tuple[CorporateAction, ...] = ()
    benchmark: float | None = None
    is_trading_session: bool = True
    valuations: Mapping[str, Valuation] = field(default_factory=dict)


@dataclass(frozen=True)
class Panel:
    days: tuple[PanelDay, ...]
    securities: Mapping[str, Security]
    publication_mode: PublicationMode
    benchmark_id: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.publication_mode not in ("strict", "assumed"):
            raise ValueError("publication_mode must be 'strict' or 'assumed'")
        dates = [day.date for day in self.days]
        if dates != sorted(set(dates)):
            raise ValueError("panel dates must be unique and ascending")


@dataclass(frozen=True)
class ExecutionRule:
    price: Literal["open", "close"] = "open"
    delay_sessions: int = 1

    def __post_init__(self) -> None:
        if self.price not in ("open", "close"):
            raise ValueError("execution price must be open or close")
        if self.delay_sessions < 1:
            raise ValueError("signals may not execute before T+1")


@dataclass(frozen=True)
class MissingDataRule:
    # next_available: any missing execution price defers the WHOLE target order (Stage B default).
    # cancel:         any missing execution price drops the order.
    # partial:        tradable names execute, names without a price are frozen (Stage F0).
    execution: Literal["next_available", "cancel", "partial"] = "next_available"
    valuation: Literal["carry_forward", "error"] = "carry_forward"
    max_stale_sessions: int | None = 5

    def __post_init__(self) -> None:
        if self.execution not in ("next_available", "cancel", "partial"):
            raise ValueError("missing.execution must be next_available, cancel or partial")
        if self.max_stale_sessions is not None and self.max_stale_sessions < 0:
            raise ValueError("max_stale_sessions must be non-negative")


@dataclass(frozen=True)
class RebalanceRule:
    frequency: Literal["daily", "weekly", "monthly", "dates"] = "daily"
    dates: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PositionSizingRule:
    method: Literal["signal_weights", "equal", "score_weighted"] = "signal_weights"
    max_position_weight: float = 1.0
    cash_weight: float = 0.0
    lot_size: float = 1.0

    def __post_init__(self) -> None:
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1]")
        if not 0 <= self.cash_weight < 1:
            raise ValueError("cash_weight must be in [0, 1)")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.001425
    commission_discount: float = 0.6
    minimum_commission: float = 1.0
    sell_tax_rate: float = 0.003
    slippage_bps: float = 0.0

    def __post_init__(self) -> None:
        values = (self.commission_rate, self.commission_discount,
                  self.minimum_commission, self.sell_tax_rate, self.slippage_bps)
        if any(v < 0 for v in values):
            raise ValueError("cost parameters must be non-negative")


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    execution: ExecutionRule = field(default_factory=ExecutionRule)
    missing: MissingDataRule = field(default_factory=MissingDataRule)
    rebalance: RebalanceRule = field(default_factory=RebalanceRule)
    sizing: PositionSizingRule = field(default_factory=PositionSizingRule)
    costs: CostConfig = field(default_factory=CostConfig)
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Signal:
    """A target portfolio produced after observing ``date``.

    ``targets`` are desired weights.  ``candidates`` and ``scores`` allow the
    generic Stage B sizing rules to turn a selection into weights without
    defining any Strategy Template.
    """

    targets: Mapping[str, float] | None = None
    candidates: Sequence[str] = ()
    scores: Mapping[str, float] = field(default_factory=dict)
    warnings: Sequence[str] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalContext:
    date: str
    index: int
    bars: Mapping[str, Bar]
    history: tuple[PanelDay, ...]
    fundamentals: Mapping[str, Mapping[str, tuple[Mapping[str, Any], ...]]]
    valuations: Mapping[str, Valuation] = field(default_factory=dict)
    securities: Mapping[str, Security] = field(default_factory=dict)
    publication_mode: PublicationMode = "strict"


SignalRule = Callable[[SignalContext], Signal | Mapping[str, float] | Sequence[str] | None]
