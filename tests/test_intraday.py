import os
import unittest
from unittest.mock import patch
import wamo_update_business_dart as core
import wamo_update_us_sec as us


class IntradayTests(unittest.TestCase):

    def test_kr_price_mode_preserves_fundamentals_dates_and_sector_without_network(self):
        old = {'ticker': '005930.KS', 'dartStatus': 'LIVE', 'dartFetchedAt': '2026-09-07',
               'sales_cur': 123, 'financialHealth': {'status': 'PASS'},
               'detailSector': '반도체', 'sector': '반도체',
               'businessProfile': {'summary': '기존 사업설명'}, 'businessModelReportDate': '20260331'}
        raw = [{'ticker': '005930.KS', 'sector': '전기전자', 'close': 999},
               {'ticker': 'NEW.KS', 'sector': '제조업', 'close': 100}]
        with patch.dict(os.environ, {'WAMO_PRICE_ONLY': '1'}), patch.object(core, 'dart_corp_map', side_effect=AssertionError('network')), patch.object(core, '_load_profile_cache', side_effect=AssertionError('disk')):
            dart = core.dart_enrich(raw, {old['ticker']: old})
            profile = core.profile_enrich(raw, {old['ticker']: old})
        self.assertEqual(raw[0]['close'], 999)
        self.assertEqual(raw[0]['dartFetchedAt'], '2026-09-07')
        self.assertEqual(raw[0]['sector'], '반도체')
        self.assertEqual(raw[0]['sales_cur'], 123)
        self.assertNotIn('sales_cur', raw[1])
        self.assertEqual(dart['fetchedCount'], 0)
        self.assertEqual(profile['fetchedCount'], 0)

    def test_us_price_mode_never_fetches_company_data_and_keeps_original_date(self):
        raw = [{'ticker': 'AAPL', 'close': 250}, {'ticker': 'NEW', 'close': 10}]
        cache = {'AAPL': {'secStatus': 'NASDAQ_FALLBACK', 'profileFetchedAt': '2026-09-07', 'sales_cur': 456}}
        with patch.dict(os.environ, {'WAMO_PRICE_ONLY': '1'}), patch.object(us, '_load_cache', return_value=cache), patch.object(us, '_save_cache'), patch.object(us, 'sec_company_map', side_effect=AssertionError('network')), patch.object(us, 'nasdaq_enrich_one', side_effect=AssertionError('network')), patch.object(us, 'sec_enrich_one', side_effect=AssertionError('network')):
            meta = us.enrich_sec(raw)
        self.assertEqual(raw[0]['close'], 250)
        self.assertEqual(raw[0]['profileFetchedAt'], '2026-09-07')
        self.assertEqual(raw[0]['secStatus'], 'NASDAQ_CACHED')
        self.assertEqual(meta['targetCount'], 0)
        self.assertEqual(meta['fetchedCount'] + meta['nasdaqFetchedCount'], 0)
