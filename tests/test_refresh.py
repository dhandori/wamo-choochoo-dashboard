import copy
import io
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import wamo_runtime as rt
import wamo_run as runner
import wamo_kis as kis
import wamo_update_business_dart as core
import wamo_update_us_sec as us
import wamo_update_movers as movers


def instant(value):
    return datetime.fromisoformat(value + '+00:00')


class RefreshTests(unittest.TestCase):
    def test_krx_keeps_alphanumeric_codes_and_discards_aggregate_rows(self):
        response = {'output': [{'ISU_SRT_CD': code} for code in ['005930', '0126Z0', '0220W0', '합계', '']]}
        from unittest.mock import Mock
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(response).encode())
        self.assertEqual(core._fetch_krx_index_members(opener, '1028', '2026-09-07'), ['005930', '0126Z0', '0220W0'])

    def test_real_201_member_list_requires_independent_full_set_confirmation(self):
        k200 = [f'{i:06d}' for i in range(199)] + ['0126Z0', '0220W0']
        kq150 = [f'{i:06d}' for i in range(1000, 1150)]
        with patch.object(core, '_krx_login_opener'), \
             patch.object(core, '_fetch_krx_index_members', side_effect=[k200, kq150]), \
             patch.object(kis, 'kospi200_master_members', return_value=set(k200)):
            members, meta = core.resolve_market_energy_members([], {}, '2026-09-07')
        self.assertEqual(len(members), 351)
        self.assertFalse(meta['approximationUsed'])
        with patch.object(core, '_krx_login_opener'), \
             patch.object(core, '_fetch_krx_index_members', side_effect=[k200, kq150]), \
             patch.object(kis, 'kospi200_master_members', return_value=set(k200[:-1])):
            with self.assertRaisesRegex(RuntimeError, '교차검증 실패'):
                core.resolve_market_energy_members([], {}, '2026-09-07')

    def test_two_distinct_korean_slots_and_duplicate_suppression(self):
        first = instant('2026-09-07T03:05')
        targets = runner.select_targets({}, first)
        self.assertEqual([m for m, _ in targets], ['KR'])
        slot = targets[0][1].isoformat()
        state = {'KR': {'slot': slot, 'success': True, 'attempts': 1}}
        self.assertEqual(runner.select_targets(state, instant('2026-09-07T04:47')), [])
        second = runner.select_targets(state, instant('2026-09-07T07:17'))
        self.assertEqual(second, [('KR', instant('2026-09-07T07:00'))])

    def test_delayed_noon_refresh_uses_latest_slot(self):
        due = runner.select_targets({}, instant('2026-09-07T07:56'))
        self.assertEqual(due, [('KR', instant('2026-09-07T07:00'))])

    def test_friday_us_close_and_holiday(self):
        close = instant('2026-09-04T21:20')
        self.assertEqual(rt.latest_slot('US', close), close)
        self.assertEqual(close.astimezone(rt.KST).strftime('%a %H:%M'), 'Sat 06:20')
        # Labor Day has no US update slots; next session is Tuesday.
        self.assertEqual(rt.next_slot('US', close), instant('2026-09-08T15:00'))
        self.assertEqual(rt.expected_session('US', instant('2026-09-07T20:00')), '2026-09-04')

    def test_no_past_weekend_catchup_or_endless_retries(self):
        self.assertEqual(runner.select_targets({}, instant('2026-09-06T12:00')), [])
        state = {'KR': {'slot': '2026-09-07T07:00:00+00:00', 'attempts': 3, 'success': False}}
        self.assertEqual(runner.select_targets(state, instant('2026-09-07T08:00')), [])
        self.assertEqual(len(runner.select_targets(state, instant('2026-09-07T08:00'), 'both', True)), 2)

    def test_prices_do_not_become_fresh_by_advancing_update_timestamp(self):
        payload = {'meta': {'updatedAt': '2026-09-07T17:00+09:00'},
                   'stocks': [{'date': '2026-08-31', 'dataStatus': 'LIVE'}]}
        with self.assertRaisesRegex(RuntimeError, '최신성 부족'):
            rt.validate_freshness(payload, 'KR', instant('2026-09-07T08:00'))
        payload['stocks'] = [{'date': '2026-09-07', 'dataStatus': 'CACHED'}]
        with self.assertRaises(RuntimeError):
            rt.validate_freshness(payload, 'KR', instant('2026-09-07T08:00'))

    def test_future_price_rejected_and_optional_failure_visible(self):
        p = {'meta': {'marketEnergy': {'status': 'FAILED', 'fallbackReason': '201개'}},
             'stocks': [{'date': '2026-09-08', 'dataStatus': 'LIVE'}]}
        with self.assertRaisesRegex(RuntimeError, '미래 날짜'):
            rt.validate_freshness(p, 'KR', instant('2026-09-07T08:00'))
        p['stocks'][0]['date'] = '2026-09-07'
        result = rt.validate_freshness(p, 'KR', instant('2026-09-07T08:00'))
        self.assertTrue(result['warnings'])

    def test_live_payload_regression_and_ui_patch_preserves_market_data(self):
        for market, name, validator in [('KR', 'index.html', core.validate_payload_integrity),
                                        ('US', 'us.html', us.validate_us_payload)]:
            original = (runner.ROOT / name).read_text(encoding='utf-8')
            data = core.extract_old_payload(original)
            self.assertEqual(validator(data)['status'], 'PASS')
            revised = core.patch_index_health_ui(original, market)
            self.assertEqual(core.extract_old_payload(revised), data)
            self.assertNotIn('href="kkangto.html"', revised)
            self.assertNotIn('미국 00:00 / 05:00', revised)
            self.assertEqual(revised.count('<script src="wamo_status.js" defer></script>'), 1)
            self.assertEqual(core.patch_index_health_ui(revised, market), revised)

    def test_dart_cache_retains_health_to_avoid_repeated_api_calls(self):
        old = {'dartStatus': 'LIVE', 'financialHealth': {'status': 'PASS'}}
        target = {}
        self.assertTrue(core._copy_dart_cache(target, old))
        self.assertEqual(target['financialHealth'], old['financialHealth'])
        with patch.object(core, 'dart_statement', side_effect=AssertionError('network')):
            self.assertEqual(movers._kr_health('005930', target, {})['status'], 'PASS')

    def test_provider_failure_restores_output(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'ROOT', Path(folder)):
            page = Path(folder) / 'index.html'
            page.write_text('good')
            before = runner.snapshot(['index.html', 'new-cache.json'])
            page.write_text('bad')
            (Path(folder) / 'new-cache.json').write_text('{}')
            runner.restore(before)
            self.assertEqual(page.read_text(), 'good')
            self.assertFalse((Path(folder) / 'new-cache.json').exists())

    def test_kis_missing_secrets_is_optional(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(kis.KIS, 'authenticate', side_effect=AssertionError('network')):
            self.assertEqual(kis.enrich([])['status'], 'NOT_CONFIGURED')

    def test_kis_quotes_do_not_populate_forward_fields(self):
        stock = {'stock_code': '005930', 'ticker': '005930.KS', 'score': 80}
        with patch.dict('os.environ', {'KIS_APP_KEY': 'test', 'KIS_APP_SEC': 'test'}), \
             patch.object(kis.KIS, 'authenticate'), patch.object(kis.KIS, 'quote', return_value={'stck_prpr': '70000', 'eps': '4000', 'per': '17.5'}), \
             patch.object(kis.KIS, 'estimate', return_value={}), patch.object(kis, 'write_json'):
            meta = kis.enrich([stock])
        self.assertTrue(meta['connected'])
        self.assertFalse(meta['forwardMetricsConnected'])
        self.assertNotIn('forecast_eps', stock)
        self.assertEqual(stock['kisQuote']['per'], 17.5)


if __name__ == '__main__':
    unittest.main()
