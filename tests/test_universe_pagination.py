import json
import unittest
from wamo_market_data import fetch_naver_universe

def page(codes, number=1):
    return json.dumps(dict(stockListCategoryType='KOSDAQ', totalCount=101, page=number, pageSize=100,
        stocks=[dict(itemCode=f'{i:06d}', stockName=f'기업{i}', sosok='1', marketValueRaw='1234567890000') for i in codes]))

class PaginationTests(unittest.TestCase):
    def test_rank_movement_recovers_all_identifiers_without_duplicates(self):
        responses = iter([page(range(100)), page([99], 2), page(list(range(99))+[100]), page([99], 2)])
        rows = fetch_naver_universe(lambda *a, **k: next(responses), 1, '.KQ', 'KOSDAQ')
        self.assertEqual(len(rows), 101)
        self.assertEqual({r['stock_code'] for r in rows}, {f'{i:06d}' for i in range(101)})

    def test_persistent_gap_fails_after_two_complete_sweeps(self):
        responses = iter([page(range(100)), page([99], 2)] * 2)
        with self.assertRaisesRegex(RuntimeError, '고유 종목.*100/101'):
            fetch_naver_universe(lambda *a, **k: next(responses), 1, '.KQ', 'KOSDAQ')
        self.assertEqual(list(responses), [])
