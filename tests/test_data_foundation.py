"""Offline tests; temporary SQLite DBs, mocked external requests only."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from app.db import upsert_many, query_all, query_one
from app.db import tables as t
from app.db.migrations import migrate, INDEXES
from app.publication import financial_deadline, revenue_deadline
from app.datasources import finmind
from app.services import data_foundation as data, history_backfill as jobs


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + self.tmp.name + '/test.db')
        migrate(self.engine)
        self.patches = [patch.object(jobs, 'engine', self.engine),
                        patch.object(jobs.config, 'DATA_DIR', Path(self.tmp.name))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_legacy_migration_idempotent_indexes_and_user_data(self):
        legacy = create_engine('sqlite:///' + self.tmp.name + '/legacy.db')
        with legacy.begin() as c:
            c.exec_driver_sql('CREATE TABLE stocks(stock_id TEXT PRIMARY KEY,name TEXT)')
            c.exec_driver_sql('CREATE TABLE month_revenue(stock_id TEXT,ym TEXT,revenue FLOAT,PRIMARY KEY(stock_id,ym))')
            c.exec_driver_sql('CREATE TABLE financials(stock_id TEXT,quarter TEXT,eps FLOAT,PRIMARY KEY(stock_id,quarter))')
            c.exec_driver_sql("INSERT INTO month_revenue VALUES('2330','2025-12',123)")
            c.exec_driver_sql("INSERT INTO financials VALUES('2330','2026Q1',8)")
        migrate(legacy)
        migrate(legacy)
        with legacy.connect() as c:
            self.assertEqual(query_one(c, 'SELECT COUNT(*) n FROM schema_migrations')['n'], 6)  # v1 Stage A+, v2 Stage D+ action repair, v3 Stage F0 provenance, v4 Stage H1.5 portfolio cancel, v5 Stage I1 finance_targets pins, v6 Stage L1 daily_refresh_runs index
            r = query_one(c, 'SELECT * FROM financials')
            self.assertEqual((r['eps'], r['publish_date'], r['publish_source']), (8, '2026-05-15', 'assumed_deadline'))
            for name, (table, cols) in INDEXES.items():
                col = cols.split(',')[0]
                plan = query_all(c, f'EXPLAIN QUERY PLAN SELECT * FROM {table} WHERE {col}=:x', x='2026-01-01')
                self.assertIn('INDEX', str(plan))
                if name in ('idx_quotes_date', 'idx_insti_date', 'idx_val_date', 'idx_revenue_ym'):
                    self.assertIn(name, str(plan))
        legacy.dispose()

    def test_migration_rolls_back_ddl_and_ledger(self):
        bad = create_engine('sqlite:///' + self.tmp.name + '/bad.db')
        with bad.begin() as c:
            c.exec_driver_sql('CREATE TABLE financials(stock_id TEXT,quarter TEXT,PRIMARY KEY(stock_id,quarter))')
            c.exec_driver_sql("INSERT INTO financials VALUES('2330','bad')")
        with self.assertRaises(ValueError): migrate(bad)
        with bad.connect() as c:
            self.assertNotIn('publish_date', str(c.exec_driver_sql('PRAGMA table_info(financials)').all()))
            self.assertIsNone(c.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='schema_migrations'").first())
        bad.dispose()

    def test_deadlines_and_overrides(self):
        self.assertEqual([financial_deadline('2025Q' + str(q)) for q in range(1,5)],
                         ['2025-05-15','2025-08-14','2025-11-14','2026-03-31'])
        self.assertEqual(revenue_deadline('2025-12'), '2026-01-10')
        with patch.dict('os.environ', {'STOCKS_PUBLICATION_OVERRIDES':'{"2330:2026Q1":"2026-06-01"}'}):
            with self.engine.begin() as c:
                upsert_many(c, t.financials, [{'stock_id':'2330','quarter':'2026Q1','eps':1}])
                self.assertEqual(query_one(c,'SELECT publish_date FROM financials')['publish_date'],'2026-06-01')

    def test_publication_strict_no_same_day_future_unknown_assumed(self):
        with self.engine.begin() as c:
            for sid, source, date in [('A','actual','2026-02-10'), ('B','observed_finmind','2026-02-12'),
                                      ('C','assumed_deadline','2026-02-10'), ('D','actual','2026-03-10')]:
                upsert_many(c,t.month_revenue,[dict(stock_id=sid,ym='2026-01',revenue=100,publish_date=date,publish_source=source)])
            self.assertEqual(data.available_fundamentals(c,'month_revenue','2026-02-10'), [])
            self.assertEqual([r['stock_id'] for r in data.available_fundamentals(c,'month_revenue','2026-02-11')], ['A'])
            self.assertEqual({r['stock_id'] for r in data.available_fundamentals(c,'month_revenue','2026-02-13')}, {'A','B'})
            self.assertEqual(len(data.available_fundamentals(c,'month_revenue','2026-02-13',allow_assumed=True)),3)
            self.assertEqual(data.available_fundamentals(c,'financials','2026-02-13'),[])

    def test_observation_is_not_actual_and_fallback_never_overwrites_actual(self):
        raw = [dict(stock_id='2330',revenue_year=2026,revenue_month=4,revenue=100,create_time='2026-05-08')]
        with patch.object(finmind,'fetch',return_value=raw):
            rows = finmind.month_revenue('2330','2026-01-01')
        self.assertEqual(rows[0]['publish_source'],'observed_finmind')
        with self.engine.begin() as c:
            upsert_many(c,t.month_revenue,rows)
            upsert_many(c,t.month_revenue,[dict(stock_id='2330',ym='2026-04',revenue=200)])
            self.assertEqual(query_one(c,'SELECT revenue FROM month_revenue')['revenue'],100)
            upsert_many(c,t.month_revenue,[dict(stock_id='2330',ym='2026-04',revenue=101,
                                               publish_date='2026-05-07',publish_source='actual')])
            upsert_many(c,t.month_revenue,rows)
            self.assertEqual(query_one(c,'SELECT revenue FROM month_revenue')['revenue'],101)
        self.assertEqual(finmind.revenue_publication({**raw[0],'create_time':''}),{})

    def test_sparse_upsert_preserves_each_rows_missing_fields(self):
        with self.engine.begin() as c:
            upsert_many(c,t.stocks,[dict(stock_id='A',name='A',listed_date='2020-01-01')])
            upsert_many(c,t.stocks,[dict(stock_id='A',name='Updated'),dict(stock_id='B',name='B',listed_date='2021-01-01')])
            self.assertEqual(query_one(c,"SELECT listed_date FROM stocks WHERE stock_id='A'")['listed_date'],'2020-01-01')

    def test_liquidity_threshold_no_future_and_delisted(self):
        with self.engine.begin() as c:
            upsert_many(c,t.stocks,[dict(stock_id=s,name=s,type='stock',listed_date='2020-01-01',
                                        delisted_date='2025-06-01' if s=='1102' else None) for s in ['1101','1102','1103']])
            upsert_many(c,t.daily_quotes,[dict(stock_id=s,date=d,amount=amount) for s,d,amount in [
                ('1101','2025-04-01',30e6),('1101','2025-05-01',30e6),('1101','2025-06-01',1e12),
                ('1102','2025-04-01',40e6),('1102','2025-05-01',40e6),('1103','2025-04-01',10e6),('1103','2025-05-01',10e6)]])
            self.assertEqual([r['stock_id'] for r in data.liquid_universe(c,'2025-06-01',30e6,2)],['1101'])
            self.assertEqual(data.liquid_universe(c,'2025-06-01',31e6,2),[])
            self.assertEqual(len(data.liquid_universe(c,'2025-06-01',0,2)),2)
            upsert_many(c,t.stocks,[dict(stock_id='1101',name='1101',listed_date='2025-07-01')])
            self.assertEqual(data.liquid_universe(c,'2025-06-01',30e6,2),[])

    def test_stock_info_date_not_listing(self):
        with self.engine.begin() as c:
            data.ingest_stock_info(c,[dict(stock_id='2330',stock_name='TSMC',type='twse',date='2026-01-01')])
            self.assertIsNone(query_one(c,'SELECT listed_date FROM stocks')['listed_date'])

    def test_adapters_benchmark_actions_and_valuation(self):
        with patch.object(finmind,'fetch',return_value=[dict(date='2025-01-02',stock_id='TAIEX',price=50000)]):
            self.assertEqual(finmind.total_return_index('2025-01-01','2025-01-03')[0]['benchmark_id'],'TAIEX_TR')
        raw = dict(date='2025-01-02',stock_id='2330',before_price=100,after_price=90,
                   stock_or_cache_dividend='權息',stock_and_cache_dividend=10)
        with patch.object(finmind,'fetch',return_value=[raw]):
            row=finmind.action_history('TaiwanStockDividendResult','2330','2025-01-01','2025-01-03')[0]
            self.assertIsNone(row['cash_dividend']);self.assertIsNone(row['stock_dividend'])
            self.assertEqual(row['ratio'],.9);self.assertEqual(row['kind'],'dividend_mixed')
        with patch.object(finmind,'fetch',return_value=[dict(date='2025-01-02',stock_id='2330',PER=20,PBR=5,dividend_yield=1)]):
            self.assertEqual(finmind.valuation_history('2330','2025-01-01','2025-01-03')[0]['pe'],20)

    def test_resume_failure_atomic_checkpoint_and_lock(self):
        job=jobs.create_job('2025-01-02','2025-01-02',stock_ids=['2330'])
        jid=job['id']
        def fetch(unit, settings):
            if unit['dataset']=='calendar':
                return t.trading_calendar,[{'date':'2025-01-02'}],'done'
            raise RuntimeError('quota')
        with patch.object(jobs,'_fetch',side_effect=fetch):
            first=jobs.run(jid,1)
            self.assertEqual(first['status'],'paused')
            second=jobs.run(jid,1)
            self.assertEqual(second['status'],'failed')
        with self.engine.connect() as c:
            self.assertEqual(query_one(c,"SELECT attempts FROM backfill_tasks WHERE dataset='calendar'")['attempts'],1)
        with jobs.runner_lock():
            with self.assertRaises(ValueError): jobs.run(jid,1)
        with patch.object(jobs,'_fetch',return_value=(t.stocks,[{'stock_id':'2330','name':'T'}],'done')):
            resumed=jobs.run(jid,1)
        self.assertEqual(resumed['status'],'paused')
        with self.engine.connect() as c:
            self.assertEqual(query_one(c,"SELECT attempts FROM backfill_tasks WHERE dataset='delistings'")['attempts'],2)

    def test_transaction_rollback_retries_without_skipping(self):
        job=jobs.create_job('2025-01-02','2025-01-02',stock_ids=['2330'])
        # Invalid extra field fails ingestion, checkpoint cannot become done.
        with patch.object(jobs,'_fetch',return_value=(t.trading_calendar,[{'date':'2025-01-02','bad':1}],'done')):
            result=jobs.run(job['id'],1)
        self.assertEqual(result['status'],'failed')
        with self.engine.connect() as c:
            self.assertEqual(query_one(c,'SELECT COUNT(*) n FROM trading_calendar')['n'],0)
        with patch.object(jobs,'_fetch',return_value=(t.trading_calendar,[{'date':'2025-01-02'}],'done')):
            self.assertEqual(jobs.run(job['id'],1)['status'],'paused')

    def test_empty_market_fails_and_true_holiday_skips(self):
        unit=dict(dataset='quotes',target='twse',start_date='2025-01-02',end_date='2025-01-02')
        with self.engine.begin() as c: upsert_many(c,t.trading_calendar,[{'date':'2025-01-02'}])
        with patch.object(jobs.twse,'daily_all_by_date',return_value=[]):
            with self.assertRaises(ValueError): jobs._fetch(unit,{})
        unit['start_date']='2025-01-01'
        self.assertEqual(jobs._fetch(unit,{})[2],'skipped_closed')

    def test_incomplete_benchmark_not_success(self):
        with self.engine.begin() as c: upsert_many(c,t.trading_calendar,[{'date':'2025-01-02'}])
        with patch.object(finmind,'total_return_index',return_value=[]), \
             patch.object(jobs,'_official_market_state',return_value=('open',{'date':'2025-01-02','twse_rows':1000,'tpex_rows':0})):
            _, rows, state, metadata = jobs._fetch(
                dict(dataset='benchmark',target='',start_date='2025-01-01',end_date='2025-01-03'),{})
        self.assertEqual(rows,[])
        self.assertEqual(state,'partial')
        self.assertEqual(metadata['source_missing_open_dates'],['2025-01-02'])
        self.assertEqual(metadata['final_missing_dates'],['2025-01-02'])

    def test_benchmark_false_trading_date_is_reconciled_after_retry(self):
        dates = ['2026-07-09','2026-07-10','2026-07-13']
        with self.engine.begin() as c:
            upsert_many(c,t.trading_calendar,[{'date':date} for date in dates])
        source = {
            date: {'benchmark_id':'TAIEX_TR','date':date,'value':value,
                   'source':'FinMind:TaiwanStockTotalReturnIndex'}
            for date,value in [('2026-07-09',100),('2026-07-13',101)]
        }
        calls=[]
        def fetch(start,end):
            calls.append((start,end))
            return [row for date,row in source.items() if start <= date <= end]
        with patch.object(finmind,'total_return_index',side_effect=fetch), \
             patch.object(jobs,'_official_market_state',return_value=('closed',{
                 'date':'2026-07-10','twse_rows':0,'tpex_rows':0})):
            _, rows, state, metadata = jobs._fetch(
                dict(dataset='benchmark',target='',start_date='2026-07-01',end_date='2026-07-31'),{})
        self.assertEqual(state,'done')
        self.assertEqual([row['date'] for row in rows],['2026-07-09','2026-07-13'])
        self.assertEqual(metadata['expected_count_initial'],3)
        self.assertEqual(metadata['source_count_initial'],2)
        self.assertEqual(metadata['initial_missing_dates'],['2026-07-10'])
        self.assertEqual(metadata['calendar_false_positive_dates'],['2026-07-10'])
        self.assertEqual((metadata['expected_count_final'],metadata['source_count_final']),(2,2))
        self.assertEqual(metadata['final_missing_dates'],[])
        self.assertEqual(calls,[('2026-07-01','2026-07-31'),
                                ('2026-07-10','2026-07-11'),
                                ('2026-07-10','2026-07-11')])

    def test_failed_benchmark_checkpoint_resumes_without_replaying_done_tasks(self):
        jid=jobs.create_job('2026-07-10','2026-07-10',stock_ids=['2330'])['id']
        task_key='benchmark::2026-07-10:2026-07-10'
        with self.engine.begin() as c:
            upsert_many(c,t.trading_calendar,[{'date':'2026-07-10'}])
            c.exec_driver_sql("UPDATE backfill_tasks SET status='done',attempts=1 WHERE job_id=?",(jid,))
            c.exec_driver_sql("UPDATE backfill_tasks SET status='failed',attempts=1,error='old' WHERE job_id=? AND task_key=?",(jid,task_key))
            c.exec_driver_sql("UPDATE backfill_jobs SET status='failed',error='old' WHERE id=?",(jid,))
        metadata={'expected_count_initial':1,'source_count_initial':0,
                  'initial_missing_dates':['2026-07-10'],
                  'calendar_false_positive_dates':['2026-07-10'],
                  'calendar_missing_open_dates':[],'final_missing_dates':[],
                  'final_extra_dates':[]}
        with patch.object(jobs,'_fetch',return_value=(t.benchmark_series,[],'done',metadata)) as fetch:
            result=jobs.run(jid,1)
        self.assertEqual(result['status'],'completed')
        fetch.assert_called_once()
        with self.engine.connect() as c:
            task=query_one(c,'SELECT status,attempts,error FROM backfill_tasks WHERE job_id=:j AND task_key=:k',j=jid,k=task_key)
            self.assertEqual((task['status'],task['attempts']),('done',2))
            self.assertEqual(json.loads(task['error'])['calendar_false_positive_dates'],['2026-07-10'])
            self.assertEqual(query_one(c,"SELECT COUNT(*) n FROM trading_calendar WHERE date='2026-07-10'")['n'],0)
            self.assertEqual(query_one(c,"SELECT COUNT(*) n FROM backfill_tasks WHERE job_id=:j AND attempts!=1 AND task_key!=:k",j=jid,k=task_key)['n'],0)

    def test_api_validation_and_pause(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client=TestClient(app)  # No lifespan; isolated migrated engine above.
        self.assertEqual(client.post('/api/admin/backfill-for-backtest',json={'min_avg_amount':-1}).status_code,422)
        response=client.post('/api/admin/backfill-for-backtest',json={'start':'2025-01-02','end':'2025-01-03','stock_ids':['2330']})
        self.assertEqual(response.status_code,200)
        jid=response.json()['id']
        self.assertEqual(client.post(f'/api/admin/backfill-for-backtest/{jid}/resume',json={'max_units':0}).status_code,422)
        self.assertEqual(client.post(f'/api/admin/backfill-for-backtest/{jid}/pause').status_code,200)
        self.assertTrue(client.get(f'/api/admin/backfill-for-backtest/{jid}').json()['stop_requested'])
        self.assertEqual(client.get('/api/admin/backfill-for-backtest/missing').status_code,404)


    def test_warrant_exclusion_and_historical_stock_retention(self):
        with self.engine.begin() as c:
            rows = [dict(stock_id=s, stock_name=s, type='twse', industry_category=cat)
                    for s,cat in [('2330','半導體'),('030001','認購權證'),('006208','ETF')]]
            data.ingest_stock_info(c, rows)
            self.assertEqual({r['stock_id'] for r in query_all(c,'SELECT stock_id FROM stocks')}, {'2330','006208'})
            upsert_many(c,t.trading_calendar,[{'date':'2025-01-02'}])
        rows = [dict(stock_id=str(i),date='2025-01-02',close=10,amount=1e6) for i in range(1000,1501)]
        rows += [dict(stock_id='030001',date='2025-01-02'),dict(stock_id='006208',date='2025-01-02')]
        unit=dict(dataset='quotes',target='twse',start_date='2025-01-02',end_date='2025-01-02')
        with patch.object(jobs.twse,'daily_all_by_date',return_value=rows):
            _, result, _ = jobs._fetch(unit,{})
        self.assertEqual(len(result),502)
        self.assertIn('1000',{r['stock_id'] for r in result})
        self.assertNotIn('030001',{r['stock_id'] for r in result})

    def test_keyboard_interrupt_and_in_process_pause(self):
        jid=jobs.create_job('2025-01-02','2025-01-02',stock_ids=['2330'])['id']
        with patch.object(jobs,'_fetch',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):jobs.run(jid,1)
        self.assertEqual(jobs.status(jid)['status'],'interrupted')
        def fetch(unit, settings):
            jobs.pause(jid)
            return t.trading_calendar,[{'date':'2025-01-02'}],'done'
        with patch.object(jobs,'_fetch',side_effect=fetch):
            self.assertEqual(jobs.run(jid,100)['status'],'paused')
        with self.engine.connect() as c:
            self.assertEqual(query_one(c,"SELECT COUNT(*) n FROM backfill_tasks WHERE status='done'")['n'],1)

    def test_completed_job_resume_does_not_fetch_again(self):
        jid=jobs.create_job('2025-01-02','2025-01-02',stock_ids=['2330'])['id']
        def fetch(unit, settings):
            if unit['dataset']=='stock_info':
                return None,[dict(stock_id='2330',stock_name='T',type='twse')],'done'
            if unit['dataset']=='financials':
                return t.financials,[dict(stock_id='2330',quarter='2025Q1',eps=1)],'done'
            if unit['dataset']=='revenue':
                return t.month_revenue,[dict(stock_id='2330',ym='2025-01',revenue=1)],'done'
            if unit['dataset']=='valuations':
                return t.valuations,[dict(stock_id='2330',date='2025-01-02',pe=20)],'done'
            return t.trading_calendar,[], 'done'
        with patch.object(jobs,'_fetch',side_effect=fetch) as mock:
            self.assertEqual(jobs.run(jid,100)['status'],'completed')
            calls=mock.call_count
            self.assertEqual(jobs.run(jid,100)['status'],'completed')
            self.assertEqual(mock.call_count,calls)

    def test_cashflow_missing_prior_quarter_is_unknown(self):
        from app.services.deep_fetch import _decumulate_cashflow
        values={'2025Q2':{'op_cashflow':300},'2025Q3':{'op_cashflow':500}}
        _decumulate_cashflow(values)
        self.assertIsNone(values['2025Q2']['op_cashflow'])
        self.assertEqual(values['2025Q3']['op_cashflow'],200)

    def test_pause_between_enqueue_and_worker_is_honored(self):
        jid=jobs.create_job('20250102','20250102',stock_ids=['2330'])['id']
        self.assertEqual(jobs.status(jid)['config']['start'],'2025-01-02')
        jobs.prepare_resume(jid)
        jobs.pause(jid)
        with patch.object(jobs,'_fetch') as fetch:
            self.assertEqual(jobs.run(jid,1,reset_stop=False)['status'],'paused')
            fetch.assert_not_called()

    def test_financial_publication_boundary(self):
        with self.engine.begin() as c:
            upsert_many(c,t.financials,[dict(stock_id='A',quarter='2025Q1',eps=2,
                                            publish_date='20250520',publish_source='actual'),
                                       dict(stock_id='B',quarter='2025Q1',eps=3)])
            self.assertEqual(data.available_fundamentals(c,'financials','20250520'),[])
            self.assertEqual([r['stock_id'] for r in data.available_fundamentals(c,'financials','20250521')],['A'])

if __name__=='__main__': unittest.main()
