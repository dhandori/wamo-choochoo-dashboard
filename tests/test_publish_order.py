import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys
import wamo_run as runner
import wamo_runtime as runtime
import wamo_update_business_dart as core


class EarlyPublicationTests(unittest.TestCase):
    def test_price_is_published_before_movers_even_when_movers_fail(self):
        for fail_movers in (False, True):
            with self.subTest(fail_movers=fail_movers), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'index.html').write_text('<body>fresh prices</body>')
                (root / 'us.html').write_text('<body>previous US</body>')
                (root / 'movers.html').write_text('previous movers')
                events = []
                published = []
                def script(name, *args, **kwargs):
                    events.append(name)
                    if name == 'wamo_update_movers.py':
                        self.assertEqual(published[0]['KR']['status'], 'PASS')
                        self.assertEqual(published[0]['KR']['moversStatus'], 'RUNNING')
                        (root / 'movers.html').write_text('new movers')
                        if fail_movers:
                            raise subprocess.CalledProcessError(1, name)
                def publish(names):
                    events.append('publish')
                    published.append(json.loads((root / 'wamo_refresh_status.json').read_text()))
                payload = {'meta': {'asOf': '2026-09-08'}, 'stocks': [{'date': '2026-09-08'}]}
                with patch.object(runner, 'ROOT', root), patch.object(runner, 'STATE', root / 'wamo_refresh_state.json'), patch.object(runner, 'STATUS', root / 'wamo_refresh_status.json'), patch.object(runner, 'run_script', side_effect=script), patch.object(runner, 'publish', side_effect=publish), patch.object(core, 'extract_old_payload', return_value=payload), patch.object(runtime, 'validate_freshness', return_value={'warnings': []}), patch.object(sys, 'argv', ['wamo_run.py', '--market', 'KR', '--publish']), patch.dict('os.environ', {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
                    runner.main()
                self.assertEqual(events[:3], ['wamo_update_business_dart.py', 'publish', 'wamo_update_movers.py'])
                self.assertEqual(published[-1]['KR']['moversStatus'], 'FAILED' if fail_movers else 'PASS')
                self.assertEqual(published[-1]['KR']['lastSuccessAt'], published[0]['KR']['lastSuccessAt'])
                if fail_movers:
                    self.assertEqual((root / 'movers.html').read_text(), 'previous movers')
                    self.assertIn('fresh prices', (root / 'index.html').read_text())
