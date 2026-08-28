"""深度層按需抓取：個股歷史價量、月營收、財報、法人/融資券、股利。

觸發時機：開個股頁 / 加入觀察名單 / 篩選器命中。
新鮮度：DEEP_FRESH_HOURS 內不重抓；增量更新從上次日期回溯少量重疊。
清理：非觀察名單且 DEEP_RETENTION_DAYS 未存取者可釋放（cleanup()）。
"""
import datetime as dt
import logging

from app.config import DEEP_FRESH_HOURS, DEEP_RETENTION_DAYS
from app.db import engine, upsert_many, query_all, query_one
from app.db.tables import (
    daily_quotes, month_revenue, financials, margin_daily,
    institutional_daily, dividends, deep_fetch_log,
)
from app.datasources import finmind

log = logging.getLogger(__name__)

DATASETS = ("price", "revenue", "financials", "chips", "dividend")
HISTORY_YEARS = 5


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _quarter(date_str: str) -> str:
    y, m = int(date_str[:4]), int(date_str[5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"


# FinMind 財報科目名稱容錯對照（type 欄位候選字）
INCOME_MAP = {
    "revenue": ["Revenue", "OperatingRevenue"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncome"],
    "pretax_income": ["PreTaxIncome", "IncomeBeforeIncomeTax"],
    "net_income": ["IncomeAfterTaxes", "NetIncome", "EquityAttributableToOwnersOfParent"],
    "eps": ["EPS", "BasicEarningsPerShare"],
    "nonop_income": ["TotalNonoperatingIncomeAndExpense", "NonoperatingIncomeAndExpense"],
}
BALANCE_MAP = {
    "total_assets": ["TotalAssets", "Assets"],
    "total_liabilities": ["TotalLiabilities", "Liabilities"],
    "equity": ["TotalEquity", "Equity", "EquityAttributableToOwnersOfParent"],
    "inventory": ["Inventories", "Inventory"],
    "receivables": ["AccountsReceivableNet", "NotesAndAccountsReceivableNet",
                    "AccountsReceivable"],
    "capital": ["OrdinaryShare", "CapitalStock", "Capital"],
}
CASHFLOW_MAP = {
    "op_cashflow": ["CashFlowsFromOperatingActivities",
                    "NetCashProvidedByOperatingActivities"],
    "invest_cashflow": ["CashProvidedByInvestingActivities",
                        "NetCashProvidedByInvestingActivities"],
    "fin_cashflow": ["CashProvidedByFinancingActivities", "CashFlowsProvidedFromFinancingActivities",
                     "NetCashProvidedByFinancingActivities"],
    "capex": ["AcquisitionOfPropertyPlantAndEquipment", "PropertyAndPlantAndEquipment"],
}


def _pivot_statement(rows: list[dict], colmap: dict, acc: dict) -> None:
    """FinMind 長格式（date/type/value）→ 以 (quarter, 欄位) 聚合進 acc。"""
    for r in rows:
        q = _quarter(r["date"])
        t = r.get("type") or ""
        for col, candidates in colmap.items():
            if t in candidates:
                acc.setdefault(q, {})[col] = r.get("value")


def _decumulate_cashflow(acc: dict[str, dict]) -> None:
    """現金流量表為年初至今累計值 → 轉成單季值（Q1 不變，Qn 減 Q(n-1) 累計）。"""
    cols = tuple(CASHFLOW_MAP)
    cum: dict[str, dict] = {q: {c: v.get(c) for c in cols} for q, v in acc.items()}
    for q in sorted(acc, reverse=True):
        y, n = q.split("Q")
        if n == "1":
            continue
        prev = cum.get(f"{y}Q{int(n) - 1}")
        if not prev:
            continue
        for c in cols:
            if acc[q].get(c) is not None and prev.get(c) is not None:
                acc[q][c] = acc[q][c] - prev[c]


def _fresh(conn, stock_id: str, dataset: str) -> bool:
    row = query_one(conn, """SELECT fetched_at FROM deep_fetch_log
                             WHERE stock_id=:s AND dataset=:d""",
                    s=stock_id, d=dataset)
    if not row or not row["fetched_at"]:
        return False
    age = dt.datetime.now() - dt.datetime.fromisoformat(row["fetched_at"])
    return age < dt.timedelta(hours=DEEP_FRESH_HOURS)


def _mark(conn, stock_id: str, dataset: str, last_date: str | None) -> None:
    upsert_many(conn, deep_fetch_log, [{
        "stock_id": stock_id, "dataset": dataset,
        "last_date": last_date, "fetched_at": _now(), "accessed_at": _now(),
    }])


def ensure_deep(stock_id: str, force: bool = False) -> dict:
    """確保個股深度資料已入庫且新鮮。回傳各資料集抓取筆數。"""
    result: dict[str, int | str] = {"stock_id": stock_id}
    start = (dt.date.today() - dt.timedelta(days=365 * HISTORY_YEARS)).isoformat()
    rev_start = (dt.date.today() - dt.timedelta(days=365 * (HISTORY_YEARS + 1))).isoformat()

    with engine.begin() as conn:
        todo = [d for d in DATASETS if force or not _fresh(conn, stock_id, d)]
        # 更新存取時間（供清理判斷）
        conn.exec_driver_sql(
            "UPDATE deep_fetch_log SET accessed_at=? WHERE stock_id=?",
            (_now(), stock_id))
    if not todo:
        result["status"] = "fresh"
        return result

    for dataset in todo:
        try:
            if dataset == "price":
                with engine.begin() as conn:
                    span = query_one(conn, """SELECT MIN(date) lo, MAX(date) hi
                                              FROM daily_quotes WHERE stock_id=:s""",
                                     s=stock_id)
                s = start
                if span and span["lo"] and span["lo"] <= start:
                    # 歷史已完整 → 只從最新日期往前 7 天增量續抓
                    s = (dt.date.fromisoformat(span["hi"])
                         - dt.timedelta(days=7)).isoformat()
                rows = finmind.price_history(stock_id, s)
                with engine.begin() as conn:
                    upsert_many(conn, daily_quotes, rows)
                    _mark(conn, stock_id, dataset,
                          rows[-1]["date"] if rows else None)
                result[dataset] = len(rows)

            elif dataset == "revenue":
                rows = finmind.month_revenue(stock_id, rev_start)
                rows = _compute_revenue_growth(rows)
                with engine.begin() as conn:
                    upsert_many(conn, month_revenue, rows)
                    _mark(conn, stock_id, dataset,
                          rows[-1]["ym"] if rows else None)
                result[dataset] = len(rows)

            elif dataset == "financials":
                acc: dict[str, dict] = {}
                _pivot_statement(finmind.financial_statements(stock_id, start),
                                 INCOME_MAP, acc)
                _pivot_statement(finmind.balance_sheet(stock_id, start),
                                 BALANCE_MAP, acc)
                _pivot_statement(finmind.cash_flows(stock_id, start),
                                 CASHFLOW_MAP, acc)
                _decumulate_cashflow(acc)
                rows = [{"stock_id": stock_id, "quarter": q, **vals}
                        for q, vals in sorted(acc.items())]
                with engine.begin() as conn:
                    upsert_many(conn, financials, rows)
                    _mark(conn, stock_id, dataset,
                          rows[-1]["quarter"] if rows else None)
                result[dataset] = len(rows)

            elif dataset == "chips":
                one_y = (dt.date.today() - dt.timedelta(days=365)).isoformat()
                inst_raw = finmind.institutional(stock_id, one_y)
                by_date: dict[str, dict] = {}
                for r in inst_raw:
                    d = by_date.setdefault(r["date"], {
                        "stock_id": stock_id, "date": r["date"],
                        "foreign_net": 0, "trust_net": 0, "dealer_net": 0,
                        "total_net": 0})
                    net = (r.get("buy") or 0) - (r.get("sell") or 0)
                    name = r.get("name") or ""
                    if name.startswith("Foreign"):
                        d["foreign_net"] += net
                    elif name == "Investment_Trust":
                        d["trust_net"] += net
                    elif name.startswith("Dealer"):
                        d["dealer_net"] += net
                    d["total_net"] += net
                margin = finmind.margin_short(stock_id, one_y)
                with engine.begin() as conn:
                    upsert_many(conn, institutional_daily, list(by_date.values()))
                    upsert_many(conn, margin_daily, margin)
                    _mark(conn, stock_id, dataset,
                          max(by_date) if by_date else None)
                result[dataset] = len(by_date)

            elif dataset == "dividend":
                ten_y = (dt.date.today() - dt.timedelta(days=3650)).isoformat()
                rows = finmind.dividend_results(stock_id, ten_y)
                with engine.begin() as conn:
                    upsert_many(conn, dividends, rows)
                    _mark(conn, stock_id, dataset,
                          rows[-1]["date"] if rows else None)
                result[dataset] = len(rows)
        except Exception as e:
            log.warning("deep fetch %s/%s failed: %s", stock_id, dataset, e)
            result[dataset] = f"error: {e}"
    result["status"] = "fetched"
    return result


def _compute_revenue_growth(rows: list[dict]) -> list[dict]:
    """由原始月營收計算 MoM / YoY / 累計 YoY。"""
    rows = sorted(rows, key=lambda r: r["ym"])
    by_ym = {r["ym"]: r["revenue"] for r in rows}
    out = []
    for r in rows:
        y, m = int(r["ym"][:4]), int(r["ym"][5:7])
        prev_ym = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
        last_year = f"{y - 1}-{m:02d}"
        rev, prev, ly = r["revenue"], by_ym.get(prev_ym), by_ym.get(last_year)
        acc = sum(by_ym.get(f"{y}-{k:02d}", 0) or 0 for k in range(1, m + 1))
        acc_ly = sum(by_ym.get(f"{y - 1}-{k:02d}", 0) or 0 for k in range(1, m + 1))
        out.append({
            **r,
            "mom": round((rev / prev - 1) * 100, 2) if rev and prev else None,
            "yoy": round((rev / ly - 1) * 100, 2) if rev and ly else None,
            "acc_yoy": round((acc / acc_ly - 1) * 100, 2) if acc and acc_ly else None,
        })
    return out


def cleanup() -> dict:
    """釋放非觀察名單且久未存取的深度資料（為未來上雲容量預留）。"""
    cutoff = (dt.datetime.now()
              - dt.timedelta(days=DEEP_RETENTION_DAYS)).isoformat(timespec="seconds")
    removed = []
    with engine.begin() as conn:
        stale = query_all(conn, """
            SELECT DISTINCT l.stock_id FROM deep_fetch_log l
            LEFT JOIN watchlist w ON w.stock_id = l.stock_id
            WHERE w.stock_id IS NULL AND COALESCE(l.accessed_at, l.fetched_at) < :c
        """, c=cutoff)
        for r in stale:
            sid = r["stock_id"]
            # 只清深度資料；輕量層（近期全市場日線）保留
            conn.exec_driver_sql("DELETE FROM financials WHERE stock_id=?", (sid,))
            conn.exec_driver_sql("DELETE FROM margin_daily WHERE stock_id=?", (sid,))
            conn.exec_driver_sql("DELETE FROM dividends WHERE stock_id=?", (sid,))
            conn.exec_driver_sql("DELETE FROM deep_fetch_log WHERE stock_id=?", (sid,))
            removed.append(sid)
    return {"removed": removed}
