"""全市場篩選器：在輕量層資料上掃描框架的量化條件，命中觸發深度抓取。

條件對映框架「中期波段：基本面正在變好，但市場還沒完全反映」：
- rev_yoy_min / rev_turn_pos / rev_accel  月營收動能（需先抓 MOPS 全市場月營收）
- insti_buy_days                          法人連續買超
- above_ma60 / above_ma240                趨勢位置
- vol_surge                               量增
- pe_max / yield_min / pb_max             估值過濾
- near_52w_high                           接近前高（突破候選）
"""
import logging
import threading

from app.db import engine, query_all

log = logging.getLogger(__name__)

PRESETS = {
    "momentum": {
        "label": "中期波段：營收轉強＋法人買超",
        "params": {"rev_yoy_min": 10, "insti_buy_days": 3, "above_ma60": True},
    },
    "turnaround": {
        "label": "轉機：營收年增由負轉正",
        "params": {"rev_turn_pos": True},
    },
    "value_dividend": {
        "label": "價值存股：低估值＋高殖利率",
        "params": {"pe_max": 15, "yield_min": 4, "pb_max": 2},
    },
    "breakout": {
        "label": "短線：接近52週高＋量增",
        "params": {"near_52w_high": 5, "vol_surge": 2, "above_ma60": True},
    },
}


def run(params: dict, limit: int = 100) -> list[dict]:
    with engine.begin() as conn:
        latest = query_all(conn, "SELECT MAX(date) d FROM daily_quotes")[0]["d"]
        if not latest:
            return []
        rows = query_all(conn, """
            SELECT q.stock_id, s.name, s.market, s.industry, s.type,
                   q.close, q.change, q.volume, q.amount,
                   v.pe, v.pb, v.dividend_yield
            FROM daily_quotes q
            JOIN stocks s ON s.stock_id = q.stock_id
            LEFT JOIN valuations v ON v.stock_id = q.stock_id AND v.date = q.date
            WHERE q.date = :d AND q.close IS NOT NULL""", d=latest)

        # 月營收（最新兩個月，判斷轉正/動能）
        rev = {}
        if any(k in params for k in ("rev_yoy_min", "rev_turn_pos", "rev_accel")):
            for r in query_all(conn, """
                SELECT stock_id, ym, yoy FROM month_revenue
                WHERE ym >= (SELECT MAX(ym) FROM month_revenue) || ''
                   OR ym IN (SELECT DISTINCT ym FROM month_revenue ORDER BY ym DESC LIMIT 3)
                ORDER BY stock_id, ym"""):
                rev.setdefault(r["stock_id"], []).append(r)

        # 法人連買
        insti = {}
        n_days = int(params.get("insti_buy_days") or 0)
        if n_days:
            dates = [r["d"] for r in query_all(conn, """
                SELECT DISTINCT date d FROM institutional_daily
                ORDER BY d DESC LIMIT :n""", n=n_days)]
            if len(dates) == n_days:
                for r in query_all(conn, """
                    SELECT stock_id,
                           SUM(CASE WHEN total_net > 0 THEN 1 ELSE 0 END) pos_days,
                           SUM(total_net) net
                    FROM institutional_daily WHERE date >= :d0
                    GROUP BY stock_id""", d0=dates[-1]):
                    insti[r["stock_id"]] = r

        # 均線/52週高/量能（一次算全市場，僅在需要時）
        tech = {}
        need_tech = any(k in params for k in
                        ("above_ma60", "above_ma240", "near_52w_high", "vol_surge"))
        if need_tech:
            hist = query_all(conn, """
                SELECT stock_id, date, close, high, volume FROM daily_quotes
                WHERE date >= date(:d, '-370 days') AND close IS NOT NULL
                ORDER BY stock_id, date""", d=latest)
            cur_sid, closes, highs, vols = None, [], [], []
            def flush():
                if cur_sid is None or not closes:
                    return
                t = {"close": closes[-1]}
                if len(closes) >= 60:
                    t["ma60"] = sum(closes[-60:]) / 60
                if len(closes) >= 240:
                    t["ma240"] = sum(closes[-240:]) / 240
                t["high52"] = max(highs)
                if len(vols) >= 21:
                    avg20 = sum(vols[-21:-1]) / 20
                    t["vol_ratio"] = vols[-1] / avg20 if avg20 else None
                tech[cur_sid] = t
            for r in hist:
                if r["stock_id"] != cur_sid:
                    flush()
                    cur_sid, closes, highs, vols = r["stock_id"], [], [], []
                closes.append(r["close"])
                highs.append(r["high"] or r["close"])
                vols.append(r["volume"] or 0)
            flush()

    hits = []
    for r in rows:
        sid = r["stock_id"]
        if params.get("exclude_etf") and r["type"] == "etf":
            continue
        if params.get("min_amount") and (r["amount"] or 0) < params["min_amount"] * 1e6:
            continue
        if params.get("pe_max") and not (r["pe"] and r["pe"] <= params["pe_max"]):
            continue
        if params.get("pb_max") and not (r["pb"] and r["pb"] <= params["pb_max"]):
            continue
        if params.get("yield_min") and not (
                r["dividend_yield"] and r["dividend_yield"] >= params["yield_min"]):
            continue

        rv = rev.get(sid, [])
        r["rev_yoy"] = rv[-1]["yoy"] if rv else None
        if "rev_yoy_min" in params:
            if r["rev_yoy"] is None or r["rev_yoy"] < params["rev_yoy_min"]:
                continue
        if params.get("rev_turn_pos"):
            if len(rv) < 2 or rv[-1]["yoy"] is None or rv[-2]["yoy"] is None:
                continue
            if not (rv[-1]["yoy"] > 0 >= rv[-2]["yoy"]):
                continue
        if params.get("rev_accel"):
            if len(rv) < 3 or any(x["yoy"] is None for x in rv[-3:]):
                continue
            if not (rv[-1]["yoy"] > rv[-2]["yoy"] > rv[-3]["yoy"]):
                continue

        if n_days:
            it = insti.get(sid)
            if not it or it["pos_days"] < n_days:
                continue
            r["insti_net"] = it["net"]

        t = tech.get(sid, {})
        if params.get("above_ma60") and not (t.get("ma60") and t["close"] >= t["ma60"]):
            continue
        if params.get("above_ma240") and not (t.get("ma240") and t["close"] >= t["ma240"]):
            continue
        if params.get("near_52w_high"):
            if not t.get("high52") or (1 - t["close"] / t["high52"]) * 100 > params["near_52w_high"]:
                continue
        if params.get("vol_surge"):
            if not t.get("vol_ratio") or t["vol_ratio"] < params["vol_surge"]:
                continue
        r["vol_ratio"] = t.get("vol_ratio")
        hits.append(r)

    hits.sort(key=lambda x: -(x.get("amount") or 0))
    hits = hits[:limit]
    _prefetch_deep([h["stock_id"] for h in hits[:10]])
    return hits


def _prefetch_deep(stock_ids: list[str]) -> None:
    """背景預抓前幾名命中個股的深度資料（開個股頁時就緒）。"""
    def work():
        from app.services import deep_fetch
        for sid in stock_ids:
            try:
                deep_fetch.ensure_deep(sid)
            except Exception as e:
                log.warning("prefetch %s: %s", sid, e)
    threading.Thread(target=work, daemon=True).start()
