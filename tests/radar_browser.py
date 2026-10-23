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
import re as _re
# One card per AI sector plus the 'AI 전체' card; read the list instead of hard-coding it.
AI_CARDS=len(_re.findall(r"\{id:'[a-z]+',name:", (ROOT/'radar/ai.mjs').read_text()))+1
def check_help_bounds(page):
    box=page.get_by_role('tooltip').bounding_box()
    assert box and box['x']>=0 and box['y']>=0,box
    assert box['x']+box['width']<=page.viewport_size['width'],box
    assert box['y']+box['height']<=page.viewport_size['height'],box
    assert page.get_by_role('tooltip').evaluate('(e)=>e.contains(document.elementFromPoint(e.getBoundingClientRect().x+e.clientWidth/2,e.getBoundingClientRect().y+e.clientHeight/2))')

def sort_box(page):
    # The sort/cap panel folds into one summary line (closed by default on phones).
    fold=page.locator('.sort-fold')
    if fold.count() and not fold.evaluate('(n)=>n.open'):fold.locator('summary').click()
    return page.get_by_role('combobox',name='정렬',exact=True)

with sync_playwright() as p:
    browser=p.chromium.launch()
    for width in (1440,390,360):
        for market in ('KR','US'):
            context=browser.new_context(viewport={'width':width,'height':1000},has_touch=width<760)
            page=context.new_page(); errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) else route.abort())
            page.goto(f'{base}/radar.html?market={market}',wait_until='networkidle')
            expect(page.locator('#date-info')).to_contain_text(DATA['markets'][market]['asOf'])
            # 2026-10-23 개편부터 첫 화면은 '추세 신호'다. 아래 점검은 신고가권 탭에서 이어 간다.
            expect(page.locator('#section-title')).to_have_text('추세 신호')
            page.locator('[data-tab=near]').click()
            expect(page.locator('#section-title')).to_have_text('52주 신고가권')
            # The 2026-10-19 layout puts the filters low on phones; bring them up first so the
            # tool's own scroll-into-view (which closes help by design) does not interfere.
            page.locator('#controls').evaluate('(n)=>n.scrollIntoView({block:"center"})'); page.wait_for_timeout(150)
            rs_help=page.locator('#controls [data-help=rs]')
            if width==1440:
                rs_help.hover();expect(page.get_by_role('tooltip')).to_contain_text('RSI')
                page.mouse.move(0,0);expect(page.get_by_role('tooltip')).not_to_be_visible()
                rs_help.focus();expect(page.get_by_role('tooltip')).to_be_visible()
                expect(rs_help).to_have_attribute('aria-describedby','term-tooltip')
                page.keyboard.press('Escape');expect(page.get_by_role('tooltip')).not_to_be_visible()
                rs_help.click()
            else:rs_help.tap()
            expect(page.get_by_role('tooltip')).to_contain_text('5일 수익률 20%')
            expect(page.get_by_role('checkbox',name='RS ≥80')).not_to_be_checked()
            check_help_bounds(page)
            page.screenshot(path=str(OUT/f'radar-{market}-{width}-help.png'),full_page=False)
            if width==1440:rs_help.click()
            else:rs_help.tap()
            expect(page.get_by_role('tooltip')).not_to_be_visible()
            page.locator('#controls [data-help=stage]').click()
            expect(page.get_by_role('tooltip')).to_contain_text('20거래일 전')
            expect(page.get_by_role('checkbox',name='Stage 2',exact=True)).not_to_be_checked()
            page.locator('#search').click();expect(page.get_by_role('tooltip')).not_to_be_visible()
            rows=DATA['markets'][market]['stocks']
            near=[s for s in rows if s['highDistancePct'] is not None and s['highDistancePct']<=10]
            expect(page.locator('#pagination')).to_contain_text(f'{len(near)}종목')
            if near:
                page.locator('.stock-link:visible').first.click();expect(page.get_by_role('dialog',name=sorted(near,key=lambda s:s['highDistancePct'])[0]['name'],exact=True)).to_be_visible()
                expect(page.locator('#detail')).to_contain_text('가격 출처')
                expect(page.locator('#detail .stat').filter(has_text='시가총액')).to_contain_text('원' if market=='KR' else '달러')
                expect(page.locator('.detail-chart')).to_be_visible()
                drawer_link=page.locator('#detail .naver-primary')
                if drawer_link.count():
                    assert drawer_link.get_attribute('href')==sorted(near,key=lambda s:s['highDistancePct'])[0]['naverUrl']
                    assert drawer_link.get_attribute('target')=='_blank'
                    assert 'noopener' in drawer_link.get_attribute('rel')
                assert not page.locator('#detail a[href*=legacy]').count()
                # The drawer now holds a taller chart; scroll first (help closes on scroll), as a user would.
                page.locator('#detail [data-help=volume]').scroll_into_view_if_needed()
                page.wait_for_timeout(250)
                page.locator('#detail [data-help=volume]').click()
                expect(page.get_by_role('tooltip')).to_contain_text('이전 50거래일')
                check_help_bounds(page)
                page.screenshot(path=str(OUT/f'radar-{market}-{width}-drawer-help.png'),full_page=False)
                page.keyboard.press('Escape')
                expect(page.get_by_role('tooltip')).not_to_be_visible()
                expect(page.locator('#drawer')).to_be_visible()
                page.screenshot(path=str(OUT/f'radar-{market}-{width}-drawer.png'),full_page=False)
                page.locator('#close-drawer').click()
                currency='원' if market=='KR' else '달러'
                for sort,reverse in [('marketCap',True),('marketCapAsc',False)]:
                    sort_box(page).select_option(sort)
                    ordered=sorted(near,key=lambda s:s['marketCap'],reverse=reverse)[:30]
                    assert page.locator('.stock-link:visible').evaluate_all('(els)=>els.map(e=>e.title)')==[s['name'] for s in ordered]
                    cap=page.locator('.market-cap:visible').first
                    expect(cap).to_have_attribute('title',f'{ordered[0]["marketCap"]:,.0f} {currency}')
                    expect(cap).not_to_have_text('정보 없음')
                page.screenshot(path=str(OUT/f'radar-{market}-{width}-market-cap.png'),full_page=False)
                if width<=390:expect(page.locator('.card-cap').first).to_contain_text(currency)
                else:expect(page.get_by_role('columnheader',name=f'시총 ({currency})',exact=True)).to_be_visible()
                sort_box(page).select_option('default')
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
            page.locator('[data-tab=ai]').click()
            expect(page.locator('#section-title')).to_have_text('AI 섹터')
            expect(page.locator('.ai-sector-card')).to_have_count(AI_CARDS)
            expect(page.locator('#industry-rail')).to_be_hidden()
            expect(page.locator('.trend-breadth')).to_have_count(AI_CARDS)
            page.locator('#ai-overview [data-help=trendBreadth]').click()
            expect(page.get_by_role('tooltip')).to_contain_text('동일 종목')
            check_help_bounds(page)
            page.keyboard.press('Escape')
            page.get_by_role('button',name='기간 비교',exact=True).click()
            expect(page.locator('.ai-heatmap .heat-cell')).to_have_count(AI_CARDS*6)  # 6 periods per row
            cell=page.locator('.heat-cell[data-sector=optics][data-period="200d"]')
            cell.focus();page.keyboard.press('Enter')
            expect(cell).to_have_attribute('aria-pressed','true')
            expect(page.get_by_role('combobox',name='AI 분야',exact=True)).to_have_value('optics')
            expect(sort_box(page)).to_have_value('default')
            expect(page.locator('#result-summary')).to_contain_text('광통신')
            names=page.locator('.stock-link:visible').evaluate_all('(els)=>els.map(e=>e.title)')
            returns=[next(s for s in rows if s['name']==name)['return200d'] for name in names]
            assert returns==sorted(returns,key=lambda value:float('-inf') if value is None else value,reverse=True)
            dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
            assert dimensions['scroll']<=dimensions['client'],dimensions
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(OUT/f'radar-{market}-{width}-heatmap.png'),full_page=False)
            page.get_by_role('button',name='카드',exact=True).click()
            page.get_by_role('button',name='필터 초기화',exact=True).click()
            prior={s['id']:s for s in DATA['markets'][market].get('comparison',{}).get('stocks',[])}
            def entered(s):
                old=prior.get(s['id'],{})
                return (s.get('highDistancePct') is not None and old.get('highDistancePct') is not None and s['highDistancePct']<=10<old['highDistancePct']) or (s.get('rs') is not None and old.get('rs') is not None and s['rs']>=80>old['rs']) or (s.get('stage2') is True and old.get('stage2') is False)
            if prior:
                page.locator('#signal-filter [data-help=newOnly]').click()
                expect(page.get_by_role('tooltip')).to_contain_text('직전 거래일')
                expect(page.get_by_role('checkbox',name='새로 들어온 종목만')).not_to_be_checked()
                page.keyboard.press('Escape')
                page.get_by_role('checkbox',name='새로 들어온 종목만').check()
                names=page.locator('.stock-link:visible').evaluate_all('(els)=>els.map(e=>e.title)')
                assert all(entered(next(s for s in rows if s['name']==name)) for name in names)
                assert page.locator('.change-badges:visible').count()==len(names)
                page.screenshot(path=str(OUT/f'radar-{market}-{width}-new.png'),full_page=False)
                page.get_by_role('checkbox',name='새로 들어온 종목만').uncheck()
                expect(page.locator('#session-changes')).not_to_have_attribute('open','')
                page.locator('#session-changes summary').click()
                expect(page.locator('.changes-guide')).to_contain_text('조건은 걸기 전')
                if page.locator('.exit-item').count():
                    page.locator('.exit-item').first.click()
                    expect(page.locator('#drawer .detail-changes')).to_contain_text('→')
                    page.locator('#close-drawer').click()
                page.locator('#session-changes summary').click()
            page.get_by_role('button',name='20일',exact=True).click()
            expect(page.locator('.ai-card-return').first).to_contain_text('20일 중앙값')
            for days in (60,120,200):
                page.get_by_role('button',name=f'{days}일',exact=True).click()
                expect(page.locator('.ai-card-return').first).to_contain_text(f'{days}일 중앙값')
                expect(page.get_by_role('button',name=f'{days}일',exact=True)).to_have_attribute('aria-pressed','true')
                expect(sort_box(page)).to_have_value('default')
                if width==1440:
                    expect(page.get_by_role('columnheader',name=f'{days}일',exact=True)).to_be_visible()
                    expect(page.get_by_role('columnheader',name='60일 추이',exact=True)).to_be_visible()
                    assert page.get_by_role('columnheader').count()==12
                else:expect(page.locator('.stock-card .stat').filter(has_text=f'{days}일').first).to_be_visible()
                names=page.locator('.stock-link:visible').evaluate_all('(els)=>els.map(e=>e.title)')
                returns=[next(s for s in rows if s['name']==name)[f'return{days}d'] for name in names]
                assert any(value is not None for value in returns)
                assert returns==sorted(returns,key=lambda value:float('-inf') if value is None else value,reverse=True)
                sort_box(page).select_option(f'return{days}d')
                page.locator('.stock-link:visible').first.click()
                expect(page.locator('#detail .stat').filter(has_text=f'{days}일 수익률')).to_be_visible()
                page.locator('#close-drawer').click()
            page.screenshot(path=str(OUT/f'radar-{market}-{width}-ai-200d.png'),full_page=False)
            dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
            assert dimensions['scroll']<=dimensions['client'],dimensions
            sort_box(page).select_option('return120d')
            expect(page.get_by_role('button',name='120일',exact=True)).to_have_attribute('aria-pressed','true')
            page.get_by_role('combobox',name='AI 분야',exact=True).select_option('optics')
            page.locator('#search').fill('010170' if market=='KR' else 'AVGO')
            expect(page.locator('.stock-link:visible')).to_have_count(1)
            expect(page.locator('.stock-link:visible')).to_contain_text('광통신')
            page.get_by_role('button',name='필터 초기화',exact=True).click()
            page.get_by_role('combobox',name='AI 종목 조건',exact=True).select_option('down')
            names=page.locator('.stock-link:visible').evaluate_all('(els)=>els.map(e=>e.title)')
            assert all(next(s for s in rows if s['name']==name)['changePct']<=-3 for name in names)
            page.get_by_role('button',name='필터 초기화',exact=True).click()
            sort_box(page).select_option('marketCap')
            names=page.locator('.stock-link:visible').evaluate_all('(els)=>els.map(e=>e.title)')
            caps=[next(s for s in rows if s['name']==name)['marketCap'] for name in names]
            assert caps==sorted(caps,reverse=True)
            page.screenshot(path=str(OUT/f'radar-{market}-{width}-ai.png'),full_page=False)
            dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
            assert dimensions['scroll']<=dimensions['client'],dimensions
            page.locator('[data-tab=near]').click()
            dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
            assert dimensions['scroll']<=dimensions['client'],dimensions
            assert not errors,errors
            # Earlier steps opened the sort panel; measure the default (folded on phones) layout.
            if width<760:page.evaluate("document.querySelectorAll('.sort-fold').forEach(n=>n.open=false)")
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(OUT/f'radar-{market}-{width}.png'),full_page=False)
            first_y=page.locator('.stock-link:visible').first.bounding_box()['y']
            # A legitimate freshness notice adds height after the session cutoff.
            # Keep the original content-density budget without hiding that notice.
            notice_height=page.locator('#notice').evaluate('(node)=>node.hidden?0:node.getBoundingClientRect().height+parseFloat(getComputedStyle(node).marginTop)+parseFloat(getComputedStyle(node).marginBottom)')
            # The market-weather strip is a deliberate always-on summary; budget the rest.
            weather_height=page.locator('#weather').evaluate('(node)=>node.hidden?0:node.getBoundingClientRect().height+parseFloat(getComputedStyle(node).marginBottom)')
            if width<760:assert weather_height<=280,weather_height  # 2026-10-23 개편: 휴대폰에서 점수 막대·'자세히' 버튼이 따로 한 줄을 쓰고, 미국은 지수 두 개라 칩이 한 줄 더 늘 수 있음
            else:assert weather_height<=120,weather_height  # 2026-10-19 redesign: larger weather conclusion line
            # Phone tabs wrap into two rows so every tab stays visible; budget the extra row.
            tabs_extra=max(0,page.locator('.tabs').evaluate('(n)=>n.getBoundingClientRect().height')-56)
            legend_extra=page.locator('.card-legend').first.evaluate('(n)=>n.offsetParent?n.getBoundingClientRect().height+8:0') if page.locator('.card-legend').count() else 0
            weather_height+=tabs_extra+legend_extra
            print('FIRST STOCK Y',market,width,round(first_y),'NOTICE',round(notice_height),'WEATHER',round(weather_height))
            if width==1440:assert first_y-notice_height-weather_height<760,first_y  # 2026-10-19 layout: two-row tabs + sort panel open on desktop
            else:assert first_y-notice_height-weather_height<1100,first_y  # phones: industry-flow strip sits above the list since 2026-10-19; 2026-10-23 grouped tabs and metric bars add ~70px
            if (ROOT/'wamo_trend.json').exists():
                page.locator('[data-tab=trend]').click()
                expect(page.locator('#section-title')).to_have_text('추세 신호')
                expect(page.locator('.tr-weather-detail')).to_be_visible()
                for key in ('sell','score','earnings','rs','weather'):
                    page.locator(f'.tr-subtabs button[data-sub={key}]').click()
                    assert f'sub={key}' in page.url,page.url
                # Switching sub-tabs far down the page keeps the bar on screen (no jump to the top).
                page.locator('.tr-subtabs button[data-sub=rs]').click()
                page.evaluate('window.scrollBy(0,2000)'); page.wait_for_timeout(200)
                page.locator('.tr-subtabs button[data-sub=score]').click(); page.wait_for_timeout(200)
                bar=page.locator('.tr-subtabs').bounding_box()
                assert -1<=bar['y']<=2,bar
                page.locator('.tr-subtabs button[data-sub=weather]').click()
                dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
                assert dimensions['scroll']<=dimensions['client'],dimensions
                page.screenshot(path=str(OUT/f'radar-{market}-{width}-trend.png'),full_page=False)
                assert not errors,errors
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
        page.goto(base+'/radar.html?tab=near',wait_until='networkidle')
        expect(page.locator('.stock-link:visible').first).to_be_visible()
        expect(page.locator('#notice')).to_contain_text('확인된')
        page.get_by_role('button',name='데이터 확인',exact=True).click()
        expect(page.locator('.stock-link:visible').first).to_be_visible()
        page.locator('[data-tab=movers]').click();expect(page.locator('.stock-link:visible').first).to_be_visible()
        page.locator('[data-tab=ai]').click();expect(page.locator('.stock-link:visible').first).to_be_visible()
        expect(page.locator('.ai-sector-card')).to_have_count(AI_CARDS)
        assert not errors,errors
        context.close();print('RADAR RETENTION PASS',failure)
    context=browser.new_context();page=context.new_page()
    page.add_init_script("window.fetch=()=>new Promise((_,reject)=>setTimeout(()=>reject(new Error('offline')),350))")
    page.goto(base+'/radar.html?tab=near',wait_until='networkidle')
    refresh=page.get_by_role('button',name='데이터 확인',exact=True)
    refresh.click()
    expect(page.get_by_role('button',name='확인 중…',exact=True)).to_be_disabled()
    expect(page.get_by_role('button',name='데이터 확인',exact=True)).to_be_enabled()
    expect(page.locator('.stock-link:visible').first).to_be_visible()
    context.close();print('RADAR MANUAL REFRESH PASS')
    context=browser.new_context();page=context.new_page()
    page.goto(base+'/radar.html?market=US&tab=ai',wait_until='networkidle')
    expect(page.locator('#section-title')).to_have_text('AI 섹터')
    page.locator('#ai-overview [data-help=median]').click()
    expect(page.get_by_role('tooltip')).to_contain_text('가운데 두 값의 평균')
    expect(page.locator('[data-ai-sector=all]')).to_have_attribute('aria-pressed','true')
    page.get_by_role('button',name='200일',exact=True).click()
    expect(page.get_by_role('tooltip')).not_to_be_visible()
    page.locator('[data-market=KR]').click()
    expect(page.get_by_role('button',name='200일',exact=True)).to_have_attribute('aria-pressed','true')
    expect(page.locator('#section-title')).to_have_text('AI 섹터')
    expect(page.locator('#date-info')).to_contain_text(DATA['markets']['KR']['asOf'])
    assert 'tab=ai' in page.url
    context.close();print('RADAR AI DEEP LINK AND MARKET SWITCH PASS')
    context=browser.new_context();page=context.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(base+'/radar.html',wait_until='networkidle')
    page.evaluate("document.querySelector('#session-changes').remove();document.querySelector('#signal-filter').remove()")
    page.locator('[data-tab=ai]').click()
    expect(page.locator('.ai-sector-card')).to_have_count(AI_CARDS)
    expect(page.locator('.stock-link:visible').first).to_be_visible()
    page.locator('[data-tab=near]').click()
    expect(page.locator('.stock-link:visible').first).to_be_visible()
    assert not errors,errors
    context.close();print('RADAR PREVIOUS TEMPLATE RETENTION PASS')
    for name,market in [('index.html','KR'),('us.html','US')]:
        page=browser.new_page()
        page.goto(base+'/'+name,wait_until='domcontentloaded');page.wait_for_url(f'**/radar.html?market={market}')
        page.close();print('RADAR ENTRY PASS',market)
    browser.close()
server.shutdown()
