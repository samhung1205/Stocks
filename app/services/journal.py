"""交易日誌：買進前八句話計畫、交易紀錄、持倉與績效統計。

核心紀律（框架第六章）：先決定最多能虧多少，再反推部位大小。
"""
import datetime as dt

from sqlalchemy import insert

from app.config import FEE_RATE, FEE_DISCOUNT, TAX_RATE
from app.db import engine, query_all, query_one
from app.db.tables import trade_plans, trades


def draft_plan(stock_id: str) -> dict:
    """依現有資料草擬八句話中「可由數據回答」的部分（q5/q6/q7）與建議進場/停損價。

    q1（原因）、q2（成長來源具體驅動力）、q3（市場預期解讀）、q4（你的優勢）
    需要主觀判斷或產業/法說會資訊，本質上無法自動生成，留白讓你自己寫——
    這也是刻意的：如果這幾句能被公式算出來，就不會是「你的優勢」了。
    """
    from app.services import stock_view, alerts as alerts_svc

    val = stock_view.valuation_band(stock_id)
    tech = stock_view.technical_summary(stock_id)
    alist = alerts_svc.scan(stock_id)
    cur = val.get("current") or {}

    q5_parts = []
    if cur.get("pe") is not None and val.get("pe_percentile") is not None:
        q5_parts.append(f"本益比 {cur['pe']:.1f} 倍，位於自身五年歷史第 {val['pe_percentile']:.0f} 百分位")
    if val.get("eps_ttm") is not None:
        q5_parts.append(f"近四季EPS合計 {val['eps_ttm']:.2f} 元")
    if cur.get("dividend_yield") is not None:
        q5_parts.append(f"殖利率 {cur['dividend_yield']:.1f}%")
    q5 = ("；".join(q5_parts) + "（僅供參考，仍需你判斷這個估值是否已完全反映未來成長）"
          if q5_parts else "")

    wrong = [a["message"] for a in alist]
    if tech.get("ma60"):
        wrong.append(f"股價跌破60日線（{tech['ma60']}）")
    if tech.get("ma240"):
        wrong.append(f"股價跌破年線（{tech['ma240']}）")
    q7 = "；".join(wrong)

    q6 = "月營收預計於下月10日前公布；季報、法說會與除權息時間請至公開資訊觀測站查詢"

    return {
        "q5_priced_in": q5, "q6_catalyst": q6, "q7_wrong_signal": q7,
        "entry_price": tech.get("close"), "stop_price": tech.get("ma60"),
    }


def suggest_position(entry: float, stop: float, max_loss: float) -> dict:
    """由最大虧損金額與停損價反推部位（股）。"""
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"error": "停損價必須低於進場價"}
    shares = int(max_loss / risk_per_share)
    lots = shares // 1000
    return {
        "risk_per_share": round(risk_per_share, 2),
        "max_shares": shares,
        "suggest_lots": lots,
        "suggest_shares": lots * 1000 if lots else shares,
        "capital_needed": round(shares * entry),
    }


def create_plan(payload: dict) -> int:
    row = {k: payload.get(k) for k in (
        "stock_id", "horizon", "q1_reason", "q2_growth_from", "q3_market_view",
        "q4_my_edge", "q5_priced_in", "q6_catalyst", "q7_wrong_signal",
        "q8_max_loss", "entry_price", "stop_price", "target_price",
        "position_shares")}
    row["created_at"] = dt.datetime.now().isoformat(timespec="seconds")
    row["status"] = "open"
    with engine.begin() as conn:
        result = conn.execute(insert(trade_plans).values(**row))
        return result.inserted_primary_key[0]


def plan_completeness(plan: dict) -> list[str]:
    """八句話未填清單（提示，不強制擋單）。"""
    labels = {
        "q1_reason": "1. 我買這家公司是因為什麼？",
        "q2_growth_from": "2. 未來獲利成長來自哪裡？",
        "q3_market_view": "3. 市場目前的主要預期是什麼？",
        "q4_my_edge": "4. 我的看法與市場有何不同？",
        "q5_priced_in": "5. 目前價格已經反映多少成長？",
        "q6_catalyst": "6. 下一個催化事件是什麼？",
        "q7_wrong_signal": "7. 哪些數據代表我判斷錯誤？",
        "q8_max_loss": "8. 最多能承受多少虧損？",
    }
    return [v for k, v in labels.items() if not plan.get(k)]


def add_trade(payload: dict) -> int:
    shares = float(payload["shares"])
    price = float(payload["price"])
    side = payload["side"]
    gross = shares * price
    fee = round(max(gross * FEE_RATE * FEE_DISCOUNT, 1))
    tax = round(gross * TAX_RATE) if side == "sell" else 0
    row = {
        "plan_id": payload.get("plan_id"),
        "stock_id": payload["stock_id"],
        "date": payload.get("date") or dt.date.today().isoformat(),
        "side": side, "shares": shares, "price": price,
        "fee": payload.get("fee", fee), "tax": payload.get("tax", tax),
        "note": payload.get("note"),
    }
    with engine.begin() as conn:
        result = conn.execute(insert(trades).values(**row))
        return result.inserted_primary_key[0]


def positions() -> list[dict]:
    """目前持倉（平均成本法）＋最新市價未實現損益。"""
    with engine.begin() as conn:
        rows = query_all(conn, """
            SELECT t.*, s.name FROM trades t
            LEFT JOIN stocks s ON s.stock_id = t.stock_id
            ORDER BY t.date, t.id""")
        latest = {r["stock_id"]: r["close"] for r in query_all(conn, """
            SELECT stock_id, close FROM daily_quotes
            WHERE (stock_id, date) IN (
                SELECT stock_id, MAX(date) FROM daily_quotes GROUP BY stock_id)""")}
    pos: dict[str, dict] = {}
    for t in rows:
        p = pos.setdefault(t["stock_id"], {
            "stock_id": t["stock_id"], "name": t.get("name"),
            "shares": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            p["cost"] += t["shares"] * t["price"] + (t["fee"] or 0)
            p["shares"] += t["shares"]
        else:
            if p["shares"] > 0:
                avg = p["cost"] / p["shares"]
                p["cost"] -= avg * t["shares"]
            p["shares"] -= t["shares"]
    out = []
    for p in pos.values():
        if p["shares"] <= 0:
            continue
        avg = p["cost"] / p["shares"]
        last = latest.get(p["stock_id"])
        mv = last * p["shares"] if last else None
        out.append({
            **p, "avg_cost": round(avg, 2), "last": last,
            "market_value": round(mv) if mv else None,
            "unrealized": round(mv - p["cost"]) if mv else None,
            "unrealized_pct": round((mv / p["cost"] - 1) * 100, 2) if mv else None,
        })
    return sorted(out, key=lambda x: -(x["market_value"] or 0))


def performance() -> dict:
    """已實現損益統計：勝率、平均賺賠、盈虧比（框架：期望值思維）。"""
    with engine.begin() as conn:
        rows = query_all(conn, "SELECT * FROM trades ORDER BY stock_id, date, id")
    realized: list[dict] = []
    book: dict[str, dict] = {}
    for t in rows:
        b = book.setdefault(t["stock_id"], {"shares": 0.0, "cost": 0.0})
        if t["side"] == "buy":
            b["cost"] += t["shares"] * t["price"] + (t["fee"] or 0)
            b["shares"] += t["shares"]
        else:
            if b["shares"] <= 0:
                continue
            avg = b["cost"] / b["shares"]
            sold = min(t["shares"], b["shares"])
            pnl = (t["price"] - avg) * sold - (t["fee"] or 0) - (t["tax"] or 0)
            realized.append({"stock_id": t["stock_id"], "date": t["date"],
                             "shares": sold, "pnl": round(pnl),
                             "pnl_pct": round((t["price"] / avg - 1) * 100, 2)})
            b["cost"] -= avg * sold
            b["shares"] -= sold
    wins = [r for r in realized if r["pnl"] > 0]
    losses = [r for r in realized if r["pnl"] <= 0]
    avg_win = sum(r["pnl"] for r in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(r["pnl"] for r in losses) / len(losses)) if losses else 0
    return {
        "closed_trades": len(realized),
        "total_pnl": sum(r["pnl"] for r in realized),
        "win_rate": round(len(wins) / len(realized) * 100, 1) if realized else None,
        "avg_win": round(avg_win), "avg_loss": round(avg_loss),
        "profit_factor": round(avg_win / avg_loss, 2) if avg_loss else None,
        "trades": realized[-100:],
    }


def list_plans(status: str | None = None) -> list[dict]:
    sql = """SELECT p.*, s.name FROM trade_plans p
             LEFT JOIN stocks s ON s.stock_id = p.stock_id"""
    if status:
        sql += " WHERE p.status = :st"
    sql += " ORDER BY p.created_at DESC"
    with engine.begin() as conn:
        plans = query_all(conn, sql, st=status) if status else query_all(conn, sql)
    for p in plans:
        p["missing"] = plan_completeness(p)
    return plans


def get_plan(plan_id: int) -> dict | None:
    with engine.begin() as conn:
        return query_one(conn, "SELECT * FROM trade_plans WHERE id=:i", i=plan_id)


def update_plan_status(plan_id: int, status: str) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE trade_plans SET status=? WHERE id=?",
                             (status, plan_id))


def list_trades() -> list[dict]:
    with engine.begin() as conn:
        return query_all(conn, """
            SELECT t.*, s.name FROM trades t
            LEFT JOIN stocks s ON s.stock_id = t.stock_id
            ORDER BY t.date DESC, t.id DESC LIMIT 200""")
