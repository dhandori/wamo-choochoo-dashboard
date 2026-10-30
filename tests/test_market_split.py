"""시장 구분(2026-10-30): KOSDAQ 종목은 KOSDAQ 지수와 비교하고, 날씨는 지수마다 따로 매긴다."""
import unittest
from datetime import date, timedelta

import wamo_trend as trend
from wamo_insights import board_of


def rows(n=260, start=100.0, step=0.2, end='2026-10-29'):
    last = date.fromisoformat(end)
    days = [last - timedelta(days=n - 1 - i) for i in range(n)]
    out, c = [], start
    for d in days:
        c += step
        out.append({'date': d.isoformat(), 'close': c, 'high': c * 1.01, 'low': c * 0.99, 'volume': 1000.0})
    return out


def stock(sid, board, step):
    r = rows(step=step)
    return {'id': sid, 'symbol': sid, 'board': board, 'rows': r, 'closes': [x['close'] for x in r]}


class BoardBenchTests(unittest.TestCase):
    def test_kosdaq_stocks_get_the_kosdaq_index_only_in_korea(self):
        u = [stock('A', 'KOSPI', .2), stock('B', 'KOSDAQ', .2)]
        b2 = rows(step=.1)
        self.assertEqual(trend.assign_board_bench(u, 'KR', b2, 'KOSDAQ', '2026-10-29'), 1)
        self.assertNotIn('benchRows', u[0])
        self.assertIs(u[1]['benchRows'], b2)
        self.assertEqual(u[1]['benchLabel'], 'KOSDAQ')
        # 미국은 모든 종목이 S&P 500 기준(나스닥 상장사도 S&P 500 구성 종목이라서).
        us = [stock('C', 'NASDAQ', .2)]
        self.assertEqual(trend.assign_board_bench(us, 'US', b2, '나스닥', '2026-10-29'), 0)
        self.assertNotIn('benchRows', us[0])

    def test_stale_or_short_second_index_is_not_used(self):
        u = [stock('B', 'KOSDAQ', .2)]
        self.assertEqual(trend.assign_board_bench(u, 'KR', rows(n=150), 'KOSDAQ', '2026-10-29'), 0)
        self.assertEqual(trend.assign_board_bench(u, 'KR', rows(end='2026-10-28'), 'KOSDAQ', '2026-10-29'), 0)
        self.assertNotIn('benchRows', u[0])

    def test_chart_payload_marks_second_index_stocks(self):
        u = [stock('A', 'KOSPI', .2), stock('B', 'KOSDAQ', .2)]
        bench, b2 = rows(step=.3), rows(step=.1)
        trend.assign_board_bench(u, 'KR', b2, 'KOSDAQ', '2026-10-29')
        out = trend.chart_payload(u, bench, '2026-10-29', 'KR', b2)
        self.assertNotIn('b', out['stocks']['A'])
        self.assertEqual(out['stocks']['B']['b'], 2)
        self.assertEqual(len(out['bench2']), len(out['dates']))
        # 보조 지수를 쓰는 종목이 없으면 bench2 를 싣지 않는다.
        self.assertNotIn('bench2', trend.chart_payload(u[:1], bench, '2026-10-29', 'KR', b2))


class WeatherByIndexTests(unittest.TestCase):
    def test_each_index_gets_its_own_verdict_and_its_own_stocks(self):
        u = [stock('A', 'KOSPI', .2), stock('B', 'KOSPI', .2), stock('C', 'KOSDAQ', -.2)]
        idx = {**trend.index_trend(rows(step=.3)), 'label': 'KOSPI', 'symbol': '^KS11'}
        sec = {**trend.index_trend(rows(start=300, step=-.5)), 'label': 'KOSDAQ', 'symbol': '^KQ11'}
        out = trend.weather_by_index('KR', u, idx, {'status': 'UNAVAILABLE'}, sec, None, False)
        self.assertEqual([x['indexLabel'] for x in out], ['KOSPI', 'KOSDAQ'])
        self.assertEqual([x['universe'] for x in out], [2, 1])
        self.assertGreater(out[0]['score'], out[1]['score'])
        breadth = {x['indexLabel']: next(f['text'] for f in x['factors'] if f['key'] == 'breadth') for x in out}
        self.assertIn('KOSPI 종목 중', breadth['KOSPI'])
        self.assertIn('KOSDAQ 종목 중', breadth['KOSDAQ'])

    def test_no_split_without_a_second_index_or_for_other_markets(self):
        u = [stock('A', 'KOSPI', .2)]
        idx = {**trend.index_trend(rows()), 'label': 'KOSPI'}
        self.assertEqual(trend.weather_by_index('KR', u, idx, {}, None, None, False), [])
        self.assertEqual(trend.weather_by_index('JP', u, idx, {}, idx, None, False), [])


class BoardLabelTests(unittest.TestCase):
    def test_board_comes_from_listing_market_or_ticker_suffix(self):
        self.assertEqual(board_of({'krx_market': 'KOSDAQ'}, 'KR'), 'KOSDAQ')
        self.assertEqual(board_of({'ticker': '005930.KS'}, 'KR'), 'KOSPI')
        self.assertEqual(board_of({'ticker': '053800.KQ'}, 'KR'), 'KOSDAQ')
        self.assertEqual(board_of({'exchange': 'NYSE AMERICAN'}, 'US'), 'NYSE American')
        self.assertEqual(board_of({'exchange': 'NASDAQ'}, 'US'), 'NASDAQ')
        self.assertIsNone(board_of({'ticker': '7203.T'}, 'JP'))


if __name__ == '__main__':
    unittest.main()
