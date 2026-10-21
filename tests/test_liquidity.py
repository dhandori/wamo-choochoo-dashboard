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
