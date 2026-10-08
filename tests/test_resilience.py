import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wamo_run as runner
from wamo_runtime import UTC


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


class RecoveryTests(unittest.TestCase):
    def test_operator_rerun_bypasses_automatic_retry_cooldown(self):
        import contextlib
        import io
        import sys
        now = at('2026-10-08T01:56')
        slot = at('2026-10-07T20:20')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'state.json').write_text(json.dumps({'US':{'slot':slot.isoformat(), 'success':False,
                'attempts':2,'lastAttemptAt':at('2026-10-08T01:55').isoformat()}}))
            output=io.StringIO()
            with patch.object(runner,'STATE',root/'state.json'), patch.object(runner,'STATUS',root/'status.json'), patch.object(runner,'datetime',wraps=datetime) as clock, patch.dict('os.environ',{'GITHUB_EVENT_NAME':'schedule','GITHUB_RUN_ATTEMPT':'2'},clear=True), patch.object(sys,'argv',['wamo_run.py','--market','US','--plan']), contextlib.redirect_stdout(output):
                clock.now.return_value=now
                runner.main()
            self.assertIn("('US',",output.getvalue())

    def test_failed_close_retries_after_cooldown(self):
        slot = at('2026-10-06T20:20')
        state = {'US': {'slot': slot.isoformat(), 'attempts': 1, 'success': False,
                        'lastAttemptAt': slot.isoformat()}}
        self.assertEqual(runner.select_targets(state, slot + timedelta(minutes=29), 'US'), [])
        self.assertEqual(runner.select_targets(state, slot + timedelta(minutes=30), 'US'), [('US', slot)])

    def test_watchdog_recovers_missed_and_repeatedly_failed_session(self):
        slot = at('2026-10-06T20:20')
        now = at('2026-10-07T10:00')
        self.assertEqual(runner.select_targets({}, now, 'US', recovery=True), [('US', slot)])
        state = {'US': {'slot': slot.isoformat(), 'attempts': 8, 'success': False,
                        'lastAttemptAt': slot.isoformat()}}
        self.assertEqual(runner.select_targets(state, now, 'US', recovery=True), [('US', slot)])
        state['US']['success'] = True
        self.assertEqual(runner.select_targets(state, now, 'US', recovery=True), [])

    def test_interrupted_run_preserves_successful_market(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'KR': {'success': False}, 'US': {'success': True}}
            status = {'KR': {'status': 'RUNNING'}, 'US': {'status': 'PASS', 'moversStatus': 'RUNNING', 'warnings': []}}
            (root / 'state.json').write_text(json.dumps(state))
            (root / 'status.json').write_text(json.dumps(status))
            with patch.object(runner, 'STATE', root/'state.json'), patch.object(runner, 'STATUS', root/'status.json'):
                self.assertTrue(hasattr(runner, 'finalize_interrupted'))
                runner.finalize_interrupted(False)
            final = json.loads((root/'status.json').read_text())
            self.assertEqual(final['KR']['status'], 'FAILED')
            self.assertEqual(final['US']['status'], 'WARNING')
            self.assertEqual(final['US']['moversStatus'], 'FAILED')
            self.assertTrue(json.loads((root/'state.json').read_text())['US']['success'])

class PublicationTests(unittest.TestCase):
    def test_identical_catalog_keeps_original_generation_time_and_bytes(self):
        import wamo_catalog as catalog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root/'index.html'
            page.write_text('window.WAMO_DATA = {"meta":{"asOf":"2026-10-06"},"stocks":[]};')
            with patch.object(catalog, 'ROOT', root), patch.object(catalog, 'PAGES', {'KR': page}):
                catalog.main()
                destination = root/'wamo_catalog.json'
                previous = json.loads(destination.read_text())
                previous['generatedAt'] = '2026-10-06T00:00:00Z'
                destination.write_text(json.dumps(previous))
                before = destination.read_bytes()
                catalog.main()
                self.assertEqual(destination.read_bytes(), before)

    def test_short_listing_does_not_repeat_same_provider_three_times(self):
        import io
        import wamo_update_business_dart as core
        from tests.test_us_history import chart
        data = json.loads(chart('2026-10-06'))
        data['chart']['result'][0]['timestamp'] = data['chart']['result'][0]['timestamp'][:20]
        calls = []
        def response(req, **kwargs):
            calls.append(req.full_url)
            return io.BytesIO(json.dumps(data).encode())
        with patch.object(core.urllib.request, 'urlopen', side_effect=response), patch.object(core.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '이력 부족'):
                core.fetch_yahoo_history('NEW')
        self.assertEqual(len(calls), 2)

    def test_missing_radar_credentials_update_status_instead_of_silent_exit(self):
        import wamo_radar as radar
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('index.html','us.html'):
                (root/name).write_text('<body></body>')
            with patch.object(radar, '__file__', str(root/'wamo_radar.py')), patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(SystemExit):
                    radar.main()
            self.assertTrue((root/'wamo_radar.json').exists())
            data = json.loads((root/'wamo_radar.json').read_text())
            self.assertEqual(data['editions']['US']['errorCode'], 'MISSING_TOKEN')

class AuxiliaryRecoveryTests(unittest.TestCase):
    def test_radar_refresh_follows_prices_and_marks_current_edition(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'wamo_radar.json').write_text(json.dumps({'editions':{'ASIA':{
                'status':'PASS','sessions':{'KR-KOSPI':'20261008','KR-KOSDAQ':'20261008'}}}}))
            status={'KR':{'status':'PASS','asOf':'2026-10-08','warnings':[]}}
            with patch.object(runner,'ROOT',root),patch.object(runner,'STATUS',root/'status.json'),patch.object(runner,'run_script') as run,patch.object(runner,'expected_session',return_value='2026-10-08'):
                runner.refresh_radar(status,['KR'])
            run.assert_called_once_with('wamo_radar.py',timeout=180)
            self.assertEqual(status['KR']['radarStatus'],'PASS')

    def test_stale_radar_pass_does_not_rollback_current_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'wamo_radar.json').write_text(json.dumps({'editions':{'ASIA':{
                'status':'PASS','sessions':{'KR-KOSPI':'20261007'}}}}))
            status={'KR':{'status':'PASS','asOf':'2026-10-08','warnings':[]}}
            with patch.object(runner,'ROOT',root),patch.object(runner,'STATUS',root/'status.json'),patch.object(runner,'run_script',side_effect=RuntimeError('radar failed')),patch.object(runner,'expected_session',return_value='2026-10-08'):
                runner.refresh_radar(status,['KR'])
            self.assertEqual(status['KR']['radarStatus'],'FAILED')
            self.assertEqual(status['KR']['status'],'WARNING')
            self.assertEqual(status['KR']['asOf'],'2026-10-08')

    def test_recovery_retries_movers_without_collecting_prices(self):
        import contextlib
        import io
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slot = at('2026-10-06T20:20')
            (root/'state.json').write_text(json.dumps({'US': {'success': True, 'slot': slot.isoformat()}}))
            (root/'status.json').write_text(json.dumps({'US': {'status': 'WARNING', 'moversStatus': 'FAILED', 'warnings': ['SEC 확인 필요', '상승률 TOP 30 갱신 실패 · 이전 결과 유지']}}))
            (root/'movers.html').write_text('old')
            def run(script, *args, **kwargs):
                self.assertEqual(script, 'wamo_update_movers.py')
                (root/'movers.html').write_text('recovered')
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'STATE', root/'state.json'), patch.object(runner, 'STATUS', root/'status.json'), patch.object(runner, 'run_script', side_effect=run), patch.object(runner, 'select_targets', return_value=[]), patch.object(runner, 'latest_slot', return_value=slot), patch.object(runner, 'expected_session', return_value='2026-10-06'), patch.object(sys, 'argv', ['wamo_run.py','--recovery','--market','US']), contextlib.redirect_stdout(io.StringIO()):
                runner.main()
            self.assertEqual((root/'movers.html').read_text(), 'recovered')
            result=json.loads((root/'status.json').read_text())['US']
            self.assertEqual(result['moversStatus'],'PASS')
            self.assertEqual(result['warnings'],['SEC 확인 필요'])
            self.assertTrue(json.loads((root/'state.json').read_text())['US']['success'])

class HolidayRecoveryTests(unittest.TestCase):
    def test_long_korean_holiday_does_not_block_us_recovery(self):
        now = at('2025-10-10T01:00')
        expected_us = at('2025-10-09T20:20')
        targets = runner.select_targets({}, now, recovery=True)
        self.assertIn(('US', expected_us), targets)
