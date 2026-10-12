"""資料表定義。

三層資料策略：
- 輕量層（全市場、每日更新）：stocks / daily_quotes / valuations / institutional_daily / market_summary / month_revenue(全市場彙總)
- 深度層（按需抓取）：daily_quotes 歷史回補、financials / margin_daily / dividends，由 deep_fetch_log 追蹤新鮮度
- 使用層：watchlist / scores / trade_plans / trades / finance_profiles / finance_targets
"""
from sqlalchemy import (
    Column, Float, Index, Integer, MetaData, String, Table, Text, UniqueConstraint,
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
#
# Stage I1（Financial Planner x Strategy Portfolio integration）新增六個 nullable 欄位：
# 舊資料全部是 NULL，NULL 語意 = 'etf'（現有行為不變，見 app/services/finance/service.py）。
# source_type='strategy_portfolio' 時 stock_id 是 portfolio_source.portfolio_key() 產生的
# opaque key（"portfolio:<id>"），不是真實股票代號；strategy_portfolio_id/portfolio_version_id
# 是明確 pin 的版本（不是「目前最新版」），research_run_id 是可選的、pin 死的 evidence 來源。
finance_targets = Table(
    "finance_targets", metadata,
    Column("profile_id", Integer, primary_key=True),
    Column("stock_id", String, primary_key=True),
    Column("weight", Float),
    Column("expected_return", Float),
    Column("actual_value", Float),
    Column("enabled", Integer, default=1),
    Column("source_type", String),              # NULL(legacy)/'etf'/'manual'/'strategy_portfolio'
    Column("strategy_portfolio_id", Integer),    # strategy_portfolios.id，只在 source_type='strategy_portfolio'
    Column("portfolio_version_id", Integer),     # strategy_portfolio_versions.id，pin 死，不自動跳版本
    Column("research_run_id", String),           # portfolio_research_runs.id（uuid hex），optional
    Column("assumption_source", String),         # NULL(legacy)/'manual'/'scenario'/'research_informed'
    Column("assumption_evidence_ref", Text),     # JSON audit snapshot；只在 assumption_source='research_informed'
)

# Stage A+ additive schema. Columns on existing tables are migrated before use.
for _table in (month_revenue, financials):
    _table.append_column(Column('publish_date', String))
    _table.append_column(Column('publish_source', String))
stocks.append_column(Column('listed_date', String))
stocks.append_column(Column('delisted_date', String))

corporate_actions = Table(
    'corporate_actions', metadata,
    Column('stock_id', String, primary_key=True),
    Column('date', String, primary_key=True),
    Column('kind', String, primary_key=True),
    Column('source', String, primary_key=True),
    Column('before_price', Float), Column('after_price', Float),
    Column('cash_dividend', Float), Column('stock_dividend', Float),
    Column('ratio', Float),  # reference price ratio; NOT share multiplier or total return
    Column('raw_json', Text),
    # Stage D+: derived only from raw source fields (see app/corporate_actions.py).
    Column('share_multiplier', Float), Column('resolution', String),
)
benchmark_series = Table(
    'benchmark_series', metadata,
    Column('benchmark_id', String, primary_key=True),
    Column('date', String, primary_key=True), Column('value', Float, nullable=False),
    Column('source', String),
)
trading_calendar = Table('trading_calendar', metadata,
    Column('date', String, primary_key=True))
backfill_jobs = Table(
    'backfill_jobs', metadata,
    Column('id', String, primary_key=True), Column('status', String),
    Column('config_json', Text), Column('created_at', String),
    Column('updated_at', String), Column('stop_requested', Integer, default=0),
    Column('error', Text),
)
backfill_tasks = Table(
    'backfill_tasks', metadata,
    Column('job_id', String, primary_key=True),
    Column('task_key', String, primary_key=True),
    Column('phase', Integer), Column('dataset', String), Column('target', String),
    Column('start_date', String), Column('end_date', String),
    Column('status', String), Column('rows_written', Integer, default=0),
    Column('attempts', Integer, default=0), Column('error', Text),
    Column('updated_at', String),
)

# Stage L1: append-only log of daily light-layer auto-refresh attempts (one row
# per attempt, updated in place from 'running' to a terminal status). This is
# the ONLY persisted state for the auto-refresh feature -- freshness is always
# derived by combining this log with the actual data tables, never a separate
# mutable "current status" row.
daily_refresh_runs = Table(
    'daily_refresh_runs', metadata,
    Column('id', String, primary_key=True),
    Column('trigger', String),          # startup_catchup / scheduler / manual
    Column('target_date', String),      # trading date this attempt targeted (may be NULL)
    Column('status', String),           # running / completed / partial / failed
    Column('started_at', String), Column('finished_at', String),
    Column('result_json', Text), Column('error', Text),
)

# Strategy Lab (Stage D1). Additive tables; created by metadata.create_all.
# strategy_versions rows are immutable snapshots: editing a strategy appends a
# new version and never rewrites an old one, so a run always points at exactly
# the definition it executed.
strategies = Table(
    'strategies', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', String, nullable=False),
    Column('description', Text),
    Column('template_key', String, nullable=False),
    Column('archived', Integer, default=0),
    Column('created_at', String), Column('updated_at', String),
)
strategy_versions = Table(
    'strategy_versions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('strategy_id', Integer, nullable=False),
    Column('version_no', Integer, nullable=False),
    Column('template_key', String, nullable=False),
    Column('params_json', Text, nullable=False),    # template overrides
    Column('backtest_json', Text, nullable=False),  # BacktestConfig overrides
    Column('definition_hash', String, nullable=False),
    Column('note', Text),
    Column('created_at', String),
    UniqueConstraint('strategy_id', 'version_no'),
)
backtest_runs = Table(
    'backtest_runs', metadata,
    Column('id', String, primary_key=True),
    Column('strategy_id', Integer, nullable=False),
    Column('version_id', Integer, nullable=False),
    Column('status', String, nullable=False),       # queued/running/completed/failed
    Column('config_hash', String, nullable=False),
    Column('request_json', Text, nullable=False),   # resolved, self-contained snapshot
    Column('repro_json', Text),                     # engine version, data fingerprint...
    Column('publication_mode', String),
    Column('metrics_json', Text), Column('warnings_json', Text),
    Column('coverage_json', Text), Column('metadata_json', Text),
    Column('open_positions_json', Text), Column('ending_cash', Float),
    Column('error', Text),
    Column('created_at', String), Column('started_at', String), Column('finished_at', String),
)
Index('idx_runs_hash', backtest_runs.c.config_hash, backtest_runs.c.status)
Index('idx_runs_strategy', backtest_runs.c.strategy_id)
backtest_equity = Table(
    'backtest_equity', metadata,
    Column('run_id', String, primary_key=True), Column('date', String, primary_key=True),
    Column('net_equity', Float), Column('gross_equity', Float), Column('benchmark', Float),
)
backtest_trades = Table(
    'backtest_trades', metadata,
    Column('run_id', String, primary_key=True),
    Column('kind', String, primary_key=True),       # fill / closed
    Column('seq', Integer, primary_key=True),
    Column('payload_json', Text, nullable=False),
)

# Strategy Lab Stage E1: optimization jobs. A job owns pre-created run rows
# (status pending) so progress and resume are plain SQL over one table.
optimization_jobs = Table(
    'optimization_jobs', metadata,
    Column('id', String, primary_key=True),
    Column('template_key', String, nullable=False),
    Column('status', String, nullable=False),   # queued/running/completed/failed/cancelled
    Column('job_hash', String, nullable=False),
    Column('objective', String, nullable=False),
    Column('request_json', Text, nullable=False),   # frozen snapshot (window, search plan, mode...)
    Column('universe_json', Text),                  # monthly-union universe (ids + per-month counts)
    Column('coverage_json', Text),                  # panel coverage, computed once
    Column('repro_json', Text),
    Column('total_runs', Integer, default=0),
    Column('cancel_requested', Integer, default=0),
    Column('retry_failed', Integer, default=0),
    Column('panel_loads', Integer, default=0),
    Column('error', Text),
    Column('created_at', String), Column('started_at', String), Column('finished_at', String),
    # Stage F0 provenance: immutable once the row exists (SQLite triggers, see db/migrations.py).
    # Jobs created before F0 keep NULL; nothing is back-filled by guessing.
    Column('strategy_id', Integer), Column('strategy_version_id', Integer),
    Column('strategy_version_no', Integer),
    Column('definition_hash', String),      # hash of the base template+backtest definition actually run
    Column('parent_job_id', String),        # job this search-space revision was derived from
    Column('root_job_id', String),          # first job of the revision chain (itself when no parent)
    Column('revision_no', Integer),         # 0 for a root job, parent + 1 for a revision
    Column('revision_kind', String),        # 'search_space_expansion' | NULL
    Column('revision_reason', Text),
    Column('revision_json', Text),          # search-space diff + parent edge axes at creation
)
OPT_PROVENANCE_COLUMNS = (
    'strategy_id', 'strategy_version_id', 'strategy_version_no', 'definition_hash',
    'parent_job_id', 'root_job_id', 'revision_no', 'revision_kind', 'revision_reason',
    'revision_json')
Index('idx_optjobs_hash', optimization_jobs.c.job_hash, optimization_jobs.c.status)
optimization_runs = Table(
    'optimization_runs', metadata,
    Column('job_id', String, primary_key=True),
    Column('run_index', Integer, primary_key=True),
    Column('point_key', String, nullable=False),
    Column('params_json', Text, nullable=False),    # flat {path: value}
    Column('status', String, nullable=False),       # pending/running/completed/failed
    Column('run_hash', String),
    Column('cached_from_job', String),
    Column('metrics_json', Text),                   # windowed metrics + selection stats
    Column('diagnostics_json', Text),               # unresolved_action_count, warnings, coverage
    Column('error', Text),
    Column('started_at', String), Column('finished_at', String),
)
Index('idx_optruns_hash', optimization_runs.c.run_hash, optimization_runs.c.status)

# Stage F0: write-once candidate freezes for Walk Forward. Immutable except that
# test_opened_at may be set once (NULL -> timestamp); see db/migrations.py triggers.
wf_candidate_freezes = Table(
    'wf_candidate_freezes', metadata,
    Column('id', String, primary_key=True),
    Column('job_id', String, nullable=False, unique=True),   # the training optimization job
    Column('root_job_id', String),
    Column('template_key', String, nullable=False),
    Column('strategy_id', Integer), Column('strategy_version_id', Integer),
    Column('definition_hash', String),
    Column('objective', String, nullable=False),
    Column('train_start', String, nullable=False), Column('train_end', String, nullable=False),
    Column('rule_version', String, nullable=False),
    Column('plateau_label', String),
    Column('primary_point_key', String), Column('primary_params_json', Text),
    Column('region_json', Text, nullable=False),            # [{key, params}] reported as a set
    Column('edge_axes_json', Text),
    Column('candidate_digest', String, nullable=False),     # content hash, independent of ids/timestamps
    Column('created_at', String, nullable=False),
    Column('test_opened_at', String),
)
WF_FREEZE_COLUMNS = (
    'id', 'job_id', 'root_job_id', 'template_key', 'strategy_id', 'strategy_version_id',
    'definition_hash', 'objective', 'train_start', 'train_end', 'rule_version', 'plateau_label',
    'primary_point_key', 'primary_params_json', 'region_json', 'edge_axes_json',
    'candidate_digest', 'created_at')

# ── Stage F1: Walk Forward jobs / folds / normalized OOS series ─────────────
# Provenance columns of a job and the window/link columns of a fold are write-once; a completed
# fold is frozen entirely (SQLite triggers, see db/migrations.py).
walkforward_jobs = Table(
    'walkforward_jobs', metadata,
    Column('id', String, primary_key=True),
    Column('template_key', String, nullable=False),
    Column('status', String, nullable=False),        # queued/running/completed/failed/cancelled
    Column('job_hash', String, nullable=False),
    Column('request_json', Text, nullable=False),    # frozen definition, search space, objective, universe
    Column('protocol_json', Text, nullable=False),   # validated protocol
    Column('protocol_version', String, nullable=False),
    Column('folds_json', Text, nullable=False),      # planned folds + dropped + notes
    Column('total_folds', Integer, nullable=False),
    Column('strategy_id', Integer), Column('strategy_version_id', Integer),
    Column('strategy_version_no', Integer), Column('definition_hash', String),
    Column('candidate_rule', String, nullable=False),
    Column('execution_policy', String, nullable=False),
    Column('publication_mode', String, nullable=False),
    Column('provenance_json', Text),                 # universe spec, engine/oos-contract versions
    Column('cancel_requested', Integer, default=0),
    Column('error', Text),
    Column('stitched_status', String),               # NULL | available | unavailable
    Column('stitched_json', Text),                   # stitched metrics (secondary evidence) + state notes
    Column('summary_json', Text),                    # diagnostics, computed at completion
    Column('repro_json', Text),
    Column('created_at', String, nullable=False),
    Column('started_at', String), Column('finished_at', String),
)
WF_JOB_IMMUTABLE = (
    'template_key', 'job_hash', 'request_json', 'protocol_json', 'protocol_version', 'folds_json',
    'total_folds', 'strategy_id', 'strategy_version_id', 'strategy_version_no', 'definition_hash',
    'candidate_rule', 'execution_policy', 'publication_mode', 'provenance_json', 'created_at')
Index('idx_wfjobs_hash', walkforward_jobs.c.job_hash, walkforward_jobs.c.status)
walkforward_folds = Table(
    'walkforward_folds', metadata,
    Column('job_id', String, primary_key=True),
    Column('fold_index', Integer, primary_key=True),
    Column('status', String, nullable=False),        # pending/running/completed/failed
    Column('stage', String),                         # training/frozen/test_opened/oos_done
    Column('outcome', String),                       # evaluated | no_candidate (when completed)
    Column('train_start', String, nullable=False), Column('train_end', String, nullable=False),
    Column('test_start', String, nullable=False), Column('test_end', String, nullable=False),
    Column('truncated', Integer, default=0),
    Column('train_job_id', String),                  # write-once once set
    Column('freeze_id', String),                     # write-once once set
    Column('candidate_digest', String),              # write-once once set
    Column('plateau_label', String),
    Column('primary_point_key', String), Column('primary_params_json', Text),
    Column('region_json', Text), Column('edge_axes_json', Text),
    Column('primary_edge_axes_json', Text),
    Column('freeze_json', Text),                     # freeze view / no-candidate reasons, verbatim
    Column('is_json', Text),                         # IS training summary of the frozen primary
    Column('oos_metrics_json', Text),                # independent-fold metrics (primary evidence)
    Column('oos_json', Text),                        # first fill, counts, boundary signal, terminal state
    Column('severity_json', Text),                   # items + summary (+ training severity)
    Column('universe_json', Text),                   # OOS universe (ids + per-month counts)
    Column('oos_repro_json', Text),
    Column('test_opened_at', String),
    Column('error', Text),
    Column('started_at', String), Column('finished_at', String),
)
WF_FOLD_WINDOW_COLUMNS = ('train_start', 'train_end', 'test_start', 'test_end', 'truncated')
walkforward_equity = Table(
    'walkforward_equity', metadata,
    Column('job_id', String, primary_key=True),
    Column('series', String, primary_key=True),      # 'fold' (independent) | 'stitched'
    Column('fold_index', Integer, primary_key=True),  # -1 for stitched
    Column('date', String, primary_key=True),
    Column('net_equity', Float), Column('gross_equity', Float), Column('benchmark', Float),
)
walkforward_trades = Table(
    'walkforward_trades', metadata,
    Column('job_id', String, primary_key=True),
    Column('series', String, primary_key=True),
    Column('fold_index', Integer, primary_key=True),
    Column('kind', String, primary_key=True),        # fill | closed
    Column('seq', Integer, primary_key=True),
    Column('date', String), Column('stock_id', String),
    Column('payload_json', Text, nullable=False),
)

# ── Stage H1: Strategy Portfolio domain model ───────────────────────────────
# A "strategy portfolio" is a long-lived identity (e.g. "Core Five") that combines
# an arbitrary, user-defined set of Single Strategies. Versions are immutable
# (never UPDATEd, only INSERTed) by service-layer convention, same as
# ``strategy_versions`` above — editing any versioned field appends a new version
# and old ``portfolio_research_runs`` keep pointing at the version they were run on.
strategy_portfolios = Table(
    'strategy_portfolios', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', String, nullable=False),
    Column('description', Text),
    Column('archived', Integer, default=0),
    Column('created_at', String), Column('updated_at', String),
)
strategy_portfolio_versions = Table(
    'strategy_portfolio_versions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('portfolio_id', Integer, nullable=False),
    Column('version_no', Integer, nullable=False),
    Column('allocation_method', String, nullable=False),      # equal_weight | fixed_weight | inverse_volatility
    Column('allocation_config_json', Text, nullable=False),   # method settings beyond per-component weight
    Column('no_candidate_policy', String, nullable=False),    # cash_hold (only implemented policy)
    Column('rebalance_json', Text, nullable=False),           # {"frequency": "monthly"}
    Column('constraints_json', Text, nullable=False),         # max_strategy_weight/max_single_stock_weight/...
    Column('execution_json', Text, nullable=False),           # CostConfig overrides + initial_cash
    Column('definition_hash', String, nullable=False),
    Column('note', Text),
    Column('created_at', String),
    UniqueConstraint('portfolio_id', 'version_no'),
)
# Each component pins the exact Single Strategy version AND the Walk Forward job whose
# independent-fold evidence supplies its exact sleeve — never just a template name, and
# never the (mutable) latest version of a strategy.
strategy_portfolio_components = Table(
    'strategy_portfolio_components', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('portfolio_version_id', Integer, nullable=False),
    Column('strategy_id', Integer, nullable=False),
    Column('strategy_version_id', Integer, nullable=False),
    Column('strategy_version_no', Integer, nullable=False),
    Column('walkforward_job_id', String, nullable=False),
    Column('target_weight', Float),      # fixed_weight method only; NULL for equal_weight/inverse_volatility
    UniqueConstraint('portfolio_version_id', 'strategy_id'),
)

# Immutable portfolio research runs (a Walk-Forward-evidence-driven portfolio backtest).
portfolio_research_runs = Table(
    'portfolio_research_runs', metadata,
    Column('id', String, primary_key=True),
    Column('portfolio_id', Integer, nullable=False),
    Column('portfolio_version_id', Integer, nullable=False),
    Column('status', String, nullable=False),          # queued/running/completed/failed
    Column('request_json', Text, nullable=False),       # start,end,initial_cash,cost overrides
    Column('components_json', Text, nullable=False),    # pinned snapshot of components at run creation
    Column('allocation_method', String, nullable=False),
    Column('allocation_config_json', Text, nullable=False),
    Column('no_candidate_policy', String, nullable=False),
    Column('constraints_json', Text, nullable=False),
    Column('execution_json', Text, nullable=False),
    Column('metrics_json', Text), Column('warnings_json', Text),
    Column('turnover_json', Text),
    Column('repro_json', Text),
    Column('error', Text),
    Column('cancel_requested', Integer, default=0),   # Stage H1.5: cooperative cancel (checked once per date)
    Column('created_at', String), Column('started_at', String), Column('finished_at', String),
)
portfolio_research_equity = Table(
    'portfolio_research_equity', metadata,
    Column('run_id', String, primary_key=True), Column('date', String, primary_key=True),
    Column('cash', Float), Column('total_equity', Float),
    Column('daily_return', Float), Column('cash_weight', Float),
)
# Sparse: one row per REBALANCE date only (holdings/contribution/orders are otherwise
# unchanged between rebalances, same "sparse, only at rebalance dates" precedent as
# Stage G's ``target_holdings_by_rebalance_date``).
portfolio_research_rebalances = Table(
    'portfolio_research_rebalances', metadata,
    Column('run_id', String, primary_key=True), Column('date', String, primary_key=True),
    Column('payload_json', Text, nullable=False),
)
