"""Pure transaction-cost model aligned with ``services.journal`` defaults."""
from __future__ import annotations

from dataclasses import dataclass

from .types import CostConfig


@dataclass(frozen=True)
class CostBreakdown:
    reference_price: float
    fill_price: float
    notional: float
    commission: float
    tax: float
    slippage: float

    @property
    def total(self) -> float:
        return self.commission + self.tax + self.slippage


def fill_price(reference_price: float, side: str, config: CostConfig) -> float:
    """Apply deterministic adverse slippage to a quoted execution price."""
    if reference_price <= 0:
        raise ValueError("reference price must be positive")
    direction = 1 if side == "buy" else -1 if side == "sell" else 0
    if not direction:
        raise ValueError("side must be buy or sell")
    return reference_price * (1 + direction * config.slippage_bps / 10_000)


def calculate(side: str, shares: float, reference_price: float,
              config: CostConfig) -> CostBreakdown:
    """Calculate commission, sell tax, and slippage for one fill.

    Commission intentionally uses the journal's ``round(max(..., 1))``
    convention.  Setting ``minimum_commission=0`` is useful for fractional or
    synthetic tests.
    """
    if shares < 0:
        raise ValueError("shares must be non-negative")
    px = fill_price(reference_price, side, config)
    notional = shares * px
    commission = round(max(notional * config.commission_rate *
                           config.commission_discount,
                           config.minimum_commission), 0) if shares else 0.0
    tax = round(notional * config.sell_tax_rate, 0) if side == "sell" and shares else 0.0
    slippage = shares * abs(px - reference_price)
    return CostBreakdown(reference_price, px, notional, float(commission),
                         float(tax), slippage)
