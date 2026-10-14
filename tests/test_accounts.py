"""Offline account lifecycle, HTTP authorization and private-data boundaries.

All fixtures use temporary SQLite; no personal DB or external datasource access.
Tests exercise actual middleware, services, CTE scopes and database write guards.
"""
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, text
from sqlalchemy import Integer, Float
from sqlalchemy.exc import DBAPIError

from app import config, identity
from app.access import AccountAccessMiddleware
from app.api import routes
from app.db import query_all, query_one, upsert_many
from app.db import tables as t
from app.db.migrations import migrate
from app.services import accounts, strategy_lab as lab, optimization_lab as opt
from app.services import walkforward_lab as wf, portfolio_lab as pl, journal, scoring, stock_view, market_daily, data_freshness
from app.services.finance import service as finance

PASSWORD = 'a good test passphrase 2026'


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'accounts.db'))
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, 'AUTH_MODE', 'accounts'))
        self.stack.enter_context(patch.object(config, 'AUTH_COOKIE_SECURE', False))
        for module in (accounts, routes, lab, opt, wf, pl, journal, scoring, stock_view, market_daily, data_freshness, finance):
            self.stack.enter_context(patch.object(module, 'engine', self.engine))
        from app.services import research_queue
        self.stack.enter_context(patch.object(research_queue, 'engine', self.engine))
        with identity.system_scope():
            migrate(self.engine)
            accounts.bootstrap('owner', PASSWORD)
        self.owner = identity.Actor(1, 'owner', 'admin')
        with identity.actor_scope(self.owner):
            result = accounts.invite('friend', '朋友')
        accounts.consume_token(result['link'].split('=')[1], PASSWORD, 'fixture')
        self.friend = identity.Actor(result['user_id'], 'friend', 'member')
        app = FastAPI()
        app.add_middleware(AccountAccessMiddleware)
        app.include_router(routes.router)
        app.mount('/', StaticFiles(directory=Path(__file__).resolve().parents[1] / 'web', html=True))
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.stack.close()
        self.tmp.cleanup()

    def login(self, username='friend'):
        c = TestClient(self.app)
        self.addCleanup(c.close)
        response = c.post('/api/auth/login', json={'username': username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        c.headers['X-CSRF-Token'] = c.cookies['stocks_csrf']
        return c

    def create_strategy(self, actor):
        with identity.actor_scope(actor):
            return lab.create_strategy({'name': actor.username + ' private', 'template_key': 'momentum_trend'})

    def test_public_surface_and_missing_session(self):
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.post('/api/health').status_code, 401)
        self.assertEqual(self.client.get('/login.html').status_code, 200)
        self.assertEqual(self.client.get('/api/finance/profiles').status_code, 401)
        self.assertEqual(self.client.get('/finance.html', follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get('/api/auth/me', headers={'Authorization': 'Basic b3duZXI6eA=='}).status_code, 401)

    def test_member_cannot_operate_admin_endpoints_or_force_data_fetch(self):
        c = self.login()
        for path in ('/api/admin/status', '/api/admin/accounts', '/api/admin/backfill-for-backtest', '/docs', '/openapi.json'):
            self.assertEqual(c.get(path).status_code, 403, path)
        for path in ('/api/admin/update-daily', '/api/admin/sync-stocks', '/api/admin/backfill', '/api/finance/refresh-etf'):
            self.assertEqual(c.post(path, json={}).status_code, 403, path)
        self.assertEqual(c.get('/api/stocks/2330/deep?force=true').status_code, 403)
        self.assertEqual(c.post('/api/stocks/2330/deep').status_code, 403)
        admin = self.login('owner')
        self.assertEqual(admin.get('/api/stocks/2330/deep?force=true').status_code, 405)

    def test_csrf_and_cross_origin_login(self):
        c = self.login()
        csrf = c.headers.pop('X-CSRF-Token')
        self.assertEqual(c.post('/api/auth/logout').status_code, 403)
        self.assertEqual(c.post('/api/auth/logout', headers={'X-CSRF-Token': 'wrong'}).status_code, 403)
        self.assertEqual(c.post('/api/auth/logout', headers={'X-CSRF-Token': csrf, 'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'owner', 'password': PASSWORD}, headers={'Origin': 'https://evil.example'}).status_code, 403)

    def test_logout_invalidates_server_session(self):
        c = self.login()
        token = c.cookies['stocks_session']
        self.assertEqual(c.post('/api/auth/logout').status_code, 200)
        self.assertIsNone(accounts.authenticate(token))

    def test_password_change_invalidates_all_sessions(self):
        a, b = self.login(), self.login()
        token = b.cookies['stocks_session']
        r = a.post('/api/auth/password', json={'current_password': PASSWORD, 'new_password': PASSWORD + ' changed'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(accounts.authenticate(token))

    def test_disabled_user_loses_sessions_and_tokens(self):
        c = self.login()
        token = c.cookies['stocks_session']
        with identity.actor_scope(self.owner):
            reset = accounts.issue_token(self.friend.id, 'reset')
            accounts.set_active(self.friend.id, False)
        self.assertIsNone(accounts.authenticate(token))
        with self.assertRaises(accounts.AccountError):
            accounts.consume_token(reset['link'].split('=')[1], PASSWORD, 'fixture')

    def test_invite_single_use_expiry_and_revocation(self):
        with identity.actor_scope(self.owner):
            issued = accounts.invite('newfriend', '新朋友')
        token = issued['link'].split('=')[1]
        accounts.consume_token(token, PASSWORD, 'fixture')
        with self.assertRaises(accounts.AccountError):
            accounts.consume_token(token, PASSWORD, 'fixture')
        with identity.actor_scope(self.owner):
            expired = accounts.invite('expired', '過期')
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE account_tokens SET expires_at=0 WHERE user_id=:i'), {'i': expired['user_id']})
        with self.assertRaises(accounts.AccountError):
            accounts.consume_token(expired['link'].split('=')[1], PASSWORD, 'fixture')
        with identity.actor_scope(self.owner):
            revoked = accounts.invite('revoked', '撤銷')
            accounts.revoke_tokens(revoked['user_id'])
        with self.assertRaises(accounts.AccountError):
            accounts.consume_token(revoked['link'].split('=')[1], PASSWORD, 'fixture')

    def test_admin_cannot_disable_owner_and_member_cannot_invite(self):
        with identity.actor_scope(self.owner), self.assertRaises(accounts.AccountError):
            accounts.set_active(1, False)
        with identity.actor_scope(self.friend), self.assertRaises(accounts.AccountError):
            accounts.invite('hacker', 'no')

    def test_reset_does_not_log_user_in_or_keep_old_session(self):
        c = self.login()
        token = c.cookies['stocks_session']
        with identity.actor_scope(self.owner):
            issued = accounts.issue_token(self.friend.id, 'reset')
        r = self.client.post('/api/auth/complete', json={'token': issued['link'].split('=')[1], 'password': PASSWORD + ' new'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn('stocks_session', self.client.cookies)
        self.assertIsNone(accounts.authenticate(token))

    def test_operator_recovery_requires_initialized_owner_and_is_single_use(self):
        c = self.login('owner')
        session = c.cookies['stocks_session']
        issued = accounts.owner_recovery()
        token = issued['link'].split('=')[1]
        accounts.consume_token(token, PASSWORD + ' recovered', 'fixture')
        self.assertIsNone(accounts.authenticate(session))
        with self.assertRaises(accounts.AccountError):
            accounts.consume_token(token, PASSWORD, 'fixture')
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE users SET active=0 WHERE id=1'))
        with self.assertRaises(accounts.AccountError):
            accounts.owner_recovery()

    def test_basic_import_preserves_legacy_login_only_once_and_requires_operator(self):
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE users SET password_hash=NULL,active=0 WHERE id=1'))
        with patch.object(config, 'AUTH_USERNAME', 'Legacy.Owner'), patch.object(config, 'AUTH_PASSWORD', 'old-pass'):
            with identity.actor_scope(self.friend), self.assertRaises(PermissionError):
                accounts.bootstrap_from_basic()
            with identity.system_scope():
                accounts.bootstrap_from_basic()
                with self.assertRaises(accounts.AccountError):
                    accounts.bootstrap_from_basic()
            session, _ = accounts.login('Legacy.Owner', 'old-pass', 'migration')
            actor, _ = accounts.authenticate(session)
            self.assertEqual(actor.id, 1)
            self.assertEqual(actor.role, 'admin')
            with identity.actor_scope(actor), self.assertRaises(accounts.AccountError):
                accounts.change_password('old-pass', 'too-short')

    def test_session_expiry_and_cookie_attributes(self):
        with patch.object(config, 'AUTH_COOKIE_SECURE', True):
            r = self.client.post('/api/auth/login', json={'username': 'friend', 'password': PASSWORD})
        self.assertIn('HttpOnly', r.headers.get_list('set-cookie')[0])
        self.assertIn('Secure', r.headers.get_list('set-cookie')[0])
        self.assertIn('SameSite=lax', r.headers.get_list('set-cookie')[0])
        token = self.client.cookies['stocks_session']
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE auth_sessions SET expires_at=0'))
        self.assertIsNone(accounts.authenticate(token))
        self.assertEqual(r.headers['cache-control'], 'no-store')
        self.assertEqual(r.headers['referrer-policy'], 'no-referrer')

    def test_passwords_and_tokens_are_not_stored_plaintext(self):
        c = self.login()
        with self.engine.connect() as conn:
            encoded = conn.execute(text('SELECT password_hash FROM users WHERE id=1')).scalar()
            stored = conn.execute(text('SELECT token_hash FROM auth_sessions')).scalar()
        self.assertTrue(encoded.startswith('$argon2id$'))
        self.assertNotEqual(stored, c.cookies['stocks_session'])
        owner = self.login('owner')
        listing = owner.get('/api/admin/accounts').json()
        self.assertFalse(any('password_hash' in row for row in listing))

    def test_unknown_and_wrong_password_match_and_rate_limit_persists(self):
        for username in ('friend', 'unknown'):
            r = self.client.post('/api/auth/login', json={'username': username, 'password': 'wrong'})
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json()['detail'], '帳號或密碼不正確')
        for _ in range(7):
            self.client.post('/api/auth/login', json={'username': 'friend', 'password': 'wrong'})
        r = self.client.post('/api/auth/login', json={'username': 'friend', 'password': PASSWORD})
        self.assertEqual(r.status_code, 429)

    def test_watchlist_scores_and_stock_overview_are_per_user(self):
        with self.engine.begin() as conn:
            upsert_many(conn, t.stocks, [{'stock_id': '2330', 'name': 'Test'}])
        for actor in (self.owner, self.friend):
            with identity.actor_scope(actor), self.engine.begin() as conn:
                upsert_many(conn, t.watchlist, [{'stock_id': '2330', 'note': actor.username}])
                upsert_many(conn, t.scores, [{'stock_id': '2330', 'note': actor.username}])
        for actor in (self.owner, self.friend):
            with identity.actor_scope(actor), self.engine.connect() as conn:
                self.assertEqual(query_one(conn, 'SELECT note FROM watchlist')['note'], actor.username)
                self.assertEqual(query_one(conn, 'SELECT note FROM scores')['note'], actor.username)
                self.assertEqual(query_one(conn, 'SELECT COUNT(*) n FROM watchlist')['n'], 1)
            with identity.actor_scope(actor):
                self.assertEqual(stock_view.overview('2330')['watchlist']['note'], actor.username)
        c = self.login()
        self.assertEqual(c.delete('/api/watchlist/2330').status_code, 200)
        with identity.actor_scope(self.owner), self.engine.connect() as conn:
            self.assertEqual(query_one(conn, 'SELECT COUNT(*) n FROM watchlist')['n'], 1)

    def test_planner_crud_owner_spoof_and_journal_holdings(self):
        ids = []
        with self.engine.begin() as conn:
            upsert_many(conn, t.daily_quotes, [{'stock_id': '2330', 'date': '2026-10-01', 'close': 100}])
        for actor in (self.owner, self.friend):
            with identity.actor_scope(actor):
                ids.append(finance.save_profile({'name': actor.username, 'owner_user_id': 999})['id'])
                journal.add_trade({'stock_id': '2330', 'side': 'buy', 'shares': actor.id * 1000, 'price': 100})
        c = self.login()
        self.assertEqual(len(c.get('/api/finance/profiles').json()), 1)
        self.assertEqual(c.get(f'/api/finance/profiles/{ids[0]}').status_code, 404)
        self.assertEqual(c.post('/api/finance/profiles', json={'id': ids[0], 'name': 'overwrite'}).status_code, 404)
        self.assertEqual(c.delete(f'/api/finance/profiles/{ids[0]}').status_code, 404)
        with identity.actor_scope(self.friend):
            self.assertEqual(journal.positions()[0]['shares'], self.friend.id * 1000)
            self.assertEqual(finance._portfolio_actuals(['2330'])['2330'], self.friend.id * 1000 * 100)
        with identity.actor_scope(self.owner):
            self.assertEqual(finance.get_profile(ids[0])['name'], 'owner')

    def test_foreign_plan_reference_and_status_mutation_rejected(self):
        with identity.actor_scope(self.owner):
            plan = journal.create_plan({'stock_id': '2330'})
        c = self.login()
        self.assertEqual(c.post(f'/api/journal/plans/{plan}/status', json={'status': 'closed'}).status_code, 404)
        self.assertEqual(c.post('/api/journal/trades', json={'plan_id': plan, 'stock_id': '2330', 'side': 'buy', 'shares': 10, 'price': 100}).status_code, 404)

    def test_strategy_versions_and_admin_private_data_do_not_bypass_ownership(self):
        owned = self.create_strategy(self.owner)
        other = self.create_strategy(self.friend)
        c = self.login()
        sid = owned['id']
        for path in (f'/api/strategy-lab/strategies/{sid}', f'/api/strategy-lab/strategies/{sid}/versions',
                     f"/api/strategy-lab/strategies/{sid}/versions/{owned['latest_version']['id']}"):
            self.assertEqual(c.get(path).status_code, 404, path)
        self.assertEqual(c.patch(f'/api/strategy-lab/strategies/{sid}', json={'name': 'overwrite'}).status_code, 404)
        self.assertEqual([s['id'] for s in c.get('/api/strategy-lab/strategies').json()], [other['id']])
        admin = self.login('owner')
        self.assertEqual(admin.get(f"/api/strategy-lab/strategies/{other['id']}").status_code, 404)

    def test_missing_actor_and_raw_sql_writes_fail_closed(self):
        self.create_strategy(self.owner)
        with self.engine.connect() as conn, self.assertRaises(PermissionError):
            query_all(conn, 'SELECT * FROM strategies')
        with identity.actor_scope(self.friend), self.engine.begin() as conn, self.assertRaises(DBAPIError):
            conn.exec_driver_sql("UPDATE strategies SET name='hacked' WHERE owner_user_id=1")
        with identity.actor_scope(self.friend), self.engine.connect() as conn, self.assertRaises(PermissionError):
            query_all(conn, 'SELECT * FROM main.strategies')

    def test_write_guards_reject_foreign_parent_and_owner_transfer(self):
        owned = self.create_strategy(self.owner)
        other = self.create_strategy(self.friend)
        with identity.actor_scope(self.friend), self.engine.begin() as conn, self.assertRaises(DBAPIError):
            conn.execute(insert(t.strategy_versions).values(
                strategy_id=owned['id'], version_no=999, template_key='momentum_trend',
                params_json='{}', backtest_json='{}', definition_hash='foreign'))
        with identity.actor_scope(self.friend), self.engine.begin() as conn, self.assertRaises(DBAPIError):
            conn.execute(text('UPDATE strategies SET owner_user_id=1 WHERE id=:i'), {'i': other['id']})

    def test_member_freshness_does_not_disclose_maintenance_details(self):
        full = {key: 'synthetic' for key in (
            'freshness', 'expected_trading_day', 'latest_completed_quote_date',
            'latest_institutional_date', 'latest_valuation_date', 'latest_market_summary_date',
            'internal_hole_detected', 'internal_hole_check_source', 'current_status', 'research_benchmark')}
        full.update({'last_error': 'private diagnostic', 'config': {}, 'recent_runs': []})
        with patch.object(data_freshness, 'status', return_value=full):
            response = self.login().get('/api/market/freshness')
        self.assertEqual(response.status_code, 200)
        for key in ('last_error', 'config', 'recent_runs'):
            self.assertNotIn(key, response.json())

    def test_optimization_cache_and_child_rows_are_owner_scoped(self):
        with identity.actor_scope(self.owner), self.engine.begin() as conn:
            conn.execute(insert(t.optimization_jobs).values(id='private-job', status='completed', template_key='momentum_trend', job_hash='hash', objective='sharpe', request_json='{}'))
            conn.execute(insert(t.optimization_runs).values(job_id='private-job', run_index=0, point_key='point', params_json='{}', run_hash='same-hash', status='completed', metrics_json='{}'))
        with identity.actor_scope(self.friend):
            self.assertIsNone(opt._find_cached('same-hash', 'other-job'))
            with self.engine.connect() as conn:
                self.assertEqual(query_all(conn, 'SELECT * FROM optimization_runs'), [])
        c = self.login()
        for suffix in ('', '/results', '/surface', '/robustness'):
            self.assertEqual(c.get('/api/strategy-lab/optimization/jobs/private-job' + suffix).status_code, 404)
        for action in ('cancel', 'resume'):
            self.assertEqual(c.post('/api/strategy-lab/optimization/jobs/private-job/' + action, json={}).status_code, 404)

    def seed_graph(self, actor):
        """Two complete ownership graphs; values are synthetic, not research evidence."""
        records = {}
        with identity.actor_scope(actor), self.engine.begin() as conn:
            for name in (*t.PRIVATE_ROOTS, *t.PRIVATE_PARENTS):
                table = t.metadata.tables[name]
                row = {}
                for col in table.columns:
                    if col.name == 'owner_user_id':
                        continue
                    if col.primary_key or (not col.nullable and col.default is None and col.server_default is None):
                        if isinstance(col.type, Integer):
                            row[col.name] = actor.id if col.name == 'id' else 0
                        elif isinstance(col.type, Float):
                            row[col.name] = 100.0
                        else:
                            row[col.name] = '{}' if col.name.endswith('_json') else f'{name}_{col.name}_{actor.id}'
                if 'status' in row:
                    row['status'] = 'completed'
                if name in t.PRIVATE_PARENTS:
                    parent, fk = t.PRIVATE_PARENTS[name]
                    row[fk] = records[parent]
                conn.execute(insert(table).values(**row))
                records[name] = row.get('id')
        return records

    def test_all_24_private_tables_are_scoped_in_joins_and_subqueries(self):
        self.seed_graph(self.owner)
        self.seed_graph(self.friend)
        for actor in (self.owner, self.friend):
            with identity.actor_scope(actor), self.engine.connect() as conn:
                for name in (*t.PRIVATE_ROOTS, *t.PRIVATE_PARENTS):
                    self.assertEqual(query_one(conn, f'SELECT COUNT(*) n FROM {name}')['n'], 1, name)
                self.assertEqual(len(query_all(conn, '''SELECT s.id FROM strategies s
                    JOIN strategy_versions v ON v.strategy_id=s.id
                    WHERE s.id IN (SELECT strategy_id FROM strategy_versions)''')), 1)

    def test_foreign_research_results_lifecycle_and_portfolio_pins(self):
        records = self.seed_graph(self.owner)
        c = self.login()
        checks = {
            '/api/strategy-lab/runs/' + records['backtest_runs']: ('', '/result', '/equity', '/trades'),
            '/api/strategy-lab/walkforward/jobs/' + records['walkforward_jobs']: ('', '/result', '/folds', '/folds/0', '/folds/0/equity', '/folds/0/trades', '/oos', '/stitched', '/stitched/equity', '/stitched/trades'),
            '/api/strategy-lab/portfolio-research-runs/' + records['portfolio_research_runs']: ('', '/result', '/equity', '/rebalances'),
            '/api/strategy-lab/portfolios/' + str(records['strategy_portfolios']): ('', '/versions', '/research-runs'),
        }
        for root, suffixes in checks.items():
            for suffix in suffixes:
                self.assertEqual(c.get(root + suffix).status_code, 404, root + suffix)
        for table, root in [('walkforward_jobs', '/api/strategy-lab/walkforward/jobs/'), ('portfolio_research_runs', '/api/strategy-lab/portfolio-research-runs/')]:
            for action in ('cancel', 'resume'):
                self.assertEqual(c.post(root + records[table] + '/' + action, json={}).status_code, 404)
        pid = records['strategy_portfolios']
        self.assertEqual(c.post(f'/api/strategy-lab/portfolios/{pid}/clone', json={'name': 'stolen'}).status_code, 404)
        evidence = c.get('/api/finance/portfolio-evidence', params={'strategy_portfolio_id': pid, 'portfolio_version_id': records['strategy_portfolio_versions']})
        self.assertEqual(evidence.status_code, 404)
        r = c.post('/api/finance/profiles', json={'name': 'stolen pin', 'targets': [
            {'source_type': 'strategy_portfolio', 'strategy_portfolio_id': pid,
             'portfolio_version_id': records['strategy_portfolio_versions']} ]})
        self.assertEqual(r.status_code, 400)

    def test_queue_quota_and_worker_actor_are_not_shared(self):
        from app.services import research_queue
        pending, observed = [], []

        def create():
            jid = 'queued-' + str(identity.owner_id())
            with self.engine.begin() as conn:
                conn.execute(insert(t.optimization_jobs).values(id=jid, status='queued', template_key='momentum_trend', job_hash=jid, objective='sharpe', request_json='{}'))
            return {'job_id': jid, 'queued': True}

        def execute(jid):
            observed.append((jid, identity.owner_id()))

        with patch.object(research_queue._pool, 'submit', side_effect=lambda fn, *args: pending.append((fn, args))):
            with identity.actor_scope(self.owner):
                research_queue.submit(create, execute)
                with self.assertRaises(RuntimeError):
                    research_queue.submit(create, execute)
            with identity.actor_scope(self.friend):
                research_queue.submit(create, execute)
        for fn, args in pending:
            fn(*args)
        self.assertEqual(observed, [('queued-1', 1), ('queued-2', 2)])
        self.assertIsNone(identity.current_actor())

    def test_account_mode_cannot_silently_fall_back_to_basic_or_owner(self):
        with self.assertRaises(PermissionError):
            identity.owner_id()
        with patch.object(config, 'AUTH_USERNAME', 'owner'), patch.object(config, 'AUTH_PASSWORD', PASSWORD):
            self.assertEqual(self.client.get('/api/auth/me', auth=('owner', PASSWORD)).status_code, 401)


if __name__ == '__main__':
    unittest.main()
