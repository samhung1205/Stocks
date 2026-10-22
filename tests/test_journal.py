"""Journal evidence drafting and original-plan readback, using isolated SQLite."""
import datetime as dt
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from app import config
from app.db import query_one, upsert_many
from app.db import tables as t
from app.db.migrations import migrate
from app.services import alerts, journal, stock_view


class JournalAssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'journal.db'))
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, 'AUTH_MODE', 'basic'))
        for module in (journal, stock_view, alerts):
            self.stack.enter_context(patch.object(module, 'engine', self.engine))
        migrate(self.engine)
        days = [(dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(70)]
        with self.engine.begin() as conn:
            upsert_many(conn, t.stocks, [{'stock_id': '2330', 'name': '展示公司'}])
            upsert_many(conn, t.daily_quotes, [
                {'stock_id': '2330', 'date': day, 'close': 100 + i,
                 'high': 101 + i, 'low': 99 + i, 'volume': 10000}
                for i, day in enumerate(days)])
            upsert_many(conn, t.valuations, [
                {'stock_id': '2330', 'date': days[-1], 'pe': 20, 'dividend_yield': 2}])
            upsert_many(conn, t.month_revenue, [
                {'stock_id': '2330', 'ym': '2026-02', 'yoy': 15, 'revenue': 100000000}])
            upsert_many(conn, t.financials, [
                {'stock_id': '2330', 'quarter': '2025Q4', 'eps': 4.5, 'op_cashflow': 200000000}])

    def tearDown(self):
        self.stack.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_draft_contains_observed_dates_and_explicit_unknowns(self):
        draft = journal.draft_plan('2330', 'growth')
        self.assertEqual(draft['assistant_mode'], 'local_evidence')
        self.assertEqual(draft['entry_price'], 169)
        self.assertLess(draft['stop_price'], draft['entry_price'])
        self.assertEqual(len(draft['questions']), 7)
        self.assertIn('2026-02', draft['q2_growth_from'])
        self.assertIn('+15.0%', draft['q2_growth_from'])
        self.assertTrue(draft['q3_market_view'].startswith('待查證：'))
        self.assertTrue(draft['q4_my_edge'].startswith('待查證：'))
        self.assertTrue(draft['q6_catalyst'].startswith('待查證：'))
        self.assertNotIn('q8_max_loss', draft)
        self.assertNotIn('target_price', draft)
        self.assertTrue(any(f['as_of'] == '2025Q4' for f in draft['evidence']))

    def test_drafting_does_not_create_or_modify_private_plans(self):
        pid = journal.create_plan({'stock_id': '2330', 'q1_reason': '我的原始判斷'})
        journal.draft_plan('2330')
        with self.engine.connect() as conn:
            self.assertEqual(query_one(conn, 'SELECT COUNT(*) n FROM trade_plans')['n'], 1)
        self.assertEqual(journal.get_plan(pid)['q1_reason'], '我的原始判斷')

    def test_missing_stock_and_unknown_focus_are_explicit_errors(self):
        with self.assertRaises(LookupError):
            journal.draft_plan('999999')
        with self.assertRaises(ValueError):
            journal.draft_plan('2330', 'guaranteed_return')

    def test_sparse_data_stays_unknown_without_inventing_prices(self):
        with self.engine.begin() as conn:
            upsert_many(conn, t.stocks, [{'stock_id': '2317', 'name': '無資料公司'}])
        draft = journal.draft_plan('2317')
        self.assertEqual(draft['evidence'], [])
        self.assertIsNone(draft['entry_price'])
        self.assertIsNone(draft['stop_price'])
        for key in ('q2_growth_from', 'q5_priced_in', 'q7_wrong_signal'):
            self.assertTrue(draft[key].startswith('待查證：'))

    def test_ma_above_close_is_not_suggested_as_a_long_stop(self):
        with patch.object(stock_view, 'technical_summary', return_value={'ma60': 200}):
            self.assertIsNone(journal.draft_plan('2330')['stop_price'])

    def test_closed_plan_readback_preserves_original_answers(self):
        draft = journal.draft_plan('2330')
        payload = {key: draft[key] for key in journal.PLAN_QUESTIONS}
        pid = journal.create_plan({'stock_id': '2330', 'q8_max_loss': 1000, **payload})
        journal.update_plan_status(pid, 'closed')
        detail = journal.plan_detail(pid)
        self.assertEqual(detail['status'], 'closed')
        self.assertEqual(detail['name'], '展示公司')
        self.assertEqual(detail['missing'], [])
        self.assertIn('市場目前的預期', detail['pending'])
        for key, value in payload.items():
            self.assertEqual(detail[key], value)
        self.assertEqual(len(journal.list_plans('closed')), 1)
        self.assertEqual(journal.list_plans('open'), [])

    def test_missing_plan_and_invalid_status_do_not_silently_succeed(self):
        with self.assertRaises(LookupError):
            journal.plan_detail(999999)
        pid = journal.create_plan({'stock_id': '2330'})
        with self.assertRaises(ValueError):
            journal.update_plan_status(pid, 'made_up')
        self.assertEqual(journal.get_plan(pid)['status'], 'open')
