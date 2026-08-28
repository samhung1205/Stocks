"""資料表定義。

三層資料策略：
- 輕量層（全市場、每日更新）：stocks / daily_quotes / valuations / institutional_daily / market_summary / month_revenue(全市場彙總)
- 深度層（按需抓取）：daily_quotes 歷史回補、financials / margin_daily / dividends，由 deep_fetch_log 追蹤新鮮度
- 使用層：watchlist / scores / trade_plans / trades / finance_profiles / finance_targets
"""
from sqlalchemy import (
    Column, Float, Integer, MetaData, String, Table, Text,
)

metadata = MetaData()

# ── 輕量層 ──────────────────────────────────────────────
stocks = Table(
    "stocks", metadata,
    Column("stock_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("market", String),          # twse / tpex
    Column("industry", String),
    Column("type", String),            # twse/tpex(股票) 或 ETF 由名稱判斷
    Column("updated_at", String),
)

daily_quotes = Table(
    "daily_quotes", metadata,
    Column("stock_id", String, primary_key=True),
    Column("date", String, primary_key=True),   # ISO YYYY-MM-DD
    Column("open", Float), Column("high", Float),
    Column("low", Float), Column("close", Float),
    Column("change", Float),
    Column("volume", Float),           # 股數
    Column("amount", Float),           # 成交金額(元)
    Column("transactions", Float),
)

valuations = Table(
    "valuations", metadata,
    Column("stock_id", String, primary_key=True),
    Column("date", String, primary_key=True),
    Column("pe", Float), Column("pb", Float), Column("dividend_yield", Float),
)

institutional_daily = Table(
    "institutional_daily", metadata,
    Column("stock_id", String, primary_key=True),
    Column("date", String, primary_key=True),
    Column("foreign_net", Float),      # 外資及陸資合計買賣超(股)
    Column("trust_net", Float),        # 投信
    Column("dealer_net", Float),       # 自營商合計
    Column("total_net", Float),
)

market_summary = Table(
    "market_summary", metadata,
    Column("date", String, primary_key=True),
    Column("taiex_close", Float), Column("taiex_change", Float),
    Column("taiex_change_pct", Float),
    Column("total_amount", Float),
    Column("foreign_net_amt", Float), Column("trust_net_amt", Float),
    Column("dealer_net_amt", Float),
)

month_revenue = Table(
    "month_revenue", metadata,
    Column("stock_id", String, primary_key=True),
    Column("ym", String, primary_key=True),      # YYYY-MM
    Column("revenue", Float),                    # 元
    Column("mom", Float), Column("yoy", Float), Column("acc_yoy", Float),
)

# ── 深度層 ──────────────────────────────────────────────
financials = Table(
    "financials", metadata,
    Column("stock_id", String, primary_key=True),
    Column("quarter", String, primary_key=True),  # 2026Q1
    Column("revenue", Float), Column("gross_profit", Float),
    Column("operating_income", Float), Column("pretax_income", Float),
    Column("net_income", Float), Column("eps", Float),
    Column("nonop_income", Float),
    Column("total_assets", Float), Column("total_liabilities", Float),
    Column("equity", Float), Column("inventory", Float),
    Column("receivables", Float), Column("capital", Float),
    Column("op_cashflow", Float), Column("invest_cashflow", Float),
    Column("fin_cashflow", Float), Column("capex", Float),
)

margin_daily = Table(
    "margin_daily", metadata,
    Column("stock_id", String, primary_key=True),
    Column("date", String, primary_key=True),
    Column("margin_balance", Float),   # 融資餘額(張)
    Column("short_balance", Float),    # 融券餘額(張)
)

dividends = Table(
    "dividends", metadata,
    Column("stock_id", String, primary_key=True),
    Column("date", String, primary_key=True),    # 除權息日
    Column("dividend", Float),                   # 股利合計(現金+股票)
)

deep_fetch_log = Table(
    "deep_fetch_log", metadata,
    Column("stock_id", String, primary_key=True),
    Column("dataset", String, primary_key=True),
    Column("last_date", String),
    Column("fetched_at", String),
    Column("accessed_at", String),
)

# ── 使用層 ──────────────────────────────────────────────
watchlist = Table(
    "watchlist", metadata,
    Column("stock_id", String, primary_key=True),
    Column("tier", String, default="research"),  # research / waiting / holding
    Column("note", Text),
    Column("added_at", String),
)

scores = Table(
    "scores", metadata,
    Column("stock_id", String, primary_key=True),
    # 手動評分（None = 用自動預填）；鍵名對映框架評分表
    Column("industry", Float), Column("moat", Float),
    Column("growth", Float), Column("cashflow", Float),
    Column("valuation", Float), Column("catalyst", Float),
    Column("chips", Float), Column("governance", Float),
    Column("note", Text),
    Column("updated_at", String),
)

trade_plans = Table(
    "trade_plans", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("stock_id", String, nullable=False),
    Column("created_at", String),
    Column("horizon", String),         # long / swing / short
    # 買進前八句話
    Column("q1_reason", Text), Column("q2_growth_from", Text),
    Column("q3_market_view", Text), Column("q4_my_edge", Text),
    Column("q5_priced_in", Text), Column("q6_catalyst", Text),
    Column("q7_wrong_signal", Text), Column("q8_max_loss", Float),
    Column("entry_price", Float), Column("stop_price", Float),
    Column("target_price", Float),
    Column("position_shares", Float),  # 由最大虧損反推
    Column("status", String, default="open"),  # open / closed / cancelled
)

trades = Table(
    "trades", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("plan_id", Integer),
    Column("stock_id", String, nullable=False),
    Column("date", String, nullable=False),
    Column("side", String, nullable=False),      # buy / sell
    Column("shares", Float, nullable=False),
    Column("price", Float, nullable=False),
    Column("fee", Float), Column("tax", Float),
    Column("note", Text),
)

# ── 財務規劃層 ──────────────────────────────────────────
# 一列一份具名情境（如「基準」「提早退休」），可切換比較。
# 數值事實用實體欄位（要進 SQL）；問卷/假設/退休參數是彈性巢狀結構，用 JSON 字串存，
# 避免為了每個假設值長一根欄位。
finance_profiles = Table(
    "finance_profiles", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("created_at", String), Column("updated_at", String),
    # Step 1 基本資料
    Column("age", Float), Column("retire_age", Float),
    Column("life_expectancy", Float), Column("horizon_years", Float),
    Column("goal", String),
    # Step 2 收入
    Column("income_monthly", Float), Column("income_other", Float),
    Column("bonus_annual", Float),
    # Step 2 支出
    Column("exp_living", Float), Column("exp_housing", Float),
    Column("exp_insurance", Float), Column("exp_loan", Float),
    Column("exp_other", Float),
    # Step 2 現有資產
    Column("asset_cash", Float), Column("asset_deposit", Float),
    Column("asset_stock", Float), Column("asset_etf", Float),
    Column("asset_bond", Float), Column("asset_other", Float),
    # Step 2 負債
    Column("debt_mortgage", Float), Column("debt_car", Float),
    Column("debt_credit", Float), Column("debt_card", Float),
    Column("debt_other", Float),
    # Step 4 每月投資（None = 採用系統建議區間中位數）
    Column("monthly_invest", Float),
    Column("need_income", Integer),          # 0/1 是否需要配息現金流
    # Step 5 風險（問卷算出 risk_type，risk_type_manual 可覆寫，比照 scores 的人工覆寫自動）
    Column("risk_answers", Text),            # JSON {q1..q5}
    Column("risk_type", String), Column("risk_type_manual", String),
    # 試算假設與退休參數
    Column("assumptions", Text),             # JSON 三情境報酬/通膨/費用/里程碑
    Column("retirement", Text),              # JSON 退休支出/退休金/提款設定
)

# 一列一檔標的（含 CASH 虛擬標的）。weight 為目標百分比；
# expected_return 空 → 用 universe 的 role 預設；actual_value 空 → 由交易日誌持倉帶入。
finance_targets = Table(
    "finance_targets", metadata,
    Column("profile_id", Integer, primary_key=True),
    Column("stock_id", String, primary_key=True),
    Column("weight", Float),
    Column("expected_return", Float),
    Column("actual_value", Float),
    Column("enabled", Integer, default=1),
)
