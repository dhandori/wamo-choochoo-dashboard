"""Real-payload radar interaction, mobile, and offline-retention smoke checks."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
DATA=json.loads((ROOT/'wamo_insights.json').read_text())
OUT=ROOT/'test-results';OUT.mkdir(exist_ok=True)
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(ROOT)))
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
with sync_playwright() as p:
    browser=p.chromium.launch()
    for width in (1440,390,360):
        for market in ('KR','US'):
            context=browser.new_context(viewport={'width':width,'height':1000})
            page=context.new_page(); errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) else route.abort())
            page.goto(f'{base}/radar.html?market={market}',wait_until='networkidle')
            expect(page.locator('#date-info')).to_contain_text(DATA['markets'][market]['asOf'])
            expect(page.locator('#section-title')).to_have_text('52주 신고가권')
            rows=DATA['markets'][market]['stocks']
            near=[s for s in rows if s['highDistancePct'] is not None and s['highDistancePct']<=10]
            expect(page.locator('#pagination')).to_contain_text(f'{len(near)}종목')
            if near:
                page.locator('.stock-link:visible').first.click();expect(page.get_by_role('dialog',name=sorted(near,key=lambda s:s['highDistancePct'])[0]['name'],exact=True)).to_be_visible()
                expect(page.locator('#detail')).to_contain_text('가격 출처')
                expect(page.locator('.detail-chart')).to_be_visible()
                drawer_link=page.locator('#detail .naver-primary')
                if drawer_link.count():
                    assert drawer_link.get_attribute('href')==sorted(near,key=lambda s:s['highDistancePct'])[0]['naverUrl']
                    assert drawer_link.get_attribute('target')=='_blank'
                    assert 'noopener' in drawer_link.get_attribute('rel')
                assert not page.locator('#detail a[href*=legacy]').count()
                page.screenshot(path=str(OUT/f'radar-{market}-{width}-drawer.png'),full_page=False)
                page.locator('#close-drawer').click()
            page.locator('[data-tab=high]').click()
            highs=[s for s in rows if s['intradayHigh52'] or s['closeHigh52']]
            expect(page.locator('#pagination')).to_contain_text(f'{len(highs)}종목')
            page.get_by_role('button',name='장중 고점',exact=True).click()
            expect(page.locator('#pagination')).to_contain_text(f'{sum(s["intradayHigh52"] is True for s in rows)}종목')
            page.locator('[data-tab=movers]').click()
            page.get_by_role('button',name='하락 ≤−3%',exact=True).click()
            expect(page.locator('#pagination')).to_contain_text(f'{sum(s["changePct"] is not None and s["changePct"]<=-3 for s in rows)}종목')
            page.screenshot(path=str(OUT/f'radar-{market}-{width}-movers.png'),full_page=False)
            page.locator('[data-tab=industries]').click()
            if page.locator('.industry-card').count():
                page.locator('.industry-card').first.click()
                expect(page.get_by_role('button',name='← 전체 산업')).to_be_visible()
                expect(page.locator('.stock-link:visible').first).to_be_visible()
            page.locator('[data-tab=near]').click()
            page.locator('#search').fill('__does_not_exist__')
            expect(page.locator('.empty')).to_contain_text('조건에 맞는 종목이 없습니다')
            page.get_by_role('button',name='필터 초기화',exact=True).click()
            expect(page.locator('#search')).to_have_value('')
            expect(page.get_by_role('button',name='이전',exact=True)).to_be_disabled()
            if width<=390:
                expect(page.locator('[data-tab=near] .tab-count')).to_contain_text(f'10% 내 {len(near):,}')
                expect(page.locator('.stock-cards')).to_be_visible()
                expect(page.locator('.table-wrap')).to_be_hidden()
            if page.locator('.rail-group').count():
                page.locator('.rail-group').first.click()
                expect(page.get_by_role('button',name='← 전체 산업')).to_be_visible()
                expect(page.locator('.stock-link:visible').first).to_be_visible()
                page.locator('[data-tab=near]').click()
            dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
            assert dimensions['scroll']<=dimensions['client'],dimensions
            assert not errors,errors
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(OUT/f'radar-{market}-{width}.png'),full_page=False)
            first_y=page.locator('.stock-link:visible').first.bounding_box()['y']
            print('FIRST STOCK Y',market,width,round(first_y))
            if width==1440:assert first_y<520,first_y
            else:assert first_y<580,first_y
            context.close()
            print('RADAR INTERACTION PASS',market,width)
    for failure in ('offline','invalid'):
        context=browser.new_context();page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(request):
            if 'wamo_insights.json' in request.request.url:
                if failure=='offline':request.abort()
                else:request.fulfill(status=200,content_type='application/json',body='{"schemaVersion":1,"markets":{}}')
            else:request.continue_()
        page.route('**/*',route)
        page.goto(base+'/radar.html',wait_until='networkidle')
        expect(page.locator('.stock-link:visible').first).to_be_visible()
        expect(page.locator('#notice')).to_contain_text('검증')
        page.get_by_role('button',name='데이터 확인',exact=True).click()
        expect(page.locator('.stock-link:visible').first).to_be_visible()
        page.locator('[data-tab=movers]').click();expect(page.locator('.stock-link:visible').first).to_be_visible()
        assert not errors,errors
        context.close();print('RADAR RETENTION PASS',failure)
    context=browser.new_context();page=context.new_page()
    page.add_init_script("window.fetch=()=>new Promise((_,reject)=>setTimeout(()=>reject(new Error('offline')),350))")
    page.goto(base+'/radar.html',wait_until='networkidle')
    refresh=page.get_by_role('button',name='데이터 확인',exact=True)
    refresh.click()
    expect(page.get_by_role('button',name='확인 중…',exact=True)).to_be_disabled()
    expect(page.get_by_role('button',name='데이터 확인',exact=True)).to_be_enabled()
    expect(page.locator('.stock-link:visible').first).to_be_visible()
    context.close();print('RADAR MANUAL REFRESH PASS')
    for name,market in [('index.html','KR'),('us.html','US')]:
        page=browser.new_page()
        page.goto(base+'/'+name,wait_until='domcontentloaded');page.wait_for_url(f'**/radar.html?market={market}')
        page.close();print('RADAR ENTRY PASS',market)
    browser.close()
server.shutdown()
