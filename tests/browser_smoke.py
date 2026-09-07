"""Render current repository data on desktop/mobile; no market API requests."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wamo_update_business_dart as core
from wamo_runtime import patch_status_ui
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
results = root / 'test-results'
results.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as folder:
    folder = Path(folder)
    for name in ['index.html', 'us.html', 'movers.html']:
        html = (root / name).read_text(encoding='utf-8')
        (folder / name).write_text(patch_status_ui(html, 'US' if name == 'us.html' else 'KR'), encoding='utf-8')
    (folder / 'wamo_status.js').write_bytes((root / 'wamo_status.js').read_bytes())
    status = {'KR': {'status': 'FAILED', 'asOf': '2026-09-07', 'attemptedAt': '2026-09-07T08:00:00Z',
                     'lastSuccessAt': '2026-09-07T07:00:00Z', 'error': '가격 갱신 실패 · 이전 정상 데이터 유지'},
              'US': {'status': 'WARNING', 'asOf': '2026-09-04', 'warnings': ['SEC 직접연결 안 됨']}}
    (folder / 'wamo_refresh_status.json').write_text(json.dumps(status), encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in [(1440, 1000), (390, 844)]:
            for name in ['index.html', 'us.html', 'movers.html']:
                page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort())
                page.goto(f'http://127.0.0.1:{server.server_port}/{name}', wait_until='networkidle', timeout=60000)
                page.locator('#wamo-live-status').wait_for()
                assert not errors, (name, width, errors)
                assert page.locator('a[href="kkangto.html"]').count() == 0
                assert '05:00' not in page.locator('#wamo-live-status').inner_text()
                if name == 'index.html':
                    assert '갱신 실패' in page.locator('#wamo-live-status').inner_text()
                if name == 'index.html':
                    panel = page.locator('#wamo-kis-panel')
                    panel.locator('summary').click()
                    assert 'Forward PER' in panel.inner_text()
                    search = panel.locator('input')
                    search.fill('__absent__')
                    assert '검색 결과가 없습니다.' in panel.inner_text()
                    search.fill('')
                    assert panel.locator('tbody tr:visible').count() > 0
                    # Existing hero opens the real drawer, including the provider card.
                    page.locator('.hero-chip').first.click()
                    page.wait_for_timeout(100)
                    assert '한국투자 시세·밸류 보조확인' in page.locator('#wamo-kis-detail').inner_text()
                    assert not errors, errors
                overflow = page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
                assert not overflow, (name, width, 'horizontal overflow')
                page.screenshot(path=str(results / f'{name}-{width}.png'), full_page=False)
                print('BROWSER PASS', name, width)
                page.close()
        browser.close()
    server.shutdown()
