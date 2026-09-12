import json
import unittest
from unittest.mock import patch

import wamo_update_business_dart as core


def stock(i, **fields):
    return dict(itemCode=f'{i:06d}', stockName=f'기업{i}',
                marketValueRaw='1234567890000', sosok='0', **fields)


def page(stocks, number=1, total=None):
    return json.dumps(dict(stockListCategoryType='KOSPI', stocks=stocks,
                          totalCount=len(stocks) if total is None else total,
                          page=number, pageSize=100))


class UniverseTests(unittest.TestCase):
    def fetch(self, responses):
        with patch.object(core, 'http_text', side_effect=responses):
            return core.fetch_market_summary(0, '.KS', 'KOSPI')

    def test_json_source_preserves_won_units_and_existing_contract(self):
        rows = self.fetch([page([stock(i) for i in range(100)])])
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0], dict(ticker='000000.KS', stock_code='000000',
                         name='기업0', market='KOREA', krx_market='KOSPI',
                         market_cap_krw=1234567890000))

    def test_all_pages_collected_and_preferred_shares_still_excluded(self):
        first = [stock(i) for i in range(100)]
        extra = stock(100)
        extra.update(itemCode='0126Z0', stockName='새기업')
        preferred = stock(101)
        preferred['stockName'] = '기업우'
        rows = self.fetch([page(first, total=102), page([extra, preferred], 2, 102)])
        self.assertEqual(len(rows), 101)
        self.assertEqual(rows[-1]['ticker'], '0126Z0.KS')

    def test_incomplete_or_duplicate_pages_fail_closed(self):
        first = [stock(i) for i in range(100)]
        for second in ([], [stock(0)]):
            with self.subTest(second=second), self.assertRaises(RuntimeError):
                self.fetch([page(first, total=101), page(second, 2, 101)])

    def test_wrong_market_or_invalid_cap_fails_closed(self):
        for change in ({'sosok': '1'}, {'marketValueRaw': 'NaN'}, {'marketValueRaw': None}):
            stocks = [stock(i) for i in range(100)]
            stocks[0].update(change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.fetch([page(stocks)])

    def test_kosdaq_market_and_suffix_are_preserved(self):
        stocks = [stock(i) for i in range(100)]
        for item in stocks:
            item['sosok'] = '1'
        response = json.loads(page(stocks))
        response['stockListCategoryType'] = 'KOSDAQ'
        with patch.object(core, 'http_text', return_value=json.dumps(response)):
            rows = core.fetch_market_summary(1, '.KQ', 'KOSDAQ')
        self.assertEqual(rows[0]['ticker'], '000000.KQ')
        self.assertEqual(rows[0]['krx_market'], 'KOSDAQ')

    def test_schema_drift_produces_controlled_failure(self):
        for response in ('[]', page([None] * 100)):
            with self.subTest(response=response[:15]), self.assertRaises(RuntimeError):
                self.fetch([response])

    def test_changed_total_or_repeated_page_is_rejected(self):
        first = page([stock(i) for i in range(100)], total=101)
        for second in (page([stock(100)], 1, 101), page([stock(100)], 2, 102)):
            with self.subTest(second=second), self.assertRaises(RuntimeError):
                self.fetch([first, second])


if __name__ == '__main__':
    unittest.main()

