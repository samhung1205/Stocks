"""Transactional SQLite migrations; v7 rebuilds mutable watchlist/scores only.

BEGIN IMMEDIATE serializes startup/migration writers; schema and ledger commit
atomically. Existing databases and fresh databases take the same path.
"""
from app import corporate_actions
from app.db.tables import (OPT_PROVENANCE_COLUMNS, WF_FOLD_WINDOW_COLUMNS, WF_FREEZE_COLUMNS,
                           WF_JOB_IMMUTABLE, metadata)
from app.publication import publication_fields

INDEXES = {
    'idx_quotes_date': ('daily_quotes', 'date'),
    'idx_insti_date': ('institutional_daily', 'date'),
    'idx_val_date': ('valuations', 'date'),
    'idx_revenue_ym': ('month_revenue', 'ym'),
    'idx_revenue_publish': ('month_revenue', 'publish_date'),
    'idx_financial_publish': ('financials', 'publish_date'),
    'idx_financial_quarter': ('financials', 'quarter'),
    'idx_actions_date': ('corporate_actions', 'date'),
    'idx_benchmark_date': ('benchmark_series', 'date'),
    'idx_backfill_pending': ('backfill_tasks', 'job_id, status, phase'),
    'idx_daily_refresh_target': ('daily_refresh_runs', 'target_date, status'),
}


def _differs(columns):
    return ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in columns)


TRIGGERS = {
    # Provenance is fixed at creation: any later change (including NULL -> value) aborts.
    'trg_optjobs_provenance_immutable': f"""CREATE TRIGGER IF NOT EXISTS trg_optjobs_provenance_immutable
        BEFORE UPDATE OF {', '.join(OPT_PROVENANCE_COLUMNS)} ON optimization_jobs
        WHEN {_differs(OPT_PROVENANCE_COLUMNS)}
        BEGIN SELECT RAISE(ABORT, 'optimization job provenance is immutable'); END""",
    'trg_wf_freeze_immutable': f"""CREATE TRIGGER IF NOT EXISTS trg_wf_freeze_immutable
        BEFORE UPDATE ON wf_candidate_freezes
        WHEN {_differs(WF_FREEZE_COLUMNS)}
             OR (OLD.test_opened_at IS NOT NULL AND NEW.test_opened_at IS NOT OLD.test_opened_at)
        BEGIN SELECT RAISE(ABORT, 'candidate freeze is immutable'); END""",
    # Stage F1: walk-forward evidence.  Job definition is write-once; a completed fold is frozen
    # (cancel / resume / restart can never touch it); OOS series of a fold are append-only.
    'trg_wfjob_definition_immutable': f"""CREATE TRIGGER IF NOT EXISTS trg_wfjob_definition_immutable
        BEFORE UPDATE ON walkforward_jobs
        WHEN {_differs(WF_JOB_IMMUTABLE)}
        BEGIN SELECT RAISE(ABORT, 'walk-forward job definition is immutable'); END""",
    'trg_wffold_completed_immutable': """CREATE TRIGGER IF NOT EXISTS trg_wffold_completed_immutable
        BEFORE UPDATE ON walkforward_folds
        WHEN OLD.status = 'completed'
        BEGIN SELECT RAISE(ABORT, 'completed walk-forward fold is immutable'); END""",
    'trg_wffold_completed_no_delete': """CREATE TRIGGER IF NOT EXISTS trg_wffold_completed_no_delete
        BEFORE DELETE ON walkforward_folds
        WHEN OLD.status = 'completed'
        BEGIN SELECT RAISE(ABORT, 'completed walk-forward fold is immutable'); END""",
    'trg_wffold_links_immutable': f"""CREATE TRIGGER IF NOT EXISTS trg_wffold_links_immutable
        BEFORE UPDATE ON walkforward_folds
        WHEN {_differs(WF_FOLD_WINDOW_COLUMNS)}
             OR (OLD.train_job_id IS NOT NULL AND NEW.train_job_id IS NOT OLD.train_job_id)
             OR (OLD.freeze_id IS NOT NULL AND NEW.freeze_id IS NOT OLD.freeze_id)
             OR (OLD.candidate_digest IS NOT NULL AND NEW.candidate_digest IS NOT OLD.candidate_digest)
             OR (OLD.test_opened_at IS NOT NULL AND NEW.test_opened_at IS NOT OLD.test_opened_at)
        BEGIN SELECT RAISE(ABORT, 'walk-forward fold window / links are immutable'); END""",
    'trg_wfequity_fold_append_only': """CREATE TRIGGER IF NOT EXISTS trg_wfequity_fold_append_only
        BEFORE UPDATE ON walkforward_equity WHEN OLD.series = 'fold'
        BEGIN SELECT RAISE(ABORT, 'independent OOS series are immutable'); END""",
    'trg_wfequity_fold_no_delete': """CREATE TRIGGER IF NOT EXISTS trg_wfequity_fold_no_delete
        BEFORE DELETE ON walkforward_equity WHEN OLD.series = 'fold'
        BEGIN SELECT RAISE(ABORT, 'independent OOS series are immutable'); END""",
    'trg_wftrades_fold_append_only': """CREATE TRIGGER IF NOT EXISTS trg_wftrades_fold_append_only
        BEFORE UPDATE ON walkforward_trades WHEN OLD.series = 'fold'
        BEGIN SELECT RAISE(ABORT, 'independent OOS series are immutable'); END""",
    'trg_wftrades_fold_no_delete': """CREATE TRIGGER IF NOT EXISTS trg_wftrades_fold_no_delete
        BEFORE DELETE ON walkforward_trades WHEN OLD.series = 'fold'
        BEGIN SELECT RAISE(ABORT, 'independent OOS series are immutable'); END""",
    'trg_wf_freeze_no_delete': """CREATE TRIGGER IF NOT EXISTS trg_wf_freeze_no_delete
        BEFORE DELETE ON wf_candidate_freezes
        BEGIN SELECT RAISE(ABORT, 'candidate freeze is immutable'); END""",
}


def migrate(engine):
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('Stage A+ migrations currently support SQLite only')
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            conn.exec_driver_sql('CREATE TABLE IF NOT EXISTS schema_migrations '
                                 '(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
            metadata.create_all(conn)
            if not conn.exec_driver_sql('SELECT 1 FROM schema_migrations WHERE version=1').first():
                for table, columns in {
                    'stocks': ('listed_date', 'delisted_date'),
                    'month_revenue': ('publish_date', 'publish_source'),
                    'financials': ('publish_date', 'publish_source'),
                }.items():
                    have = {r[1] for r in conn.exec_driver_sql(f'PRAGMA table_info({table})')}
                    for col in columns:
                        if col not in have:
                            conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {col} TEXT')
                for table, period in [('month_revenue', 'ym'), ('financials', 'quarter')]:
                    rows = conn.exec_driver_sql(f'SELECT stock_id,{period} FROM {table} '
                                                'WHERE publish_date IS NULL OR publish_source IS NULL').mappings()
                    values = []
                    for row in rows:
                        r = publication_fields(table, dict(row))
                        values.append((r['publish_date'], r['publish_source'], r['stock_id'], r[period]))
                    if values:
                        conn.exec_driver_sql(f'UPDATE {table} SET publish_date=?,publish_source=? '
                                             f'WHERE stock_id=? AND {period}=?', values)
                for name, (table, cols) in INDEXES.items():
                    conn.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS {name} ON {table}({cols})')
                conn.exec_driver_sql("INSERT INTO schema_migrations VALUES(1,datetime('now'))")
            if not conn.exec_driver_sql('SELECT 1 FROM schema_migrations WHERE version=2').first():
                # Stage D+: derived corporate-action fields, rebuilt from stored raw_json only.
                have = {r[1] for r in conn.exec_driver_sql('PRAGMA table_info(corporate_actions)')}
                if 'share_multiplier' not in have:
                    conn.exec_driver_sql('ALTER TABLE corporate_actions ADD COLUMN share_multiplier FLOAT')
                if 'resolution' not in have:
                    conn.exec_driver_sql('ALTER TABLE corporate_actions ADD COLUMN resolution VARCHAR')
                corporate_actions.repair(conn)
                conn.exec_driver_sql("INSERT INTO schema_migrations VALUES(2,datetime('now'))")
            if not conn.exec_driver_sql('SELECT 1 FROM schema_migrations WHERE version=3').first():
                # Stage F0: optimization provenance columns (legacy rows stay NULL) + write-once guards.
                have = {r[1] for r in conn.exec_driver_sql('PRAGMA table_info(optimization_jobs)')}
                types = {'strategy_id': 'INTEGER', 'strategy_version_id': 'INTEGER',
                         'strategy_version_no': 'INTEGER', 'revision_no': 'INTEGER'}
                for col in OPT_PROVENANCE_COLUMNS:
                    if col not in have:
                        conn.exec_driver_sql(
                            f'ALTER TABLE optimization_jobs ADD COLUMN {col} {types.get(col, "TEXT")}')
                conn.exec_driver_sql("INSERT INTO schema_migrations VALUES(3,datetime('now'))")
            if not conn.exec_driver_sql('SELECT 1 FROM schema_migrations WHERE version=4').first():
                # Stage H1.5: cooperative cancel for portfolio research runs (legacy rows default 0).
                have = {r[1] for r in conn.exec_driver_sql('PRAGMA table_info(portfolio_research_runs)')}
                if 'cancel_requested' not in have:
                    conn.exec_driver_sql(
                        'ALTER TABLE portfolio_research_runs ADD COLUMN cancel_requested INTEGER DEFAULT 0')
                conn.exec_driver_sql("INSERT INTO schema_migrations VALUES(4,datetime('now'))")
            if not conn.exec_driver_sql('SELECT 1 FROM schema_migrations WHERE version=5').first():
                # Stage I1: Financial Planner <-> Strategy Portfolio integration pins.
                # All nullable; legacy rows stay NULL (= 'etf', unchanged behavior) -- no backfill.
                have = {r[1] for r in conn.exec_driver_sql('PRAGMA table_info(finance_targets)')}
                types = {'source_type': 'TEXT', 'strategy_portfolio_id': 'INTEGER',
                         'portfolio_version_id': 'INTEGER', 'research_run_id': 'TEXT',
                         'assumption_source': 'TEXT', 'assumption_evidence_ref': 'TEXT'}
                for col, sqltype in types.items():
                    if col not in have:
                        conn.exec_driver_sql(f'ALTER TABLE finance_targets ADD COLUMN {col} {sqltype}')
                conn.exec_driver_sql("INSERT INTO schema_migrations VALUES(5,datetime('now'))")
            if not conn.exec_driver_sql('SELECT 1 FROM schema_migrations WHERE version=6').first():
                # Stage L1: daily_refresh_runs is a brand-new table (metadata.create_all above
                # already created it on both fresh and existing DBs) -- only its index needs a
                # versioned migration, since INDEXES is otherwise only applied under version 1.
                table, cols = INDEXES['idx_daily_refresh_target']
                conn.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_daily_refresh_target ON {table}({cols})')
                conn.exec_driver_sql("INSERT INTO schema_migrations VALUES(6,datetime('now'))")
            if not conn.exec_driver_sql('SELECT 1 FROM schema_migrations WHERE version=7').first():
                from app.db.ownership import migrate_ownership
                migrate_ownership(conn)
                conn.exec_driver_sql("INSERT INTO schema_migrations VALUES(7,datetime('now'))")
            for sql in TRIGGERS.values():
                conn.exec_driver_sql(sql)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
