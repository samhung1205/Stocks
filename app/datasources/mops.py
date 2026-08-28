"""公開資訊觀測站（MOPS）：全市場月營收彙總（一次請求拿整月全部公司）。

端點：https://mops.twse.com.tw/nas/t21/{sii|otc}/t21sc03_{民國年}_{月}_0.html
Big5 編碼 HTML 表格。這是免費來源中唯一能「一次拿全市場月營收」的管道，
供篩選器用；個股歷史月營收走 FinMind。
"""
import io

import pandas as pd

from app.datasources.base import get

# MOPS 2024 改版後，彙總表留在舊版網域 mopsov
URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{y}_{m}_0.html"


def monthly_revenue_all(year: int, month: int) -> list[dict]:
    """抓取 year-month（西元）上市＋上櫃全部公司月營收。回傳 month_revenue 列。"""
    out: list[dict] = []
    ym = f"{year}-{month:02d}"
    for market in ("sii", "otc"):
        url = URL.format(market=market, y=year - 1911, m=month)
        try:
            resp = get(url)
            html = resp.content.decode("big5", errors="ignore")
            tables = pd.read_html(io.StringIO(html))
        except Exception:
            continue
        for df in tables:
            # 目標表：多層欄位含「公司代號」「營業收入-當月營收」等
            cols = ["".join(map(str, c)) if isinstance(c, tuple) else str(c)
                    for c in df.columns]
            cols = [c.replace(" ", "").replace("　", "") for c in cols]
            df.columns = cols
            id_col = next((c for c in cols if "公司代號" in c), None)
            rev_col = next((c for c in cols if "當月營收" in c and "去年" not in c), None)
            if not id_col or not rev_col:
                continue
            mom_col = next((c for c in cols if "上月比較" in c), None)
            yoy_col = next((c for c in cols if "去年同月" in c), None)
            acc_col = next((c for c in cols if "前期比較" in c), None)
            for _, row in df.iterrows():
                sid = str(row[id_col]).strip()
                if not sid.isdigit() or len(sid) < 4:
                    continue
                rev = pd.to_numeric(row[rev_col], errors="coerce")
                if pd.isna(rev):
                    continue
                def f(col):
                    if not col:
                        return None
                    v = pd.to_numeric(row[col], errors="coerce")
                    return None if pd.isna(v) else float(v)
                out.append({
                    "stock_id": sid, "ym": ym,
                    "revenue": float(rev) * 1000,  # 千元 → 元
                    "mom": f(mom_col), "yoy": f(yoy_col), "acc_yoy": f(acc_col),
                })
    return out
