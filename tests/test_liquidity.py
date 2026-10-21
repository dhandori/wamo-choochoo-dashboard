"""시장 거래대금(유동성): 4분면 판정, 상승·하락 비율, 유동성 경보, 셀링 클라이맥스, 업종 돈 흐름."""
import unittest
from datetime import date, timedelta

import wamo_liquidity as L


def sessions(n, end='2026-10-21'):
    d, out = date.fromisoformat(end), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def stock(sym, industry, closes, volumes):
    rows = [{'date': d, 'close': c, 'high': c * 1.01, 'low': c * 0.99, 'volume': v}
            for d, c, v in zip(sessions(len(closes)), closes, volumes)]
    return {'id': f'KR:{sym}', 'symbol': sym, 'name': sym, 'industry': industry, 'rows': rows,
            'closes': closes, 'marketCap': 1e12, 'rsPercentile': 50}


def universe(n=320, price_tail=0.0, vol_tail=1.0, tail=5):
    """n days of flat 100 / volume 1000; the last `tail` days drift by price_tail per day and vol_tail x volume."""
    out = []
    for k in range(12):
        closes = [100.0 + k] * n
        vols = [1000.0] * n
        for i in range(n - tail, n):
            closes[i] = closes[i - 1] * (1 + price_tail)
            vols[i] = 1000.0 * vol_tail
        out.append(stock(f'S{k}', '반도체' if k % 2 else '조선', closes, vols))
    return out


class Quadrants(unittest.TestCase):
    def judge(self, price_tail, vol_tail):
        u = universe(price_tail=price_tail, vol_tail=vol_tail)
        bench = [{'date': r['date'], 'close': r['close']} for r in u[0]['rows']]
        return L.analyze(u, bench)

    def test_price_up_flow_up_is_healthy(self):
        r = self.judge(0.01, 1.4)
        self.assertEqual(r['trend']['key'], 'HEALTHY'); self.assertEqual(r['score'], 1)

    def test_price_up_flow_down_is_thin(self):
        self.assertEqual(self.judge(0.01, 0.6)['trend']['key'], 'THIN')

    def test_price_down_flow_up_is_distribution(self):
        r = self.judge(-0.01, 1.4)
        self.assertEqual(r['trend']['key'], 'DISTRIBUTION'); self.assertEqual(r['score'], -1)

    def test_price_down_flow_down_is_exhaustion(self):
        self.assertEqual(self.judge(-0.01, 0.6)['trend']['key'], 'EXHAUSTION')

    def test_flat_market_has_no_direction(self):
        self.assertEqual(self.judge(0.0, 1.0)['trend']['key'], 'FLAT')


class Ratios(unittest.TestCase):
    def test_up_down_ratio_uses_turnover_of_winners_vs_losers(self):
        u = universe()
        for k, s in enumerate(u):              # even stocks fall, odd stocks rise on the last day
            s['rows'][-1]['close'] = s['closes'][-1] = 99.0 if k % 2 == 0 else 103.0
            s['rows'][-1]['volume'] = 3000.0 if k % 2 == 0 else 1000.0
        r = L.analyze(u)
        up = sum(s['rows'][-1]['close'] * s['rows'][-1]['volume'] for s in u if s['rows'][-1]['close'] > s['rows'][-2]['close'])
        down = sum(s['rows'][-1]['close'] * s['rows'][-1]['volume'] for s in u if s['rows'][-1]['close'] < s['rows'][-2]['close'])
        self.assertAlmostEqual(r['upDown']['today'], round(up / down, 2), 2)
        self.assertLess(r['upDown']['today'], 1)   # losers carried the money today

    def test_dry_liquidity_warns_and_flood_notes(self):
        u = universe(vol_tail=0.4, tail=20)
        self.assertEqual(L.analyze(u)['liquidity']['key'], 'DRY')
        u = universe(vol_tail=1.6, tail=20)
        self.assertEqual(L.analyze(u)['liquidity']['key'], 'FLOOD')

    def test_selling_climax_needs_big_drop_and_year_high_turnover(self):
        u = universe(n=320)
        for s in u:
            s['rows'][-3]['close'] = s['closes'][-3] = s['closes'][-4] * 0.95
            s['rows'][-3]['volume'] = 9000.0
            for i in (-2, -1):
                s['rows'][i]['close'] = s['closes'][i] = s['closes'][-3]
        bench = [{'date': r['date'], 'close': r['close']} for r in u[0]['rows']]
        r = L.analyze(u, bench)
        self.assertEqual(r['sellingClimax']['daysAgo'], 2)
        self.assertLess(r['sellingClimax']['changePct'], -3)
        u2 = universe(n=320)
        for s in u2:
            s['rows'][-3]['close'] = s['closes'][-3] = s['closes'][-4] * 0.95   # same drop, ordinary volume
        self.assertIsNone(L.analyze(u2, [{'date': r['date'], 'close': r['close']} for r in u2[0]['rows']])['sellingClimax'])

    def test_industry_share_change_sums_to_zero(self):
        u = universe()
        for s in u:
            if s['industry'] == '조선':
                for r in s['rows'][-5:]:
                    r['volume'] = 3000.0
        r = L.analyze(u)
        ind = {x['name']: x for x in r['industries']['inflow'] + r['industries']['outflow']}
        self.assertGreater(ind['조선']['shareChangePp'], 0)
        self.assertLess(ind['반도체']['shareChangePp'], 0)
        self.assertAlmostEqual(ind['조선']['shareChangePp'] + ind['반도체']['shareChangePp'], 0, 1)

    def test_too_short_history_is_unavailable(self):
        self.assertEqual(L.analyze(universe(n=30))['status'], 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()


def index_rows(n=300, price_tail=0.0, vol_tail=1.0, tail=5, drop_at=None, drop_vol=None):
    rows, c = [], 1000.0
    for i, d in enumerate(sessions(n)):
        v = 1e6
        if i >= n - tail:
            c *= 1 + price_tail
            v = 1e6 * vol_tail
        if drop_at is not None and i == n + drop_at:
            c *= 0.95
            v = drop_vol
        rows.append({'date': d, 'close': c, 'high': c * 1.01, 'low': c * 0.99, 'volume': v})
    return rows


class IndexVolume(unittest.TestCase):
    """시장 판정은 지수 공식 거래량(거래소 전체)으로 한다."""

    def test_each_index_is_judged_on_its_own_volume(self):
        u = universe()
        for s in u[:6]:
            s['board'] = 'KOSPI'
        for s in u[6:]:
            s['board'] = 'KOSDAQ'
        r = L.split(u, [('KOSPI', 'KOSPI', '^KS11', lambda s: s['board'] == 'KOSPI', index_rows(price_tail=0.01, vol_tail=1.5)),
                        ('KOSDAQ', 'KOSDAQ', '^KQ11', lambda s: s['board'] == 'KOSDAQ', index_rows(price_tail=-0.01, vol_tail=1.5))])
        g = {x['key']: x for x in r['groups']}
        self.assertEqual(g['KOSPI']['trend']['key'], 'HEALTHY')
        self.assertEqual(g['KOSDAQ']['trend']['key'], 'DISTRIBUTION')
        self.assertEqual(r['score'], -1)                      # the weaker market sets the weather score
        self.assertIn('KOSDAQ 분산', r['summary'])
        self.assertEqual(g['KOSDAQ']['members'], 6)
        self.assertIsNotNone(r['industries'])                 # money-flow industries still from all screened stocks

    def test_dry_index_volume_warns(self):
        g = L.index_flow(index_rows(vol_tail=0.5, tail=20), 'ALL', 'S&P 500', '^GSPC')
        self.assertEqual(g['liquidity']['key'], 'DRY')
        self.assertLess(g['series'][-1]['x50'], 1)             # chart is in multiples of the 50-day average

    def test_selling_climax_on_index_volume(self):
        g = L.index_flow(index_rows(drop_at=-2, drop_vol=5e6), 'KOSPI', 'KOSPI', '^KS11')
        self.assertEqual(g['sellingClimax']['daysAgo'], 1)
        self.assertIsNone(L.index_flow(index_rows(drop_at=-2, drop_vol=1e6), 'KOSPI', 'KOSPI', '^KS11')['sellingClimax'])

    def test_index_without_volume_is_skipped(self):
        rows = [dict(r, volume=None) for r in index_rows()]
        self.assertEqual(L.split(universe(), [('ALL', 'X', '^X', lambda s: True, rows)])['status'], 'UNAVAILABLE')
