"""Single-process FIFO research dispatch, one outstanding request per account.

Identity is captured at submission and restored in the worker. Walk Forward
executes training optimization inside the same worker. Restart recovery remains
the labs' existing failed/resumable contract; queued work is never replayed twice.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import RLock

from sqlalchemy import text

from app import identity
from app.db import engine, query_one

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='research')
_submission_lock = RLock()


def submit(create, execute, *args, key='job_id', resume=False, **kwargs):
    actor = identity.current_actor()
    if actor is None:
        raise PermissionError('Authentication required')
    with _submission_lock:
        with engine.connect() as conn:
            count = query_one(conn, '''SELECT SUM(n) AS n FROM (
                SELECT COUNT(*) n FROM backtest_runs WHERE status IN ('queued','running')
                UNION ALL SELECT COUNT(*) FROM optimization_jobs WHERE status IN ('queued','running')
                UNION ALL SELECT COUNT(*) FROM walkforward_jobs WHERE status IN ('queued','running')
                UNION ALL SELECT COUNT(*) FROM portfolio_research_runs WHERE status IN ('queued','running'))''')['n']
        if count:
            raise RuntimeError('已有研究工作正在排隊或執行，請先完成或取消')
        queued = create(*args, **kwargs)
        if queued.get('queued'):
            run_id = args[0] if resume else queued[key]
            context = copy_context()

            def run():
                with engine.connect() as conn:
                    enabled = conn.execute(text('SELECT active FROM users WHERE id=:i'), {'i': actor.id}).scalar()
                if not enabled:
                    with engine.begin() as conn:
                        for table in ('backtest_runs', 'optimization_jobs', 'walkforward_jobs', 'portfolio_research_runs'):
                            conn.execute(text(f"UPDATE {table} SET status='failed',error=:e WHERE id=:i AND owner_user_id=:u AND status='queued'"),
                                         {'e': 'Account disabled before execution', 'i': run_id, 'u': actor.id})
                    return
                execute(run_id)

            _pool.submit(context.run, run)
        return queued
