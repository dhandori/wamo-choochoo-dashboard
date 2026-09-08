import os
import unittest
from datetime import datetime
from unittest.mock import patch
import wamo_runtime as rt
import wamo_run as runner
import wamo_update_business_dart as core
import wamo_update_us_sec as us


class IntradayTests(unittest.TestCase):
    def test_extra_slots_are_price_only_and_original_slots_are_full(self):
        for market in rt.SLOTS:
            slots = list(rt.slots_near(market, datetime(2026, 9, 8, tzinfo=rt.UTC)))
            slots = [s for s in slots if s.date().isoformat() == '2026-09-08']
            self.assertEqual(sum(rt.price_only_slot(market, s) for s in slots), 5)
            self.assertEqual(sum(not rt.price_only_slot(market, s) for s in slots), 3)
        now = datetime(2026, 9, 8, 4, 22, tzinfo=rt.UTC)
        state = {'KR': {'slot': '2026-09-08T03:00:00+00:00', 'success': True}}
        targets = runner.select_targets(state, now, 'KR')
        self.assertEqual(targets[0][1].hour, 4)
        state['KR'] = {'slot': targets[0][1].isoformat(), 'success': True}
        self.assertEqual(runner.select_targets(state, now, 'KR'), [])

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

    def test_delayed_full_slot_is_not_lost_to_added_price_slot(self):
        slot = datetime(2026, 9, 8, 4, tzinfo=rt.UTC)
        self.assertFalse(rt.use_price_only('KR', slot, {}))
        self.assertFalse(rt.use_price_only('KR', slot, {'lastFullSlot': '2026-09-08T00:30:00+00:00'}))
        self.assertTrue(rt.use_price_only('KR', slot, {'lastFullSlot': '2026-09-08T03:00:00+00:00'}))
        self.assertTrue(rt.use_price_only('KR', slot, {'slot': '2026-09-08T03:00:00+00:00', 'success': True}))
