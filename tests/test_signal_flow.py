import copy
import unittest
from datetime import datetime, timezone

from wamo_insights import build_insights, project
from wamo_runtime import calendar

NOW = datetime(2026, 10, 17, 3, tzinfo=timezone.utc)
DAY = '2026-10-16'


def stock(symbol='NVDA', market='US', asof=DAY, count=260):
    sessions = calendar(market).sessions
    end = sessions.get_loc(asof) + 1
    rows = [{'date': d.date().isoformat(), 'close': 80., 'high': 100., 'low': 70., 'volume': 100.}
            for d in sessions[end-count:end]]
    rows[-1]['close'] = 95.
    return {'ticker': symbol, 'date': asof, 'history': rows, 'rsPercentile': 85, 'stage2Core': True}


def build(s, previous=None, baseline=None, market='US'):
    return build_insights({market: {'meta': {'asOf': s['date']}, 'stocks': [s]}}, NOW, previous, baseline)


class SignalFlowTests(unittest.TestCase):
    def test_ma50_requires_contiguous_sessions_and_handles_equal_close(self):
        s = stock(count=55)
        row = project(s, 'US', DAY)
        self.assertTrue(row['aboveMa50'])
        self.assertFalse(row['aboveMa50Ago5'])
        s['history'][-1]['close'] = 80
        self.assertFalse(project(s, 'US', DAY)['aboveMa50'])
        self.assertIsNone(project(stock(count=49), 'US', DAY)['aboveMa50'])
        self.assertIsNone(project(stock(count=54), 'US', DAY)['aboveMa50Ago5'])
        s = stock(count=56)
        del s['history'][-10]
        self.assertIsNone(project(s, 'US', DAY)['aboveMa50'])
        self.assertIsNone(project(s, 'US', DAY)['aboveMa50Ago5'])

    def test_previous_actual_session_is_preserved_on_same_day_rebuild(self):
        old = stock(asof='2026-10-15'); old.update(rsPercentile=79, stage2Core=False)
        previous = build(old)
        first = build(stock(), previous)
        comp = first['markets']['US']['comparison']
        self.assertEqual(comp['asOf'], '2026-10-15')
        self.assertEqual(comp['expectedSession'], '2026-10-15')
        self.assertEqual(comp['stocks'][0]['rs'], 79)
        self.assertFalse(comp['stocks'][0]['stage2'])
        self.assertEqual(build(stock(), first)['markets']['US']['comparison'], comp)
        self.assertNotIn('comparison', previous['markets']['US']['comparison'])

    def test_missed_sessions_and_unknown_stocks_are_not_new_entries(self):
        previous = build(stock(asof='2026-10-14'))
        comp = build(stock(), previous)['markets']['US']['comparison']
        self.assertIsNone(comp['asOf'])
        self.assertEqual(comp['stocks'], [])
        previous = build(stock(symbol='AMD', asof='2026-10-15'))
        comp = build(stock(), previous)['markets']['US']['comparison']
        self.assertNotIn('US:NVDA', [s['id'] for s in comp['stocks']])

    def test_weekends_use_exchange_session_and_markets_are_independent(self):
        previous = build(stock(), market='US')
        monday = stock(asof='2026-10-19')
        future = datetime(2026, 10, 20, 3, tzinfo=timezone.utc)
        result = build_insights({'US': {'meta': {'asOf': monday['date']}, 'stocks': [monday]}}, future, previous)
        self.assertEqual(result['markets']['US']['comparison']['asOf'], DAY)
        self.assertIsNone(result['markets']['KR']['asOf'])
        kr = stock('005930', 'KR', asof='2026-10-12')
        result = build(kr, market='KR')['markets']['KR']
        self.assertEqual(result['comparison']['expectedSession'], '2026-10-08')

    def test_dated_seed_only_bootstraps_the_exact_previous_session(self):
        seed = build(stock(asof='2026-10-15'))
        first = build(stock(), baseline=seed)
        self.assertEqual(first['markets']['US']['comparison']['asOf'], '2026-10-15')
        wrong = copy.deepcopy(seed); wrong['markets']['US']['asOf'] = '2026-10-14'
        self.assertIsNone(build(stock(), baseline=wrong)['markets']['US']['comparison']['asOf'])
        bad = copy.deepcopy(seed); bad['markets']['US']['stocks'][0]['rs'] = 'bad'
        self.assertIsNone(build(stock(), baseline=bad)['markets']['US']['comparison']['stocks'][0]['rs'])

    def test_failed_refresh_retains_comparison_with_its_price_date(self):
        previous = build(stock(asof='2026-10-15'))
        current = build(stock(), previous)
        failed = build_insights({}, NOW, current)['markets']['US']
        self.assertEqual(failed['asOf'], DAY)
        self.assertEqual(failed['comparison'], current['markets']['US']['comparison'])
