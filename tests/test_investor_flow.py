"""외국인·기관 추정 수급: 기간 합산 규칙과 레이더 스냅샷 통과를 검증한다."""
import unittest
from unittest.mock import patch

import wamo_update_business_dart as dart
from wamo_insights import investor_flow


def rows(count, close=100.0, foreign=10.0, institution=-5.0):
    out = []
    for i in range(count):
        month, day = 9 + i // 28, (i % 28) + 1
        out.append({'date': f'2026-{month:02d}-{day:02d}', 'close': close,
                    'foreign_shares': foreign, 'institution_shares': institution})
    return out[::-1]  # 실제 수집기처럼 최신일이 먼저 온다


class NaverFlowSumTests(unittest.TestCase):
    def test_period_sum_requires_full_window(self):
        # 30거래일치만 있으면 5·20일 합계는 만들고 60일 합계는 만들지 않는다.
        with patch.object(dart, '_naver_flow_rows', return_value=rows(30)):
            out = dart._naver_flow_one('005930')
        self.assertEqual(out['foreign_netbuy_5d'], 5 * 10.0 * 100.0)
        self.assertEqual(out['foreign_netbuy_20d'], 20 * 10.0 * 100.0)
        self.assertNotIn('foreign_netbuy_60d', out)
        self.assertEqual(out['institution_netbuy_20d'], 20 * -5.0 * 100.0)
        self.assertEqual(out['flow_sessions'], 30)
        self.assertEqual(out['flow_asof'], rows(30)[0]['date'])
        self.assertTrue(out['flow_is_estimate'])

    def test_too_few_sessions_returns_nothing(self):
        with patch.object(dart, '_naver_flow_rows', return_value=rows(10)):
            self.assertEqual(dart._naver_flow_one('005930'), {})

    def test_mostly_missing_values_do_not_fabricate_a_sum(self):
        data = rows(30)
        for row in data[:18]:  # 20일 창에서 18일이 비면 80% 기준 미달
            row['foreign_shares'] = None
        with patch.object(dart, '_naver_flow_rows', return_value=data):
            out = dart._naver_flow_one('005930')
        self.assertNotIn('foreign_netbuy_20d', out)
        self.assertIn('institution_netbuy_20d', out)


class InsightsFlowTests(unittest.TestCase):
    def test_passthrough_rounds_and_keeps_only_verified_numbers(self):
        stock = {
            'foreign_netbuy_5d': 1234.6, 'foreign_netbuy_20d': 'oops',
            'institution_netbuy_20d': -200.4, 'institution_netbuy_20d_pct_mcap': 0.12345,
            'flow_asof': '2026-10-23', 'flow_sessions': 30,
        }
        flow = investor_flow(stock)
        self.assertEqual(flow['foreignNet5d'], 1235)
        self.assertNotIn('foreignNet20d', flow)
        self.assertEqual(flow['instNet20d'], -200)
        self.assertEqual(flow['instPct20d'], 0.123)
        self.assertEqual(flow['asOf'], '2026-10-23')
        self.assertEqual(flow['sessions'], 30)
        self.assertTrue(flow['estimate'])

    def test_no_flow_fields_means_no_flow_block(self):
        self.assertIsNone(investor_flow({'name': 'X', 'close': 10}))


if __name__ == '__main__':
    unittest.main()
