"""財務規劃與 ETF 長期投資模擬。

分層：
- 純計算引擎（不碰 DB、不 import app.db，可離線單測）：
  universe / health / risk / allocation / compound / retirement / rebalance
- 讀 DB 的三層：etf_stats（歷史含息報酬）、service（profile CRUD 與結果組裝）、
  portfolio_source（Stage I1：Strategy Portfolio investment source 的唯讀 evidence，
  只碰 app.services.portfolio_lab 的既有唯讀函式，從不寫回 Strategy Portfolio 那一側）
"""
