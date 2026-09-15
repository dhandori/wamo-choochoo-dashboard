import copy
import io
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
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

    def test_korean_close_runs_once_at_1600_kst(self):
        close = instant('2026-09-07T07:00')
        self.assertEqual(runner.select_targets({}, close), [('KR', close)])
        state = {'KR': {'slot': close.isoformat(), 'success': True, 'attempts': 1}}
        self.assertEqual(runner.select_targets(state, instant('2026-09-07T08:00')), [])

    def test_friday_us_close_and_holiday(self):
        close = instant('2026-09-04T20:20')
        self.assertEqual(rt.latest_slot('US', close), close)
        self.assertEqual(close.astimezone(rt.KST).strftime('%a %H:%M'), 'Sat 05:20')
        # Labor Day has no US update slots; next session is Tuesday.
        self.assertEqual(rt.next_slot('US', close), instant('2026-09-08T20:20'))
        self.assertEqual(rt.expected_session('US', instant('2026-09-07T20:00')), '2026-09-04')

    def test_us_dst_standard_time_and_early_close(self):
        self.assertEqual(rt.latest_slot('US', instant('2026-03-09T20:20')), instant('2026-03-09T20:20'))
        self.assertEqual(rt.latest_slot('US', instant('2026-11-03T21:20')), instant('2026-11-03T21:20'))
        self.assertEqual(rt.expected_session('US', instant('2026-11-27T18:20')), '2026-11-27')
        # Even on a 13:00 ET early close, the publication slot remains 16:20 ET.
        self.assertEqual(runner.select_targets({}, instant('2026-11-27T18:20'), 'US'), [])
        self.assertEqual(runner.select_targets({}, instant('2026-11-27T21:20'), 'US'), [('US', instant('2026-11-27T21:20'))])

    def test_two_us_cron_candidates_publish_only_once_per_session(self):
        # During standard time, 20:20 UTC is before the logical 16:20 ET slot.
        self.assertEqual(runner.select_targets({}, instant('2026-11-03T20:20')), [])
        slot = instant('2026-11-03T21:20')
        self.assertEqual(runner.select_targets({}, slot), [('US', slot)])
        state = {'US': {'slot': slot.isoformat(), 'success': True, 'attempts': 1}}
        self.assertEqual(runner.select_targets(state, instant('2026-11-03T22:20')), [])
        self.assertEqual(runner.select_targets(state, instant('2026-11-03T22:20'), 'US', True), [])

    def test_second_us_cron_does_not_retry_failed_close_automatically(self):
        slot = instant('2026-03-09T20:20')
        failed = {'US': {'slot': slot.isoformat(), 'success': False, 'attempts': 1}}
        self.assertEqual(runner.select_targets(failed, instant('2026-03-09T21:20'), 'US'), [])
        self.assertEqual(runner.select_targets(failed, instant('2026-03-09T21:20'), 'US', True), [('US', slot)])
        failed['US']['attempts'] = 3
        self.assertEqual(runner.select_targets(failed, instant('2026-03-09T21:20'), 'US', True), [])

    def test_schedule_migration_deduplicates_by_market_session(self):
        legacy = {'US': {'slot': instant('2026-09-14T21:20').isoformat(),
                         'success': True, 'attempts': 1}}
        self.assertEqual(runner.select_targets(legacy, instant('2026-09-15T05:45'), 'US', True), [])
        malformed = {'US': {'slot': '0001-01-01T00:00:00+14:00',
                            'success': True, 'attempts': 1}}
        self.assertEqual(runner.select_targets(malformed, instant('2026-09-15T05:45'), 'US', True),
                         [('US', instant('2026-09-14T20:20'))])

    def test_workflow_has_only_three_close_candidates(self):
        import inspect
        import re
        workflow = (runner.ROOT / '.github/workflows/main.yml').read_text()
        crons = re.findall(r"cron: '([0-9]+) ([0-9]+) \* \* 1-5'", workflow)
        actual = {(int(hour), int(minute)) for minute, hour in crons}
        self.assertEqual(actual, {(7, 0), (20, 20), (21, 20)})
        self.assertNotIn('7,22,37,52', workflow)
        self.assertIn('fetch-depth: 1', workflow)
        self.assertIn("if: github.event_name != 'push'", workflow)
        self.assertIn("github.event_name == 'push' && 'code-check' || 'data-update'", workflow)
        self.assertIn("steps.refresh.outputs.updated == 'true'", workflow)
        self.assertIn('timeout-minutes: 65', workflow)
        self.assertIn('timeout-minutes: 40', workflow)
        # The child timeout must leave time for restore/status publication before
        # GitHub terminates the 30-minute refresh step.
        self.assertEqual(inspect.signature(runner.run_script).parameters['timeout'].default, 1500)

    def test_all_published_schedule_labels_match_daily_close_workflow(self):
        files = ['READ_ME_FIRST.txt', 'wamo_update_movers.py', 'wamo_status.js']
        obsolete = [
            '한국 09:30 / 10:30',
            '미국 22:40 / 23:40',
            '한국 09:30~16:00',
            '미국 22:40~다음날 06:20',
            '한국 12:00 / 16:00',
            '미국 00:00 / 05:00',
            '시장별 거래일 8회',
            'TOP 30 시장별 3회',
        ]
        for name in files:
            content = (runner.ROOT / name).read_text(encoding='utf-8')
            with self.subTest(name=name):
                for label in obsolete:
                    self.assertNotIn(label, content)
                self.assertIn('한국 16:00', content)
                self.assertIn('미국 뉴욕 16:20 이후', content)
        status_script = (runner.ROOT / 'wamo_status.js').read_text(encoding='utf-8')
        self.assertIn("querySelectorAll('small, #wamo-update-schedule, .sub b')", status_script)
        for market, name in [('KR', 'index.html'), ('US', 'us.html')]:
            revised = rt.patch_status_ui((runner.ROOT / name).read_text(encoding='utf-8'), market)
            self.assertNotIn('시장별 거래일 8회', revised)
            self.assertIn('한국 16:00', revised)
            self.assertIn('미국 뉴욕 16:20 이후', revised)

    def test_2026_cron_candidates_produce_one_update_per_market_session(self):
        cases = {'KR': [(7, 0)], 'US': [(20, 20), (21, 20)]}
        for market, candidates in cases.items():
            state, sessions = {}, []
            day = date(2026, 1, 1)
            while day.year == 2026:
                if day.weekday() < 5:
                    for hour, minute in candidates:
                        now = datetime(day.year, day.month, day.day, hour, minute, tzinfo=rt.UTC)
                        targets = runner.select_targets(state, now, market)
                        for selected, slot in targets:
                            sessions.append(slot.astimezone(rt.MARKET_TZ[selected]).date().isoformat())
                            state[selected] = {'slot': slot.isoformat(), 'success': True, 'attempts': 1}
                day += timedelta(days=1)
            expected = [d.date().isoformat() for d in rt.calendar(market).sessions_in_range('2026-01-01', '2026-12-31')]
            self.assertEqual(sessions, expected)

    def test_no_target_marks_workflow_output_as_not_updated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'output.txt'
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'STATE', root / 'state.json'), \
                 patch.object(runner, 'STATUS', root / 'status.json'), patch.object(runner, 'select_targets', return_value=[]), \
                 patch.object(sys, 'argv', ['wamo_run.py']), patch.dict('os.environ', {'GITHUB_OUTPUT': str(output)}, clear=True):
                runner.main()
            self.assertEqual(output.read_text(), 'updated=false\n')

    def test_no_weekend_holiday_or_stale_manual_publication(self):
        self.assertEqual(runner.select_targets({}, instant('2026-09-06T12:00')), [])
        self.assertEqual(runner.select_targets({}, instant('2026-09-07T20:20'), 'US', True), [])
        self.assertEqual(runner.select_targets({}, instant('2026-09-07T03:00'), 'KR', True), [])

    def test_prices_do_not_become_fresh_by_advancing_update_timestamp(self):
        payload = {'meta': {'updatedAt': '2026-09-07T17:00+09:00'},
                   'stocks': [{'date': '2026-08-31', 'dataStatus': 'LIVE'}]}
        with self.assertRaisesRegex(RuntimeError, '최신성 부족'):
            rt.validate_freshness(payload, 'KR', instant('2026-09-07T08:00'))
        payload['stocks'] = [{'date': '2026-09-07', 'dataStatus': 'CACHED'}]
        with self.assertRaises(RuntimeError):
            rt.validate_freshness(payload, 'KR', instant('2026-09-07T08:00'))

    def test_intraday_date_cannot_pass_as_previous_us_close(self):
        payload = {'meta': {}, 'stocks': [{'date': '2026-11-03', 'dataStatus': 'LIVE'}]}
        with self.assertRaisesRegex(RuntimeError, '최신성 부족'):
            rt.validate_freshness(payload, 'US', instant('2026-11-03T20:20'))

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
            # Only the declared schedule label may change; all market data must match.
            data['meta']['scheduledUpdateKst'] = rt.run_meta(market)['scheduledUpdateKst']
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
