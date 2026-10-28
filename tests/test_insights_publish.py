import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wamo_publish_insights import publish_snapshot, version_assets

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

class AssetVersionTests(unittest.TestCase):
    def test_assets_get_content_hashes_and_modules_share_one_map(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'radar').mkdir()
            (root/'radar/view.js').write_text("import { a } from './help.js';")
            (root/'radar/help.js').write_text('export const a=1;')
            (root/'radar/style.css').write_text('body{}')
            page='<link href="./radar/style.css"><link href="./radar/gone.css"><!--WAMO_IMPORTMAP--><script type="module" src="./radar/view.js"></script>'
            html=version_assets(page,root)
            self.assertRegex(html,r'href="\./radar/style\.css\?v=[0-9a-f]{10}"')
            self.assertIn('href="./radar/gone.css"',html)  # missing file stays as written
            self.assertRegex(html,r'src="\./radar/view\.js\?v=[0-9a-f]{10}"')
            imports=json.loads(html.split('<script type="importmap">')[1].split('</script>')[0])['imports']
            self.assertEqual(set(imports),{'./radar/help.js','./radar/view.js'})
            self.assertLess(html.index('importmap'),html.index('type="module"'))
            # Same content → same page; a changed module → only its own entry changes.
            self.assertEqual(html,version_assets(page,root))
            (root/'radar/help.js').write_text('export const a=2;')
            changed=json.loads(version_assets(page,root).split('<script type="importmap">')[1].split('</script>')[0])['imports']
            self.assertNotEqual(changed['./radar/help.js'],imports['./radar/help.js'])
            self.assertEqual(changed['./radar/view.js'],imports['./radar/view.js'])

    def test_no_hand_written_versions_left(self):
        root=Path(__file__).resolve().parents[1]
        files=[root/'radar.template.html',*(root/'radar').glob('*.js'),*(root/'radar').glob('*.mjs')]
        stale=[p.name for p in files if '?v=' in p.read_text(encoding='utf-8')]
        self.assertEqual(stale,[],'버전은 wamo_publish_insights.version_assets 가 붙인다. ?v= 를 손으로 쓰지 않는다')
