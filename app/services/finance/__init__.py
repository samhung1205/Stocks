"""財務規劃與 ETF 長期投資模擬。

分層：
- 純計算引擎（不碰 DB、不 import app.db，可離線單測）：
  universe / health / risk / allocation / compound / retirement / rebalance
- 讀 DB 的兩層：etf_stats（歷史含息報酬）、service（profile CRUD 與結果組裝）
"""
