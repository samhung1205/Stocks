"""SQLite ownership boundary for private reads and writes.

Read helpers prepend fixed CTEs that shadow private tables with owner-filtered
relations (including descendant tables). SQL is not parsed or rewritten by
regex; identifiers in the scope graph come only from tables.py. User values
remain bound parameters. Market-only SQL is unchanged. Private SELECTs must
use query_all/query_one; explicit main.<private_table> and direct Core SELECTs
are reserved for this boundary, migrations and account administration.

SQLite triggers separately enforce owner-preserving writes, including raw SQL.
The actor is a ContextVar set by ASGI middleware/queue workers, never the client.
System scope is explicit and limited to bootstrap/migration and maintenance.
"""
import re
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import config, identity
from app.db.tables import PRIVATE_ROOTS, PRIVATE_PARENTS, metadata

PRIVATE = (*PRIVATE_ROOTS, *PRIVATE_PARENTS)
_PRIVATE_PATTERN = re.compile(r'\b(' + '|'.join(PRIVATE) + r')\b', re.I)


@event.listens_for(Engine, 'connect')
def register_identity(dbapi_conn, _):
    if isinstance(dbapi_conn, sqlite3.Connection):
        dbapi_conn.create_function('stocks_user_id', 0, lambda: identity.owner_id())
        dbapi_conn.create_function('stocks_system', 0,
            lambda: int(identity.is_system() or config.AUTH_MODE == 'basic'))


def scoped_select(sql, params):
    if config.AUTH_MODE == 'basic' or identity.is_system() or not _PRIVATE_PATTERN.search(sql):
        return sql, params
    uid = identity.owner_id()  # Missing identity never becomes an unrestricted read.
    if re.search(r'\b(?:main|temp)\s*\.', sql, re.I):
        raise PermissionError('Schema-qualified private reads are not permitted')
    if not sql.lstrip().upper().startswith('SELECT'):
        raise PermissionError('Private reads must use a scoped SELECT')
    scopes = [f'{name} AS (SELECT rowid,* FROM main.{name} WHERE owner_user_id=:__actor_id)'
              for name in PRIVATE_ROOTS]
    scopes += [f'{name} AS (SELECT rowid,* FROM main.{name} WHERE {fk} IN (SELECT id FROM {parent}))'
               for name, (parent, fk) in PRIVATE_PARENTS.items()]
    return 'WITH ' + ','.join(scopes) + ' ' + sql, {**params, '__actor_id': uid}


def _owner_expression(name, row):
    if name in PRIVATE_ROOTS:
        return f'{row}.owner_user_id'
    parent, fk = PRIVATE_PARENTS[name]
    if parent in PRIVATE_ROOTS:
        return f'(SELECT owner_user_id FROM {parent} WHERE id={row}.{fk})'
    grandparent, parent_fk = PRIVATE_PARENTS[parent]
    return (f'(SELECT p.owner_user_id FROM {parent} c JOIN {grandparent} p '
            f'ON p.id=c.{parent_fk} WHERE c.id={row}.{fk})')


def install_write_guards(conn):
    for name in PRIVATE:
        for operation, row in [('INSERT', 'NEW'), ('UPDATE', 'OLD'), ('DELETE', 'OLD')]:
            expression = _owner_expression(name, row)
            condition = f'({expression}) IS NOT stocks_user_id()'
            if operation == 'UPDATE':
                condition += f' OR ({_owner_expression(name, "NEW")}) IS NOT ({expression})'
            conn.exec_driver_sql(f'''CREATE TRIGGER IF NOT EXISTS owner_{name}_{operation.lower()}
                BEFORE {operation} ON {name}
                WHEN stocks_system()=0 AND ({condition})
                BEGIN SELECT RAISE(ABORT, 'Private resource access denied'); END''')


def migrate_ownership(conn):
    # The inactive placeholder owns all pre-account rows. Bootstrap claims id=1
    # explicitly, rather than assigning old data to whichever friend registers first.
    conn.exec_driver_sql("INSERT OR IGNORE INTO users(id,username,display_name,role,active,created_at) "
                         "VALUES(1,'__owner_pending__','管理員','admin',0,datetime('now'))")
    for name in PRIVATE_ROOTS:
        have = {r[1] for r in conn.exec_driver_sql(f'PRAGMA table_info({name})')}
        if 'owner_user_id' not in have:
            if name in ('watchlist', 'scores'):
                # Rebuild only these mutable tables to allow one stock per user.
                # Never rebuild/update immutable research tables or their ids.
                conn.exec_driver_sql(f'ALTER TABLE {name} RENAME TO _legacy_{name}')
                metadata.tables[name].create(conn)
                cols = ','.join(c.name for c in metadata.tables[name].columns if c.name != 'owner_user_id')
                conn.exec_driver_sql(f'INSERT INTO {name}({cols},owner_user_id) '
                                     f'SELECT {cols},1 FROM _legacy_{name}')
                conn.exec_driver_sql(f'DROP TABLE _legacy_{name}')
            else:
                conn.exec_driver_sql(f'ALTER TABLE {name} ADD COLUMN owner_user_id INTEGER NOT NULL DEFAULT 1')
        conn.exec_driver_sql(f'CREATE INDEX IF NOT EXISTS idx_{name}_owner ON {name}(owner_user_id)')
    install_write_guards(conn)
