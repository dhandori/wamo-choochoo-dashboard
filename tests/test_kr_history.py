import unittest
from unittest.mock import patch
import wamo_update_business_dart as core


class CurrentHistoryTests(unittest.TestCase):
    def test_stale_success_falls_back_to_current_naver(self):
        stale = [{'date': '2026-09-04'}]
        current = [{'date': '2026-09-07'}]
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
        current = [{'date': '2026-09-07'}]
        with patch.object(core, 'fetch_yahoo_history', return_value=(current, 'yahoo')), patch.object(core, 'fetch_naver_history') as naver:
            self.assertEqual(core.fetch_current_kr_history('005930.KS', '005930', '2026-09-07')[0], current)
            naver.assert_not_called()
