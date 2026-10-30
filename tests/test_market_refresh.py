"""Offline regressions for closure evidence, sparse datasets and retry disclosure."""
import datetime as dt
import json
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text
from test_data_freshness import FreshnessTestBase
from app.db import upsert_many, query_one, query_all
from app.db import tables as t
from app.services import market_daily as daily, market_coverage as coverage, data_freshness as fresh
from app.services import history_backfill as history
from app.datasources import twse, tpex


class FixedDate(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 30)


class MarketRefreshTests(FreshnessTestBase):
    def setUp(self):
        super().setUp()
        self.daily_patches = [patch.object(daily, 'engine', self.engine),
            patch.object(history, 'engine', self.engine),
            patch.object(daily, 'dt', SimpleNamespace(date=FixedDate, datetime=dt.datetime, timedelta=dt.timedelta, timezone=dt.timezone))]
        for item in self.daily_patches:
            item.start()
        self.patches.extend(self.daily_patches)

    def seed_window(self, missing=()):
        for date in ('2026-10-20','2026-10-21','2026-10-22','2026-10-27','2026-10-28','2026-10-29','2026-10-30'):
            if date not in missing:
                self._set_quote_date(date)

    def test_one_new_institutional_row_cannot_certify_both_markets(self):
        self._set_quote_date('2026-10-30')
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM institutional_daily WHERE stock_id='2330'"))
        with self.engine.connect() as conn:
            result = coverage.check(conn, '2026-10-30')
        self.assertEqual(result['quotes']['state'], 'current')
        self.assertEqual(result['institutional']['latest_date'], '2026-10-30')
        self.assertEqual(result['institutional']['state'], 'missing')
        self.assertFalse(coverage.complete(result))

    def test_nullable_close_does_not_count_as_observed_quote(self):
        self._set_quote_date('2026-10-30')
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE daily_quotes SET close=NULL WHERE stock_id='2330'"))
        with self.engine.connect() as conn:
            self.assertEqual(coverage.check(conn,'2026-10-30')['quotes']['state'], 'missing')

    def test_valuation_previous_actual_session_is_disclosed_not_redated(self):
        self._set_quote_date('2026-10-29')
        self._set_quote_date('2026-10-30')
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM valuations WHERE date='2026-10-30'"))
        with self.engine.connect() as conn:
            result=coverage.check(conn,'2026-10-30')['valuations']
        self.assertEqual(result['state'], 'publication_lag')
        self.assertEqual(result['latest_date'], '2026-10-29')

    def test_no_calendar_never_claims_fresh(self):
        self._set_quote_date('2026-10-30', calendar=False)
        with patch.object(fresh.twse,'resolve_latest_trading_date',return_value='2026-10-30'):
            result=fresh.status()
        self.assertEqual(result['freshness'],'unknown')
        self.assertFalse(result['datasets']['quotes']['calendar_verified'])

    def test_old_partial_log_does_not_override_repaired_actual_evidence(self):
        self._set_quote_date('2026-10-30')
        with self.engine.begin() as conn:
            upsert_many(conn,t.daily_refresh_runs,[{'id':'old','target_date':'2026-10-30','status':'partial','started_at':fresh._now_utc()}])
        with patch.object(fresh.twse,'resolve_latest_trading_date',return_value='2026-10-30'), patch.object(daily,'update_daily') as update:
            self.assertEqual(fresh.status()['freshness'],'fresh')
            self.assertEqual(fresh.attempt_refresh('scheduler')['reason'],'already_fresh')
        update.assert_not_called()

    def test_exhausted_retry_state_promises_no_next_check(self):
        with patch.object(fresh.config,'AUTO_REFRESH_ENABLED',True):
            state=fresh._retry_state('partial','idle',True,'2026-10-30')
        self.assertEqual(state['state'],'exhausted')
        self.assertIsNone(state['next_check_at'])
        self.assertTrue(state['requires_action'])

    def test_next_check_matches_scheduler_grid_and_taiwan_time(self):
        now=dt.datetime(2026,10,30,16,31,tzinfo=dt.timezone(dt.timedelta(hours=8)))
        with patch.object(fresh,'now_local',return_value=now), patch.object(fresh.config,'ENABLE_SCHEDULER',True):
            state=fresh._retry_state('partial','idle',False,'2026-10-30')
        self.assertEqual(state['next_check_at'],'2026-10-30T16:45:00+08:00')

    def test_empty_daily_responses_alone_never_delete_calendar(self):
        self.seed_window()
        with self.engine.begin() as conn:
            upsert_many(conn,t.trading_calendar,[{'date':'2026-10-23'}])
        with patch.object(twse,'daily_all_by_date',return_value=[]), patch.object(tpex,'daily_all_by_date',return_value=[]), \
             patch.object(twse,'monthly_trading_dates',side_effect=ValueError('invalid monthly response')), \
             patch.object(twse,'valuations_latest',return_value=[]), patch.object(tpex,'valuations_latest',return_value=[]):
            result=daily.backfill_history(11)
        self.assertTrue(result['errors'])
        with self.engine.connect() as conn:
            self.assertIsNotNone(query_one(conn,"SELECT date FROM trading_calendar WHERE date='2026-10-23'"))

    def test_isolated_closure_reconciles_with_returned_audit_evidence(self):
        self.seed_window()
        with self.engine.begin() as conn:
            upsert_many(conn,t.trading_calendar,[{'date':'2026-10-23'}])
        monthly={'dates':['2026-10-22','2026-10-27'], 'source':'official-monthly'}
        secondary={'date':'20261023','rows':0,'source':'official-tpex'}
        with patch.object(twse,'daily_all_by_date',return_value=[]), patch.object(tpex,'daily_all_by_date',return_value=[]), \
             patch.object(twse,'monthly_trading_dates',return_value=monthly), patch.object(tpex,'empty_session_evidence',return_value=secondary), \
             patch.object(twse,'valuations_latest',return_value=[]), patch.object(tpex,'valuations_latest',return_value=[]):
            result=daily.backfill_history(11)
        self.assertEqual(result['calendar_reconciliation'][0]['date'],'2026-10-23')
        self.assertEqual(result['calendar_reconciliation'][0]['tpex_evidence']['source'],'official-tpex')
        with self.engine.connect() as conn:
            self.assertIsNone(query_one(conn,"SELECT date FROM trading_calendar WHERE date='2026-10-23'"))
            self.assertEqual(query_one(conn,"SELECT COUNT(*) n FROM daily_quotes WHERE date='2026-10-23'")['n'],0)

    def test_open_or_boundary_date_is_not_a_confirmed_closure(self):
        for dates in (['2026-10-22','2026-10-23','2026-10-27'], ['2026-10-22']):
            with patch.object(twse,'monthly_trading_dates',return_value={'dates':dates,'source':'official'}):
                self.assertIsNone(daily._closed_session_evidence('2026-10-23'))
        self.assertIsNone(daily._closed_session_evidence('2026-10-30'))

    def test_partial_historical_institutional_data_is_not_skipped_by_summary(self):
        self.seed_window()
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM institutional_daily WHERE date='2026-10-21'"))
        def inst(date):
            return [{'stock_id':sid,'date':'2026-10-21','total_net':123} for sid in ('2330','6488')] if date=='20261021' else []
        with patch.object(twse,'daily_all_by_date',return_value=[]) as quotes, patch.object(tpex,'daily_all_by_date',return_value=[]), \
             patch.object(twse,'institutional_by_date',side_effect=inst), patch.object(tpex,'institutional_by_date',return_value=[]), \
             patch.object(twse,'valuations_latest',return_value=[]), patch.object(tpex,'valuations_latest',return_value=[]):
            daily.backfill_history(11)
        self.assertNotIn(('20261021',), [call.args for call in quotes.call_args_list])
        with self.engine.connect() as conn:
            self.assertEqual(query_one(conn,"SELECT COUNT(*) n FROM institutional_daily WHERE date='2026-10-21'")['n'],2)

    def test_benchmark_only_job_has_no_quote_universe_or_private_tasks(self):
        job=history.create_benchmark_job('2026-09-19','2026-10-29')
        with self.engine.connect() as conn:
            tasks=query_all(conn,'SELECT dataset FROM backfill_tasks WHERE job_id=:i',i=job['id'])
        self.assertEqual(tasks,[{'dataset':'benchmark'}])
        self.assertEqual(job['config']['datasets'],['benchmark'])
        with self.assertRaises(ValueError):
            history.create_benchmark_job(20260919, '2026-10-29')

    def test_single_old_dataset_hole_dispatches_backfill_and_rechecks(self):
        self.seed_window()
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM institutional_daily WHERE date='2026-10-21'"))
        def repair(days):
            self._set_quote_date('2026-10-21')
            return {'errors': []}
        with patch.object(twse,'resolve_latest_trading_date',return_value='2026-10-30'), \
             patch.object(daily,'update_daily') as latest, \
             patch.object(daily,'backfill_history',side_effect=repair) as backfill, \
             patch.object(fresh,'_watchlist_deep_refresh'):
            result = fresh.attempt_refresh('scheduler')
        self.assertEqual(result['status'], 'completed')
        backfill.assert_called_once()
        latest.assert_not_called()

    def test_manual_repair_logs_actual_partial_and_does_not_deep_fetch(self):
        self.seed_window()
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM institutional_daily WHERE date='2026-10-21'"))
        with patch.object(twse,'resolve_latest_trading_date',return_value='2026-10-30'), \
             patch.object(daily,'backfill_history',return_value={'errors': []}), \
             patch.object(fresh,'_watchlist_deep_refresh') as deep:
            result = fresh.run_manual_repair()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self._run_row('2026-10-30')['trigger'], 'manual_repair')
        deep.assert_not_called()

    def test_monthly_and_tpex_evidence_reject_malformed_payloads(self):
        with patch.object(twse,'get_json',return_value={'stat':'OK','data':[]}):
            with self.assertRaises(ValueError): twse.monthly_trading_dates('202610')
        for response in ({'stat':'error'}, {'stat':'ok','date':'20261023','tables':[]}):
            with patch.object(tpex,'get_json',return_value=response):
                self.assertIsNone(tpex.empty_session_evidence('20261023'))
