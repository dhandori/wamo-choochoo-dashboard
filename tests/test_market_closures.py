import unittest
from datetime import datetime, timezone

import wamo_runtime as rt


def closed(market, when, ahead=10):
    return {x['date']: x for x in rt.market_closures(market, when, ahead)['closed']}


class MarketClosureTests(unittest.TestCase):
    def test_japan_culture_day_has_korean_name_and_next_open(self):
        c = closed('JP', datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(c['2026-11-03']['name'], '문화의 날')
        self.assertEqual(c['2026-11-03']['nextOpen'], '2026-11-04')
        self.assertNotIn('2026-11-04', c)

    def test_weekend_and_long_holiday_point_to_the_same_reopen_day(self):
        c = closed('KR', datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertEqual(c['2026-09-25']['name'], '추석')
        self.assertEqual(c['2026-09-24']['nextOpen'], '2026-09-28')
        self.assertEqual(c['2026-09-27']['name'], '주말')

    def test_market_local_date_decides_today(self):
        # 11/2 16:00 UTC 는 도쿄로 11/3 01:00 이다. 어제(11/2 거래일)는 목록에 없다.
        c = closed('JP', datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc))
        self.assertNotIn('2026-11-02', c)
        self.assertEqual(rt.market_closures('US', datetime(2026, 11, 2, tzinfo=timezone.utc))['timeZone'], 'America/New_York')

    def test_unknown_holiday_name_falls_back(self):
        self.assertEqual(rt.holiday_ko('Something New'), '휴장일')
        self.assertEqual(rt.holiday_ko('Marine Day (2022 onwards)'), '바다의 날')


if __name__ == '__main__':
    unittest.main()
