"""Sparse OHLC regression: real SQLite reads, no personal DB or network."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from app.db import upsert_many
from app.db import tables as t
from app.db.migrations import migrate
from app.services import stock_view


class TechnicalSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'stock.db'))
        migrate(self.engine)
        self.patch = patch.object(stock_view, 'engine', self.engine)
        self.patch.start()
        with self.engine.begin() as conn:
            upsert_many(conn, t.stocks, [{'stock_id': '2330', 'name': '展示公司'}])

    def tearDown(self):
        self.patch.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def quotes(self, rows):
        with self.engine.begin() as conn:
            upsert_many(conn, t.daily_quotes, [{'stock_id': '2330', **r} for r in rows])

    def test_no_close_data_has_no_technical_evidence(self):
        self.quotes([{'date': '2026-10-01', 'high': 110, 'low': 90}])
        self.assertEqual(stock_view.technical_summary('2330'), {})

    def test_close_only_keeps_price_but_unknown_extremes_and_ma(self):
        self.quotes([{'date': '2026-10-01', 'close': 100}])
        summary = stock_view.technical_summary('2330')
        self.assertEqual(summary['close'], 100)
        for key in ('high_52w', 'low_52w', 'off_high_pct', 'ma60', 'above_ma60', 'above_ma240'):
            self.assertIsNone(summary[key], key)

    def test_partial_extremes_use_only_observed_values(self):
        self.quotes([
            {'date': '2026-10-01', 'close': 100, 'high': 110},
            {'date': '2026-10-02', 'close': 99, 'low': 95},
            {'date': '2026-10-03', 'close': 100},
        ])
        summary = stock_view.technical_summary('2330')
        self.assertEqual(summary['high_52w'], 110)
        self.assertEqual(summary['low_52w'], 95)
        self.assertEqual(summary['off_high_pct'], -9.1)

    def test_complete_prices_keep_existing_ma_and_extremes(self):
        import datetime as dt
        self.quotes([{'date': (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(),
                      'open': 99 + i, 'close': 100 + i, 'high': 101 + i,
                      'low': 98 + i, 'volume': 10000} for i in range(60)])
        summary = stock_view.technical_summary('2330')
        self.assertEqual(summary['ma20'], 149.5)
        self.assertEqual(summary['ma60'], 129.5)
        self.assertTrue(summary['above_ma60'])
        self.assertIsNone(summary['above_ma240'])
        self.assertEqual(summary['high_52w'], 160)
        self.assertEqual(summary['low_52w'], 98)
        self.assertEqual(summary['vol_ratio'], 1)
