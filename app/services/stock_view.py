"""個股頁資料組裝：七大面向所需的全部序列與衍生指標。"""
import datetime as dt

from app.db import engine, query_all, query_one


def _margins(f: dict) -> dict:
    rev = f.get("revenue")
    def pct(x):
        return round(x / rev * 100, 2) if x is not None and rev else None
    return {
        "gross_margin": pct(f.get("gross_profit")),
        "op_margin": pct(f.get("operating_income")),
        "net_margin": pct(f.get("net_income")),
    }


def overview(stock_id: str) -> dict | None:
    with engine.begin() as conn:
        info = query_one(conn, "SELECT * FROM stocks WHERE stock_id=:s", s=stock_id)
        if not info:
            return None
        last_q = query_one(conn, """SELECT * FROM daily_quotes WHERE stock_id=:s
                                    ORDER BY date DESC LIMIT 1""", s=stock_id)
        val = query_one(conn, """SELECT * FROM valuations WHERE stock_id=:s
                                 ORDER BY date DESC LIMIT 1""", s=stock_id)
        watch = query_one(conn, "SELECT * FROM watchlist WHERE stock_id=:s", s=stock_id)
    return {"info": info, "quote": last_q, "valuation": val, "watchlist": watch}


def kline(stock_id: str, days: int = 500) -> list[dict]:
    with engine.begin() as conn:
        rows = query_all(conn, """
            SELECT date, open, high, low, close, volume FROM daily_quotes
            WHERE stock_id=:s AND close IS NOT NULL
            ORDER BY date DESC LIMIT :n""", s=stock_id, n=days)
    return list(reversed(rows))


def revenue_series(stock_id: str, months: int = 60) -> list[dict]:
    with engine.begin() as conn:
        rows = query_all(conn, """
            SELECT ym, revenue, mom, yoy, acc_yoy FROM month_revenue
            WHERE stock_id=:s ORDER BY ym DESC LIMIT :n""", s=stock_id, n=months)
    return list(reversed(rows))


def financial_series(stock_id: str) -> list[dict]:
    with engine.begin() as conn:
        rows = query_all(conn, """SELECT * FROM financials WHERE stock_id=:s
                                  ORDER BY quarter""", s=stock_id)
    out = []
    for i, f in enumerate(rows):
        d = {**f, **_margins(f)}
        # ROE（近四季淨利 / 權益）
        if i >= 3 and f.get("equity"):
            ni4 = sum((rows[j].get("net_income") or 0) for j in range(i - 3, i + 1))
            d["roe_ttm"] = round(ni4 / f["equity"] * 100, 2)
        # 負債比
        if f.get("total_assets") and f.get("total_liabilities") is not None:
            d["debt_ratio"] = round(f["total_liabilities"] / f["total_assets"] * 100, 2)
        # 自由現金流
        if f.get("op_cashflow") is not None and f.get("capex") is not None:
            d["fcf"] = f["op_cashflow"] - abs(f["capex"])
        out.append(d)
    return out


def chips_series(stock_id: str, days: int = 120) -> dict:
    with engine.begin() as conn:
        inst = query_all(conn, """
            SELECT date, foreign_net, trust_net, dealer_net, total_net
            FROM institutional_daily WHERE stock_id=:s
            ORDER BY date DESC LIMIT :n""", s=stock_id, n=days)
        margin = query_all(conn, """
            SELECT date, margin_balance, short_balance FROM margin_daily
            WHERE stock_id=:s ORDER BY date DESC LIMIT :n""", s=stock_id, n=days)
    inst = list(reversed(inst))
    cum5 = sum((r["total_net"] or 0) for r in inst[-5:])
    cum20 = sum((r["total_net"] or 0) for r in inst[-20:])
    return {"institutional": inst, "margin": list(reversed(margin)),
            "net5": cum5, "net20": cum20}


def valuation_band(stock_id: str) -> dict:
    """估值三層比較素材：目前 PE/PB/殖利率 vs 自身五年區間。"""
    with engine.begin() as conn:
        cur = query_one(conn, """SELECT * FROM valuations WHERE stock_id=:s
                                 ORDER BY date DESC LIMIT 1""", s=stock_id)
        # 以歷史收盤與近四季 EPS 重建歷史 PE 較複雜；用歷史估值紀錄替代
        hist = query_all(conn, """
            SELECT date, pe, pb, dividend_yield FROM valuations
            WHERE stock_id=:s ORDER BY date""", s=stock_id)
        divs = query_all(conn, """SELECT date, dividend FROM dividends
                                  WHERE stock_id=:s ORDER BY date""", s=stock_id)
        eps = query_all(conn, """SELECT quarter, eps FROM financials
                                 WHERE stock_id=:s AND eps IS NOT NULL
                                 ORDER BY quarter DESC LIMIT 8""", s=stock_id)

    def pct_rank(values, x):
        vals = sorted(v for v in values if v is not None)
        if not vals or x is None:
            return None
        return round(sum(1 for v in vals if v <= x) / len(vals) * 100, 1)

    pe_hist = [r["pe"] for r in hist]
    pb_hist = [r["pb"] for r in hist]
    return {
        "current": cur,
        "pe_percentile": pct_rank(pe_hist, cur["pe"]) if cur else None,
        "pb_percentile": pct_rank(pb_hist, cur["pb"]) if cur else None,
        "history": hist[-1250:],
        "dividends": divs,
        "eps_recent": list(reversed(eps)),
        "eps_ttm": round(sum(r["eps"] or 0 for r in eps[:4]), 2) if len(eps) >= 4 else None,
    }


def technical_summary(stock_id: str) -> dict:
    """均線位置、量能、52 週高低 — 供技術面板與評分。"""
    rows = kline(stock_id, 260)
    if not rows:
        return {}
    closes = [r["close"] for r in rows]
    vols = [r["volume"] or 0 for r in rows]
    last = closes[-1]

    def ma(n):
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    high52 = max(r["high"] or 0 for r in rows)
    low52 = min(r["low"] or 1e12 for r in rows if r["low"])
    vol20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else None
    return {
        "close": last, "ma20": ma(20), "ma60": ma(60),
        "ma120": ma(120), "ma240": ma(240),
        "high_52w": high52, "low_52w": low52,
        "off_high_pct": round((last / high52 - 1) * 100, 1) if high52 else None,
        "vol_ratio": round(vols[-1] / vol20, 2) if vol20 else None,
        "above_ma60": last >= ma(60) if ma(60) else None,
        "above_ma240": last >= ma(240) if ma(240) else None,
    }
