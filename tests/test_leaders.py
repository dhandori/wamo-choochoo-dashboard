"""Leading stocks / leading industries must come from causal price rules."""
import unittest
from datetime import date, timedelta

import wamo_leaders as L


def dates(n):
    d, out = date(2025, 1, 2), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DAYS = dates(300)


def stock(sid, industry, closes, volume=1e6, breakout_day=None):
    rows = []
    for k, (d, c) in enumerate(zip(DAYS, closes)):
        v = volume * (3 if breakout_day is not None and k == breakout_day else 1)
        rows.append({'date': d, 'close': c, 'high': c * 1.005, 'low': c * 0.995, 'volume': v})
    return {'id': f'US:{sid}', 'symbol': sid, 'name': sid, 'industry': industry, 'marketCap': 5e9,
            'rows': rows, 'closes': [r['close'] for r in rows]}


def rising(rate, start=100.0):
    return [start * (1 + rate) ** k for k in range(len(DAYS))]


def falling(rate, start=100.0):
    return [start * (1 - rate) ** k for k in range(len(DAYS))]


def flat(start=100.0):
    return [start + (k % 5) * 0.01 for k in range(len(DAYS))]


def universe():
    u = []
    # Strong group: steady new highs, one fresh volume breakout on the last day.
    for k in range(4):
        closes = rising(0.003 + k * 0.0004)
        u.append(stock(f'S{k}', 'Strong', closes, breakout_day=len(DAYS) - 1 if k == 3 else None))
    for k in range(4):
        u.append(stock(f'W{k}', 'Weak', falling(0.002 + k * 0.0003)))
    for k in range(4):
        u.append(stock(f'F{k}', 'Flat', flat(100 + k)))
    return u


BENCH = [{'date': d, 'close': 100.0 + k * 0.02, 'high': 0, 'low': 0, 'volume': 1} for k, d in enumerate(DAYS)]


class LeaderTests(unittest.TestCase):
    def test_strong_uptrend_is_a_leader_and_weak_is_not(self):
        out = L.analyze(universe(), BENCH, 'US')
        ids = {x['id'] for x in out['leaders']['items']}
        self.assertIn('US:S3', ids)
        self.assertFalse(any(i.startswith('US:W') or i.startswith('US:F') for i in ids))
        s0 = next(x for x in out['leaders']['items'] if x['id'] == 'US:S3')
        self.assertEqual(s0['breakoutDates'], [DAYS[-1]])
        self.assertTrue(all(s0['checks'].values()))

    def test_small_cap_fails_liquidity(self):
        u = universe()
        for s in u:
            s['marketCap'] = 5e8
        out = L.analyze(u, BENCH, 'US')
        self.assertEqual(out['leaders']['count'], 0)

    def test_strong_industry_ranks_first_and_leads(self):
        out = L.analyze(universe(), BENCH, 'US')
        ind = out['industries']
        self.assertEqual(ind['status'], 'OK')
        first = ind['items'][0]
        self.assertEqual(first['name'], 'Strong')
        self.assertEqual(first['rank'], 1)
        self.assertTrue(first['conditions']['rsTop'])
        self.assertEqual(first['newHighs'], 4)
        self.assertEqual(first['above50Pct'], 100.0)
        self.assertTrue(first['leading'])
        self.assertEqual(first['stage'], 'HOLD')
        self.assertTrue(first['rsLineHigh'])
        self.assertEqual(ind['items'][-1]['name'], 'Weak')
        self.assertIsNone(ind['items'][-1]['stage'])

    def test_emergence_counts_breakouts_after_the_ftd(self):
        ftd = [{'status': 'OK', 'phase': 'CONFIRMED', 'label': 'S&P 500',
                'ftd': {'date': DAYS[-3], 'late': False, 'qualityScore': 2}}]
        out = L.analyze(universe(), BENCH, 'US', ftd)
        em = out['emergence']
        self.assertEqual(em['ftd']['date'], DAYS[-3])
        self.assertEqual(em['count'], 1)
        self.assertEqual(em['status'], 'WATCHING')
        self.assertEqual(em['age'], 2)

    def test_no_open_ftd_reports_reference_count_only(self):
        out = L.analyze(universe(), BENCH, 'US', [{'status': 'OK', 'phase': 'OUTSIDE', 'ftd': None}])
        self.assertEqual(out['emergence']['status'], 'NO_FTD')
        self.assertIsNone(out['emergence']['ftd'])

    def test_no_future_information(self):
        u = universe()
        cut = [dict(s, rows=s['rows'][:-1], closes=s['closes'][:-1]) for s in u]
        out = L.analyze(cut, BENCH[:-1], 'US')
        s0 = next((x for x in out['leaders']['items'] if x['id'] == 'US:S3'), None)
        self.assertTrue(s0 is None or DAYS[-1] not in s0['breakoutDates'])

    def test_watch_checklist_scores_strong_high_and_weak_low(self):
        u = universe()
        for s in u:
            s['growth'] = {'ok': s['industry'] == 'Strong', 'text': 'x'}
        out = L.analyze(u, BENCH, 'US')
        by = {x['name']: x for x in out['industries']['map']}
        self.assertGreater(by['Strong']['watchScore'], by['Weak']['watchScore'])
        strong = next(x for x in out['industries']['items'] if x['name'] == 'Strong')
        for key in ('rsRising', 'nearHigh', 'together', 'growth', 'candidate'):
            self.assertTrue(strong['watch'][key], key)
        weak = next(x for x in out['industries']['items'] if x['name'] == 'Weak')
        self.assertFalse(weak['watch']['candidate'])
        self.assertFalse(weak['watch']['growth'])
        # Leading industries are not repeated in the waiting list.
        self.assertNotIn('Strong', [x['name'] for x in out['industries']['watchlist']])
        self.assertEqual(len(out['watchChecks']), 6)

    def test_phase_tells_wait_before_ftd(self):
        wait = L.analyze(universe(), BENCH, 'US', [{'status': 'OK', 'phase': 'RALLY', 'label': 'KOSPI'}])
        self.assertEqual(wait['phase']['mode'], 'WAIT')
        ftd = L.analyze(universe(), BENCH, 'US', [{'status': 'OK', 'phase': 'CONFIRMED', 'label': 'S&P 500',
                                                    'ftd': {'date': DAYS[-3]}}])
        self.assertEqual(ftd['phase']['mode'], 'FTD')

    def test_growth_rule(self):
        from wamo_trend import growth_of
        self.assertTrue(growth_of({'sales_yoy': 25}, 'KR')['ok'])
        self.assertFalse(growth_of({'sales_yoy': 5}, 'KR')['ok'])
        self.assertIsNone(growth_of({}, 'KR')['ok'])
        self.assertTrue(growth_of({'canSlim': {'items': {'C': {'status': 'PASS'}}}}, 'US')['ok'])
        self.assertTrue(growth_of({'sepaFundamental': {'epsPass': True}}, 'US')['ok'])


if __name__ == '__main__':
    unittest.main()
