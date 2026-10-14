import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wamo_publish_insights import publish_snapshot

class InsightPublishTests(unittest.TestCase):
    def test_snapshot_is_embedded_and_script_content_cannot_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'radar.template.html').write_text('<html><!--WAMO_INSIGHTS--></html>')
            data={'schemaVersion':1,'markets':{},'name':'</script><script>alert(1)</script>'}
            with patch('wamo_publish_insights.collect_snapshot',return_value=data):publish_snapshot(root)
            html=(root/'radar.html').read_text()
            self.assertNotIn('</script><script>alert',html)
            self.assertIn('id="wamo-snapshot"',html)
            self.assertEqual(json.loads((root/'wamo_insights.json').read_text()),data)
    def test_collection_failure_preserves_existing_published_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'radar.template.html').write_text('<!--WAMO_INSIGHTS-->')
            (root/'radar.html').write_text('old page');(root/'wamo_insights.json').write_text('{"old":true}')
            with patch('wamo_publish_insights.collect_snapshot',side_effect=ValueError('invalid')):
                with self.assertRaises(ValueError):publish_snapshot(root)
            self.assertEqual((root/'radar.html').read_text(),'old page')
            self.assertEqual((root/'wamo_insights.json').read_text(),'{"old":true}')
