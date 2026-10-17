import unittest
from unittest.mock import patch
from tests.test_price_sources import history
import wamo_update_business_dart as core


class CurrentHistoryTests(unittest.TestCase):
    def test_corrupt_current_primary_uses_independent_valid_history(self):
        bad=history()
        bad[100]['high']=90.
        good=history()
        with patch.object(core,'fetch_yahoo_history',return_value=(bad,'yahoo')),patch.object(core,'fetch_naver_history',return_value=good):
            rows,source,_=core.fetch_current_kr_history('005930.KS','005930','2026-10-07')
        self.assertEqual(source,'NAVER Finance')
        self.assertEqual(rows,good)

    def test_both_providers_with_one_bad_old_bar_use_repaired_history(self):
        bad_y, bad_n = history(), history()
        bad_y[100]['high'] = 90.
        bad_n[50]['low'] = 0.
        with patch.object(core,'fetch_yahoo_history',return_value=(bad_y,'yahoo')),patch.object(core,'fetch_naver_history',return_value=bad_n):
            rows,source,_=core.fetch_current_kr_history('012450.KS','012450','2026-10-07')
        self.assertEqual(source,'Yahoo Finance')
        self.assertEqual(rows[-1]['date'],'2026-10-07')
        self.assertGreaterEqual(rows[100]['high'], rows[100]['close'])

    def test_bad_latest_bar_is_never_repaired(self):
        bad_y, bad_n = history(), history()
        bad_y[-1]['high'] = 90.
        bad_n[-1]['low'] = 0.
        with patch.object(core,'fetch_yahoo_history',return_value=(bad_y,'yahoo')),patch.object(core,'fetch_naver_history',return_value=bad_n):
            with self.assertRaises(RuntimeError):
                core.fetch_current_kr_history('012450.KS','012450','2026-10-07')

    def test_recovery_excludes_next_intraday_bar(self):
        rows = history('2026-10-08')
        with patch.object(core, 'fetch_yahoo_history', return_value=(rows,'yahoo')):
            result, _, _ = core.fetch_current_kr_history('005930.KS','005930','2026-10-07')
        self.assertEqual(result[-1]['date'], '2026-10-07')

    def test_stale_success_falls_back_to_current_naver(self):
        stale = history('2026-09-04')
        current = history('2026-09-07')
        with patch.object(core, 'fetch_yahoo_history', return_value=(stale, 'yahoo')), patch.object(core, 'fetch_naver_history', return_value=current):
            rows, source, host = core.fetch_current_kr_history('005930.KS', '005930', '2026-09-07')
        self.assertEqual(rows, current)
        self.assertEqual(source, 'NAVER Finance')

    def test_both_stale_fail_instead_of_marking_live(self):
        stale = [{'date': '2026-09-04'}]
        with patch.object(core, 'fetch_yahoo_history', return_value=(stale, 'yahoo')), patch.object(core, 'fetch_naver_history', return_value=stale):
            with self.assertRaisesRegex(RuntimeError, '기대 2026-09-07'):
                core.fetch_current_kr_history('005930.KS', '005930', '2026-09-07')

    def test_current_primary_does_not_duplicate_queries(self):
        current = history('2026-09-07')
        with patch.object(core, 'fetch_yahoo_history', return_value=(current, 'yahoo')), patch.object(core, 'fetch_naver_history') as naver:
            self.assertEqual(core.fetch_current_kr_history('005930.KS', '005930', '2026-09-07')[0], current)
            naver.assert_not_called()
