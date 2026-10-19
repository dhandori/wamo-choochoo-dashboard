import unittest
import wamo_update_us_sec as us


class PinnedUniverseTests(unittest.TestCase):
    def test_pinned_name_below_cut_is_added_once(self):
        listed = [{'ticker': f'T{i}'} for i in range(5)] + [{'ticker': 'FLNC'}, {'ticker': 'ZZZ'}]
        out = us.select_price_candidates(listed, limit=3, pinned=('FLNC',))
        self.assertEqual([s['ticker'] for s in out], ['T0', 'T1', 'T2', 'FLNC'])

    def test_pinned_already_in_top_or_missing_changes_nothing(self):
        listed = [{'ticker': 'FLNC'}, {'ticker': 'A'}, {'ticker': 'B'}]
        self.assertEqual(len(us.select_price_candidates(listed, limit=2, pinned=('FLNC',))), 2)
        self.assertEqual(len(us.select_price_candidates(listed[1:], limit=5, pinned=('FLNC',))), 2)


if __name__ == '__main__':
    unittest.main()
