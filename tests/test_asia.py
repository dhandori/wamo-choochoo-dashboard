"""Japan/Taiwan Lite engine: universe parsing, same RS/Stage 2 math, safe failure."""
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import wamo_asia as asia

NOW = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)   # 17:00 KST Tuesday
SESSION = '2026-10-20'


def weekdays(n, end=SESSION):
    d, out = date.fromisoformat(end), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def rows_for(trend, n=300, base=100.0):
    out = []
    for i, d in enumerate(weekdays(n)):
        c = base * (1 + trend) ** i
        out.append({'date': d, 'close': c, 'high': c * 1.01, 'low': c * 0.99, 'volume': 1e6 + i})
    return out


def items(market, n):
    suffix = '.T' if market == 'JP' else '.TW'
    return [{'code': str(7000 + i) if market == 'JP' else str(2300 + i), 'ticker': (str(7000 + i) if market == 'JP' else str(2300 + i)) + suffix,
             'name': f'종목{i}', 'nameLocal': f'Local{i}', 'industry': '전기기기' if i % 2 else '반도체',
             'shares': 1e9, 'exchange': 'TSE' if market == 'JP' else 'TWSE'} for i in range(n)]


def fake_fetch(ticker):
    i = int(ticker.split('.')[0][-2:])
    return rows_for(0.0004 * (i % 9) - 0.001)


def patched():
    return [mock.patch('wamo_runtime.expected_session', lambda m, now=None: SESSION),
            mock.patch('wamo_insights.expected_session', lambda m, now=None: SESSION),
            mock.patch('wamo_insights.next_slot', lambda m, now: NOW + timedelta(days=1)),
            mock.patch('wamo_insights.recent_sessions', lambda m, a: tuple(weekdays(55)))]


class UniverseParsing(unittest.TestCase):
    def test_jpx_xlsx_keeps_topix500_domestic_only(self):
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(['日付', 'コード', '銘柄名', '市場・商品区分', '33業種コード', '33業種区分', '17業種コード', '17業種区分', '規模コード', '規模区分'])
        ws.append([20260930, 7203, 'トヨタ自動車', 'プライム（内国株式）', 3700, '輸送用機器', 6, '自動車', 1, 'TOPIX Core30'])
        ws.append([20260930, '130A', '新規', 'グロース（内国株式）', 5250, '情報・通信業', 10, '', '-', '-'])
        ws.append([20260930, 1001, 'ETF', 'ETF・ETN', '-', '-', '-', '-', '-', '-'])
        buf = io.BytesIO(); wb.save(buf)
        rows = asia.read_jpx(buf.getvalue())
        self.assertEqual([r['code'] for r in rows], ['7203', '130A', '1001'])
        self.assertEqual(rows[0]['size'], 'TOPIX Core30')
        self.assertEqual(asia.JP33[rows[0]['sector33']], '수송기기(자동차·부품)')

    def test_taiwan_market_cap_from_official_fields(self):
        info = [{'公司代號': '2330', '公司簡稱': '台積電', '英文簡稱': 'TSMC', '產業別': '24',
                 '已發行普通股數或TDR原股發行股數': '25932370067', '實收資本額': '259323700670'},
                {'公司代號': '9103', '公司簡稱': '美德醫療-DR', '英文簡稱': '', '產業別': '91', '已發行普通股數或TDR原股發行股數': '1'}]
        quotes = [{'Code': '2330', 'ClosingPrice': '2420.00'}, {'Code': '9103', 'ClosingPrice': '1.0'}]
        rows = asia.tw_rows(info, quotes, 'TWSE', '.TW')
        tsmc = rows[0]
        self.assertEqual(tsmc['ticker'], '2330.TW')
        self.assertEqual(tsmc['name'], 'TSMC (台積電)')
        self.assertEqual(tsmc['industry'], '반도체')
        self.assertAlmostEqual(tsmc['cap'], 25932370067 * 2420.0)

    def test_taiwan_shares_fall_back_to_paid_in_capital(self):
        rows = asia.tw_rows([{'公司代號': '6488', '公司簡稱': '環球晶', '產業別': '24', '實收資本額': '4781000000'}],
                            [{'SecuritiesCompanyCode': '6488', 'Close': '400'}], 'TPEx', '.TWO')
        self.assertEqual(rows[0]['shares'], 478100000)
        self.assertEqual(rows[0]['ticker'], '6488.TWO')

    def test_universe_is_rebuilt_weekly_and_old_list_survives_a_failed_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            ok = {'JP': lambda: (calls.append(1) or items('JP', 3), {'count': 3})}
            asia.load_universe('JP', root, date(2026, 10, 1), ok)
            asia.load_universe('JP', root, date(2026, 10, 5), ok)
            self.assertEqual(len(calls), 1)                         # cached within 7 days
            bad = {'JP': lambda: (_ for _ in ()).throw(RuntimeError('JPX down'))}
            got, info, rebuilt = asia.load_universe('JP', root, date(2026, 10, 20), bad)
            self.assertEqual(len(got), 3)
            self.assertIn('JPX down', info['warning'])
            with self.assertRaises(RuntimeError):
                asia.load_universe('JP', root, date(2026, 12, 20), bad)  # too old


class EngineRun(unittest.TestCase):
    def run_engine(self, root, fetch=fake_fetch, force=False):
        builders = {'JP': lambda: (items('JP', 40), {'count': 40}), 'TW': lambda: (items('TW', 40), {'count': 40})}
        ctx = patched()
        for c in ctx:
            c.start()
        try:
            return asia.run(('JP', 'TW'), root, NOW, force, fetch, lambda sym: rows_for(0.001), builders)
        finally:
            for c in ctx:
                c.stop()

    def test_full_run_builds_radar_blocks_charts_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names, status, failed = self.run_engine(root)
            self.assertEqual(failed, [])
            stored = json.loads((root / asia.MARKETS_FILE).read_text())
            for market in ('JP', 'TW'):
                block = stored['markets'][market]
                self.assertEqual(block['status'], 'CURRENT')
                self.assertEqual(block['asOf'], SESSION)
                self.assertEqual(len(block['stocks']), 40)
                s = block['stocks'][0]
                self.assertEqual(s['market'], market)
                self.assertIsNotNone(s['rs'])
                self.assertAlmostEqual(s['marketCap'], 1e9 * s['close'])
                self.assertIsNone(s['detailUrl'])
                chart = json.loads((root / f'wamo_chart_{market}.json').read_text())
                self.assertIn(s['symbol'], chart['stocks'])
                self.assertEqual(status[market]['status'], 'PASS')
            self.assertTrue(stored['markets']['JP']['stocks'][1]['naverUrl'].endswith('/7001.T/total'))
            self.assertIsNone(stored['markets']['TW']['stocks'][0]['naverUrl'])
            rs = sorted(s['rs'] for s in stored['markets']['JP']['stocks'])
            self.assertLess(rs[0], 20); self.assertGreater(rs[-1], 80)   # percentile within the market
            self.assertIn('wamo_chart_JP.json', names)

    def test_second_run_same_session_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_engine(root)
            calls = []
            names, _, _ = self.run_engine(root, fetch=lambda t: calls.append(t) or fake_fetch(t))
            self.assertEqual(calls, [])
            self.assertEqual(names, [])

    def test_too_many_price_failures_keep_previous_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_engine(root)
            before = json.loads((root / asia.MARKETS_FILE).read_text())['markets']['JP']

            def flaky(t):
                if int(t.split('.')[0][-2:]) % 4 == 0:
                    raise RuntimeError('429')
                return fake_fetch(t)
            _, status, failed = self.run_engine(root, fetch=flaky, force=True)
            self.assertEqual(sorted(failed), ['JP', 'TW'])
            self.assertEqual(status['JP']['status'], 'FAILED')
            after = json.loads((root / asia.MARKETS_FILE).read_text())['markets']['JP']
            self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
