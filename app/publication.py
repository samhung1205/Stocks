"""Publication metadata only; no strategy panel or execution logic.

Date-only evidence is usable strictly AFTER that date (unknown intraday time).
Assumed deadlines cannot prove publication; strict readers exclude them.
"""
import datetime as dt
import json
import os


def revenue_deadline(ym: str) -> str:
    y, m = map(int, ym.split('-'))
    return dt.date(y + (m == 12), m % 12 + 1, 10).isoformat()


def financial_deadline(quarter: str) -> str:
    y, q = quarter.split('Q')
    # Q1 is SAME year. General-company nominal deadlines, not actual evidence.
    rules = json.loads(os.environ.get('STOCKS_FINANCIAL_DEADLINES',
                                     '{"1":"05-15","2":"08-14","3":"11-14","4":"03-31"}'))
    return dt.date.fromisoformat(f'{int(y) + (q == "4")}-{rules[q]}').isoformat()


def publication_fields(table: str, row: dict) -> dict:
    if row.get('publish_date') and row.get('publish_source'):
        return {**row, 'publish_date': dt.date.fromisoformat(row['publish_date']).isoformat()}
    period = row['ym'] if table == 'month_revenue' else row['quarter']
    # Per-company exceptions/holiday extensions: {"2330:2026Q1":"2026-05-18"}.
    overrides = json.loads(os.environ.get('STOCKS_PUBLICATION_OVERRIDES', '{}'))
    fallback = revenue_deadline(period) if table == 'month_revenue' else financial_deadline(period)
    date = overrides.get(f"{row['stock_id']}:{period}", fallback)
    date = dt.date.fromisoformat(date).isoformat()
    return {**row, 'publish_date': date, 'publish_source': 'assumed_deadline'}
