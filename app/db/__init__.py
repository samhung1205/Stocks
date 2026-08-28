"""SQLite（WAL）＋ SQLAlchemy Core。日後上雲換 DB_URL 即可移植到 Postgres。"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import DB_URL
from app.db.tables import metadata

engine = create_engine(DB_URL, future=True)

if DB_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def init_db() -> None:
    metadata.create_all(engine)


def upsert_many(conn, table, rows: list[dict]) -> int:
    """批次 upsert（依主鍵衝突則更新）。rows 為 dict list。

    rows 欄位可不一致（如缺某些財報科目）：以聯集補 None 對齊，
    且只更新這批資料實際提供的欄位，避免衝突時把舊值洗成 NULL。
    """
    if not rows:
        return 0
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
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=pk, set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk)
        conn.execute(stmt)
        n += len(chunk)
    return n


def query_all(conn, sql: str, **params) -> list[dict]:
    rs = conn.execute(text(sql), params)
    cols = rs.keys()
    return [dict(zip(cols, row)) for row in rs.fetchall()]


def query_one(conn, sql: str, **params) -> dict | None:
    rows = query_all(conn, sql, **params)
    return rows[0] if rows else None
