"""證交所 MIS 準即時快照（約 5 秒延遲）。

公開未授權端點：僅供盤中對「觀察名單」少量輪詢，一次最多 20 檔，
呼叫端已由 base.py 限速（3 秒/請求）。
"""
from app.datasources.base import get_json, num

URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def snapshot(pairs: list[tuple[str, str]]) -> list[dict]:
    """pairs: [(stock_id, market)]，market 為 twse/tpex。回傳即時快照列表。"""
    out = []
    for i in range(0, len(pairs), 20):
        chunk = pairs[i : i + 20]
        ex_ch = "|".join(
            f"{'tse' if m == 'twse' else 'otc'}_{sid}.tw" for sid, m in chunk
        )
        data = get_json(URL, params={"ex_ch": ex_ch, "json": "1", "delay": "0"},
                        cache_ttl=5)
        for r in data.get("msgArray", []):
            out.append({
                "stock_id": r.get("c"),
                "name": r.get("n"),
                "last": num(r.get("z")),        # 最新成交價（盤中可能為 '-'）
                "yesterday": num(r.get("y")),
                "open": num(r.get("o")), "high": num(r.get("h")),
                "low": num(r.get("l")),
                "volume": num(r.get("v")),      # 累計成交量(張)
                "best_bid": num((r.get("b") or "").split("_")[0]),
                "best_ask": num((r.get("a") or "").split("_")[0]),
                "time": r.get("%") or r.get("t"),
                "date": r.get("d"),
            })
    return out
