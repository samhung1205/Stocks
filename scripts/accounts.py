"""Local operator account bootstrap/recovery. Never run by the HTTP server.

Usage: uv run python scripts/accounts.py init --username owner
       uv run python scripts/accounts.py recover
An existing SQLite DB is backed up with the backup API before any migration.
New passwords use getpass, never CLI arguments. --from-basic-env explicitly
imports existing STOCKS_AUTH_USERNAME/PASSWORD once without printing them.
"""
import argparse
import datetime as dt
import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import make_url
from app import config
from app.db import init_db
from app.identity import system_scope
from app.services import accounts
from backup_sqlite import backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'recover'))
    source_options = parser.add_mutually_exclusive_group()
    source_options.add_argument('--username')
    source_options.add_argument('--from-basic-env', action='store_true',
                                help='保留既有 Basic 帳密（只轉入一次，不顯示其值）')
    args = parser.parse_args()
    url = make_url(config.DB_URL)
    if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
        parser.error('This operator command requires a file-backed SQLite database')
    if args.action == 'init' and not (args.username or args.from_basic_env):
        parser.error('init requires --username or --from-basic-env')
    if args.action == 'recover' and (args.username or args.from_basic_env):
        parser.error('recover does not accept account initialization options')
    password = None
    if args.action == 'init' and args.from_basic_env:
        accounts._username(config.AUTH_USERNAME)
        if not 1 <= len(config.AUTH_PASSWORD) <= 128:
            parser.error('Existing Basic password is missing or cannot be imported')
    elif args.action == 'init':
        password = getpass.getpass('管理員密碼（12–128 字元）：')
        if password != getpass.getpass('再次輸入密碼：'):
            parser.error('兩次密碼不一致')
        # Validate before a backup/migration to avoid a partial operator setup.
        accounts._username(args.username)
        accounts._password(password)
    source = Path(url.database)
    if source.exists():
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup(source, config.DATA_DIR / 'backups' / f'before-accounts-{stamp}.db')
    elif args.action == 'recover':
        parser.error('Database does not exist')
    with system_scope():
        init_db()
        if args.action == 'init':
            if args.from_basic_env:
                accounts.bootstrap_from_basic()
            else:
                accounts.bootstrap(args.username, password)
            print('管理員已建立，既有私人資料保留歸屬於此帳號。')
        else:
            result = accounts.owner_recovery()
            print('請將下列路徑接在網站網址後開啟，30 分鐘內有效，僅顯示這一次：')
            print(result['link'])


if __name__ == '__main__':
    main()
