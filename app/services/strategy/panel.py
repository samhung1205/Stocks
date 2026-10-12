"""Point-in-time panel construction -- the strategy package's only DB layer."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from app.db import engine as default_engine, query_all
from app.security_class import classify_security

from .adjustment import adjust_bars
from .types import (Bar, CorporateAction, Panel, PanelDay, Publication,
                    PublicationMode, Security, Valuation)

STRICT_SOURCES = ("actual", "observed_finmind")
ASSUMED_SOURCES = (*STRICT_SOURCES, "assumed_deadline")


def _normal_date(value: str) -> str:
    return dt.date.fromisoformat(value).isoformat()


def build_panel(start: str, end: str, stock_ids: Sequence[str], *,
                publication_mode: PublicationMode = "strict",
                benchmark_id: str | None = "TAIEX_TR", db_engine=None) -> Panel:
    """Read DB facts and expose each publication on the first later panel day.

    Publication timestamps are date-only, so ``publish_date < panel_date`` is
    intentional: a value published on T cannot be used to generate a T signal.
    Strict mode reads only Actual/Observed; assumed mode additionally permits
    ``assumed_deadline``.  The chosen mode is embedded in the returned Panel and
    later copied into every backtest result.
    """
    start, end = _normal_date(start), _normal_date(end)
    ids = tuple(dict.fromkeys(str(s) for s in stock_ids))
    if not ids:
        raise ValueError("stock_ids may not be empty")
    if publication_mode not in ("strict", "assumed"):
        raise ValueError("publication_mode must be strict or assumed")
    db_engine = default_engine if db_engine is None else db_engine
    binds = {f"s{i}": sid for i, sid in enumerate(ids)}
    in_sql = ",".join(f":s{i}" for i in range(len(ids)))
    with db_engine.connect() as conn:
        securities = query_all(conn, f"""SELECT stock_id,listed_date,delisted_date,
            market,industry,type AS security_type,name
            FROM stocks WHERE stock_id IN ({in_sql})""", **binds)
        quotes = query_all(conn, f"""SELECT stock_id,date,open,high,low,close,volume,amount
            FROM daily_quotes WHERE stock_id IN ({in_sql}) AND date BETWEEN :start AND :end
            ORDER BY date,stock_id""", start=start, end=end, **binds)
        calendar = query_all(conn, """SELECT date FROM trading_calendar
            WHERE date BETWEEN :start AND :end ORDER BY date""", start=start, end=end)
        actions = query_all(conn, f"""SELECT stock_id,date,kind,cash_dividend,
            stock_dividend,ratio,source,share_multiplier,resolution FROM corporate_actions
            WHERE stock_id IN ({in_sql}) AND date BETWEEN :start AND :end
            ORDER BY date,stock_id""", start=start, end=end, **binds)
        benchmark = (query_all(conn, """SELECT date,value FROM benchmark_series
            WHERE benchmark_id=:bid AND date BETWEEN :start AND :end ORDER BY date""",
            bid=benchmark_id, start=start, end=end) if benchmark_id else [])
        valuations = query_all(conn, f"""SELECT stock_id,date,pe,pb,dividend_yield
            FROM valuations WHERE stock_id IN ({in_sql}) AND date BETWEEN :start AND :end
            ORDER BY date,stock_id""", start=start, end=end, **binds)
        sources = ASSUMED_SOURCES if publication_mode == "assumed" else STRICT_SOURCES
        source_binds = {f"p{i}": source for i, source in enumerate(sources)}
        source_sql = ",".join(f":p{i}" for i in range(len(sources)))
        publications = []
        for dataset, period in (("month_revenue", "ym"), ("financials", "quarter")):
            rows = query_all(conn, f"""SELECT * FROM {dataset}
                WHERE stock_id IN ({in_sql}) AND publish_date < :end_exclusive
                  AND publish_source IN ({source_sql})
                ORDER BY publish_date,stock_id,{period}""",
                end_exclusive=(dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat(),
                **binds, **source_binds)
            for row in rows:
                publications.append(Publication(
                    dataset=dataset, stock_id=row["stock_id"],
                    publish_date=row["publish_date"],
                    publish_source=row["publish_source"], period=row[period],
                    values={k: v for k, v in row.items() if k not in {
                        "stock_id", period, "publish_date", "publish_source"}},
                ))

    security_map = {}
    for r in securities:
        r = dict(r)
        # security_type is the explicit Stage D+ class ('stock' = ordinary share only),
        # not the legacy stocks.type which labelled TPEx ETFs/DRs/preferreds as 'stock'.
        cls, _ = classify_security(r["stock_id"], r.pop("name", None), r.get("industry"),
                                   r.get("security_type"))
        r["security_type"] = "stock" if cls == "common_stock" else cls
        security_map[r["stock_id"]] = Security(
            **r, industry_source="current_stocks" if r.get("industry") else None)
    for sid in ids:
        security_map.setdefault(sid, Security(sid))
    bars_by_date: dict[str, dict[str, Bar]] = defaultdict(dict)
    for row in quotes:
        bars_by_date[row["date"]][row["stock_id"]] = Bar(**row)
    actions_by_date: dict[str, list[CorporateAction]] = defaultdict(list)
    for row in actions:
        actions_by_date[row["date"]].append(CorporateAction(**row))
    benchmark_by_date = {r["date"]: r["value"] for r in benchmark}
    valuations_by_date: dict[str, dict[str, Valuation]] = defaultdict(dict)
    for row in valuations:
        value = Valuation(**row)
        valuations_by_date[value.date][value.stock_id] = value
    trading_dates = {r["date"] for r in calendar} or set(bars_by_date)
    dates = set(trading_dates)
    # A delisting may occur on a non-trading day.  Include it so the engine can
    # force an explicit last-known-close exit instead of silently carrying it.
    dates.update(s.delisted_date for s in security_map.values()
                 if s.delisted_date and start <= s.delisted_date <= end)
    ordered_dates = sorted(dates)
    bars_by_date, adjustment = adjust_bars(bars_by_date, actions, ordered_dates)
    publications.sort(key=lambda p: (p.publish_date, p.stock_id, p.period))
    ready_by_date: dict[str, list[Publication]] = defaultdict(list)
    cursor = 0
    for date in ordered_dates:
        while cursor < len(publications) and publications[cursor].publish_date < date:
            ready_by_date[date].append(publications[cursor])
            cursor += 1
    days = tuple(PanelDay(date, bars_by_date.get(date, {}),
                          tuple(ready_by_date.get(date, ())),
                          tuple(actions_by_date.get(date, ())),
                          benchmark_by_date.get(date), date in trading_dates,
                          valuations_by_date.get(date, {}))
                 for date in ordered_dates)
    warnings = [] if calendar else ["trading_calendar missing; quote dates used"]
    if adjustment["residual_jumps"]:
        warnings.append(
            f"adjusted-price residual jump >25% on {len(adjustment['residual_jumps'])} ex-date(s) "
            "(reference price disagrees with quotes); factor series for those names is suspect")
    return Panel(days, security_map, publication_mode, benchmark_id, tuple(warnings))


def synthetic_panel(dates: Sequence[str], bars: Iterable[Mapping], *,
                    securities: Iterable[Mapping] = (),
                    publications: Iterable[Mapping] = (),
                    actions: Iterable[Mapping] = (),
                    valuations: Iterable[Mapping] = (),
                    benchmark: Mapping[str, float] | None = None,
                    publication_mode: PublicationMode = "strict",
                    adjust_prices: bool = False) -> Panel:
    """Build a small in-memory panel while enforcing the same PIT boundary."""
    ordered = tuple(sorted(_normal_date(d) for d in dates))
    bars_by_date: dict[str, dict[str, Bar]] = defaultdict(dict)
    for value in bars:
        bar = value if isinstance(value, Bar) else Bar(**value)
        bars_by_date[bar.date][bar.stock_id] = bar
    action_by_date: dict[str, list[CorporateAction]] = defaultdict(list)
    for value in actions:
        action = value if isinstance(value, CorporateAction) else CorporateAction(**value)
        action_by_date[action.date].append(action)
    valuation_by_date: dict[str, dict[str, Valuation]] = defaultdict(dict)
    for value in valuations:
        valuation = value if isinstance(value, Valuation) else Valuation(**value)
        valuation_by_date[valuation.date][valuation.stock_id] = valuation
    allowed = ASSUMED_SOURCES if publication_mode == "assumed" else STRICT_SOURCES
    pending = []
    for value in publications:
        publication = value if isinstance(value, Publication) else Publication(**value)
        if publication.publish_source in allowed:
            pending.append(publication)
    pending.sort(key=lambda p: (p.publish_date, p.stock_id, p.period))
    ready_by_date: dict[str, list[Publication]] = defaultdict(list)
    cursor = 0
    for date in ordered:
        while cursor < len(pending) and pending[cursor].publish_date < date:
            ready_by_date[date].append(pending[cursor])
            cursor += 1
    security_map = {}
    for value in securities:
        security = value if isinstance(value, Security) else Security(**value)
        security_map[security.stock_id] = security
    for rows in bars_by_date.values():
        for sid in rows:
            security_map.setdefault(sid, Security(sid))
    benchmark = benchmark or {}
    if adjust_prices:
        bars_by_date, _ = adjust_bars(
            bars_by_date, [a for d in action_by_date.values() for a in d], ordered)
    days = tuple(PanelDay(date, bars_by_date.get(date, {}),
                          tuple(ready_by_date.get(date, ())),
                          tuple(action_by_date.get(date, ())), benchmark.get(date), True,
                          valuation_by_date.get(date, {}))
                 for date in ordered)
    return Panel(days, security_map, publication_mode)
