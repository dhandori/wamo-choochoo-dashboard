"""매크로 지수 두 줄 판정(주식시장 충격 / 신용·자금 스트레스)과 절대 기준 바닥 (2026-11-22)."""
import unittest
from datetime import date, timedelta

import wamo_macro as m


def row(key, level):
    return {'key': key, 'level': level}


class MacroAxesTests(unittest.TestCase):
    def test_quiet_credit_does_not_dilute_stock_shock(self):
        # 2026-11-20: 공포 지표는 위기인데 채권 지표가 조용해 한데 평균한 종합이 '경계'에 머물렀다
        rows = [row('vix', 1), row('vix_ratio', 1), row('fng', 1), row('sp_dd', 2),
                row('us_hy', 2), row('us_ig', 3), row('us_liq', 3), row('us_curve', 5)]
        ax = m.market_axes(rows, 'US')
        self.assertEqual(ax['stock']['level'], 1)
        self.assertEqual(ax['credit']['level'], 3)
        self.assertEqual(ax['level'], 1)          # 대표 단계는 나쁜 쪽

    def test_markets_are_separate(self):
        rows = [row('vix', 1), row('kr_aa', 4), row('kr_bbb', 4), row('vkospi', 5), row('ks_dd', 5)]
        self.assertEqual(m.market_axes(rows, 'KR')['stock']['level'], 5)
        self.assertEqual(m.market_axes(rows, 'KR')['credit']['level'], 4)
        self.assertEqual(m.market_axes(rows, 'US')['credit']['level'], None)

    def test_every_indicator_has_an_axis_and_market(self):
        for ind in m.INDICATORS:
            self.assertIn(m.axis_of(ind['key']), ('stock', 'credit'))
        self.assertEqual(m._market_of({'key': 'ks_dd'}), 'KR')
        self.assertEqual(m._market_of({'key': 'sp_dd'}), 'US')

    def test_half_steps_round_to_the_risky_side(self):
        self.assertEqual(m.composite([3, 4]), 3)   # 위험도 2.5 → 3
        self.assertEqual(m.composite([2, 3]), 2)   # 위험도 3.5 + 0.5 → 4

    def test_floor(self):
        self.assertEqual(m.apply_floor('vix', {'value': 31.0, 'level': 4})['level'], 2)
        self.assertEqual(m.apply_floor('vix', {'value': 41.0, 'level': 3})['level'], 1)
        self.assertEqual(m.apply_floor('vkospi', {'value': 53.2, 'level': 3})['floor'], 40)
        calm = m.apply_floor('vix', {'value': 18.0, 'level': 4})
        self.assertEqual((calm['level'], calm.get('floor')), (4, None))
        worse = m.apply_floor('vix', {'value': 31.0, 'level': 1})   # 이미 더 위험하면 그대로
        self.assertEqual((worse['level'], worse.get('floor')), (1, None))

    def test_drawdown(self):
        d0 = date(2026, 1, 1)
        px = {d0 + timedelta(days=i): v for i, v in enumerate([100, 120, 90, 120, 60])}
        dd = m.drawdown(px, window=3)
        self.assertEqual(list(dd.values()), [-25.0, 0.0, -50.0])


if __name__ == '__main__':
    unittest.main()
