"""Strategy Lab backtest core.

Layering is deliberate:

* :mod:`panel` is the only module in this package that reads the database and
  turns publication-dated data into point-in-time observations.
* :mod:`engine`, :mod:`rules`, :mod:`costs`, and :mod:`metrics` are pure
  calculation modules.  They accept dataclasses/lists/dicts and never query
  application state.

Stage C adds pure, cloneable strategy templates.  Optimization, walk-forward
analysis, API, and UI remain outside this package stage.
"""

from .engine import run_backtest
from .panel import build_panel, synthetic_panel
from .templates import TEMPLATES, get_template
from .types import BacktestConfig, ExecutionRule, MissingDataRule, Panel

__all__ = [
    "BacktestConfig", "ExecutionRule", "MissingDataRule", "Panel",
    "TEMPLATES", "build_panel", "get_template", "run_backtest", "synthetic_panel",
]
