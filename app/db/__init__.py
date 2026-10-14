"""SQLite（WAL）＋ SQLAlchemy Core；資料位置由 DB_URL 控制。

Ownership scopes/write guards and upserts currently target SQLite. A future
Postgres adapter must preserve those boundaries, not merely change the URL.
"""
from sqlalchemy import create_engine, event, text, case
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import DB_URL
from app.db.tables import metadata
from app.db import ownership

engine = create_engine(DB_URL, future=True)

if DB_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        # Stage L1.1: WAL allows only one writer transaction at a time; without a
        # busy_timeout a second writer gets an immediate "database is locked"
        # instead of waiting. app.services.data_freshness.refresh_lock() /
        # app.services.history_backfill.runner_lock() already serialize the three
        # named daily/historical writers via a shared flock, but per-stock deep
        # fetch (app/services/deep_fetch.py) is not under that lock -- this is the
        # safety net for that residual overlap, not a substitute for the lock.
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def init_db() -> None:
    from app.db.migrations import migrate
    migrate(engine)


def upsert_many(conn, table, rows: list[dict]) -> int:
    """批次 upsert（依主鍵衝突則更新）。rows 為 dict list。

    rows 欄位可不一致（如缺某些財報科目）：以聯集補 None 對齊，
    且只更新這批資料實際提供的欄位，避免衝突時把舊值洗成 NULL。
    """
    if not rows:
        return 0
    if table.name in ownership.PRIVATE_ROOTS:
        from app.identity import owner_id
        rows = [{**row, 'owner_user_id': owner_id()} for row in rows]
    if table.name in ('month_revenue', 'financials'):
        from app.publication import publication_fields
        rows = [publication_fields(table.name, r) for r in rows]
    # Group by actual keys so a sparse row never erases fields in another row.
    groups = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    if len(groups) > 1:
        return sum(upsert_many(conn, table, group) for group in groups.values())
    keys = set().union(*(r.keys() for r in rows))
    rows = [{k: r.get(k) for k in keys} for r in rows]
    pk = [c.name for c in table.primary_key.columns]
    n = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i : i + 500]
        stmt = sqlite_insert(table).values(chunk)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in table.columns
            if c.name not in pk and c.name in keys
        }
        if table.name in ('month_revenue', 'financials'):
            # Lower-quality ingestion must not rewrite values under an older,
            # better date. Actual > observed > assumed; preserve the whole row.
            def rank(col):
                return case((col == 'actual', 3), (col == 'observed_finmind', 2), else_=1)
            keep = rank(table.c.publish_source) > rank(stmt.excluded.publish_source)
            update_cols = {k: case((keep, table.c[k]), else_=v) for k, v in update_cols.items()}
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=pk, set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk)
        conn.execute(stmt)
        n += len(chunk)
    return n


def query_all(conn, sql: str, **params) -> list[dict]:
    sql, params = ownership.scoped_select(sql, params)
    rs = conn.execute(text(sql), params)
    cols = rs.keys()
    return [{k: v for k, v in zip(cols, row) if k != 'rowid'} for row in rs.fetchall()]


def query_one(conn, sql: str, **params) -> dict | None:
    rows = query_all(conn, sql, **params)
    return rows[0] if rows else None
