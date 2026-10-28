"""Real-payload radar interaction, mobile, and offline-retention smoke checks."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
DATA=json.loads((ROOT/'wamo_insights.json').read_text())
TREND=json.loads((ROOT/'wamo_trend.json').read_text())
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

STEP_OF={'trend':'market','ftd':'market','leaders':'sector','industries':'sector','ai':'sector','near':'stock','high':'stock','high60':'stock','movers':'stock','patterns':'stock'}
def go_tab(page,tab):
    # 2026-10-28 3단계 탭: 휴대폰은 고른 단계의 탭만 보이므로 다른 단계 탭은 단계 칸을 먼저 누른다.
    b=page.locator(f'[data-tab={tab}]')
    if not b.is_visible():page.locator(f'[data-step-go={STEP_OF[tab]}]').click()
    b.click()

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
            go_tab(page,'near')
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
            go_tab(page,'high')
            highs=[s for s in rows if s['intradayHigh52'] or s['closeHigh52']]
            expect(page.locator('#pagination')).to_contain_text(f'{len(highs)}종목')
            page.get_by_role('button',name='장중 고점',exact=True).click()
            expect(page.locator('#pagination')).to_contain_text(f'{sum(s["intradayHigh52"] is True for s in rows)}종목')
            go_tab(page,'movers')
            page.get_by_role('button',name='하락 ≤−3%',exact=True).click()
            expect(page.locator('#pagination')).to_contain_text(f'{sum(s["changePct"] is not None and s["changePct"]<=-3 for s in rows)}종목')
            page.screenshot(path=str(OUT/f'radar-{market}-{width}-movers.png'),full_page=False)
            go_tab(page,'industries')
            if page.locator('.industry-card').count():
                page.locator('.industry-card').first.click()
                expect(page.get_by_role('button',name='← 전체 산업')).to_be_visible()
                expect(page.locator('.stock-link:visible').first).to_be_visible()
            go_tab(page,'near')
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
                go_tab(page,'near')
            go_tab(page,'ai')
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
                expect(page.locator(f'#detail .ret-tile[data-key="return{days}d"]')).to_be_visible()  # 2026-10-28: 수익률은 작은 칸 6개 묶음
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
            go_tab(page,'near')
            dimensions=page.evaluate('({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth})')
            assert dimensions['scroll']<=dimensions['client'],dimensions
            assert not errors,errors
            # Earlier steps opened the sort panel; measure the default layout, which is
            # folded at every width since 2026-10-28 (it cost 147px on desktop).
            page.evaluate("document.querySelectorAll('.sort-fold').forEach(n=>n.open=false)")
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
            if width==1440:assert first_y-notice_height-weather_height<800,first_y  # 2026-10-19 layout: two-row tabs; 2026-10-28 3단계 탭 머리줄(단계 이름·설명) 한 줄 추가 · 정렬 패널은 기본 접힘
            else:assert first_y-notice_height-weather_height<1100,first_y  # phones: industry-flow strip sits above the list since 2026-10-19; 2026-10-23 grouped tabs and metric bars add ~70px
            if (ROOT/'wamo_trend.json').exists():
                go_tab(page,'trend')
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
                # 2026-10-28: 탭 줄이 화면 위에 고정되므로 하위 탭 줄은 그 바로 아래에 붙는다.
                tabs_h=page.locator('.tabs').bounding_box()['height']
                assert tabs_h-1<=bar['y']<=tabs_h+2,(bar,tabs_h)
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
        go_tab(page,'movers');expect(page.locator('.stock-link:visible').first).to_be_visible()
        go_tab(page,'ai');expect(page.locator('.stock-link:visible').first).to_be_visible()
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
    go_tab(page,'ai')
    expect(page.locator('.ai-sector-card')).to_have_count(AI_CARDS)
    expect(page.locator('.stock-link:visible').first).to_be_visible()
    go_tab(page,'near')
    expect(page.locator('.stock-link:visible').first).to_be_visible()
    assert not errors,errors
    context.close();print('RADAR PREVIOUS TEMPLATE RETENTION PASS')
    # 스크리닝 대상 기준: 213종목이 시장 전체가 아니라는 것을 화면에서 읽을 수 있어야 한다.
    for market in ('KR','US'):
        context=browser.new_context();page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'{base}/radar.html?market={market}&tab=near',wait_until='networkidle')
        page.locator('#coverage-summary').click()
        rules=page.locator('#coverage-detail .universe-rule')
        universe=DATA['markets'][market]['coverage']['universe']
        expect(rules.first).to_be_visible()
        first=rules.first.inner_text()
        # 임계값은 스냅샷에서 읽어야 한다. 화면 코드에 숫자를 적으면 수집 기준이 바뀔 때 어긋난다.
        if market=='KR':
            assert '시총 1조원 이상' in first,first
            assert '100억원 이상' in first,first
        else:
            assert '10억 달러 이상' in first,first
            assert '1,000만 달러 이상' in first,first
        # 단계 줄은 숫자가 실제로 줄어드는 단계만 담는다.
        texts=rules.all_inner_texts()
        if len(texts)>1:
            shown=[int(_re.sub(r'[^0-9]','',part)) for part in texts[1].split('→')]
            assert shown==sorted(shown,reverse=True) and len(set(shown))==len(shown),texts[1]
            assert shown[-1]==universe['steps'][-1]['count'],(shown,universe['steps'])
        assert not errors,errors
        context.close();print('RADAR UNIVERSE RULES PASS',market)
    # 일본·대만은 수집 단계가 퍼널을 남기지 않는다. 줄을 비우고 조용히 넘어가야 한다.
    context=browser.new_context();page=context.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(f'{base}/radar.html?market=JP&tab=near',wait_until='networkidle')
    page.locator('#coverage-summary').click()
    assert page.locator('#coverage-detail .universe-rule').count()==0
    assert not errors,errors
    context.close();print('RADAR UNIVERSE RULES ABSENT PASS JP')
    # 패턴 셋업: 셋업북 12개 패턴 중 진행 중인 것만 카드로 보여 준다. 두 시장 모두 확인한다.
    for market in ('US','KR'):
        context=browser.new_context();page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'{base}/radar.html?market={market}&tab=patterns',wait_until='networkidle')
        summary=DATA['markets'][market]['patternSummary']
        expect(page.locator('#section-title')).to_have_text('패턴 셋업')
        expect(page.locator('[data-tab=patterns]')).to_have_class(_re.compile(r'\bactive\b'))
        # 셋업북 ①-1: 시장 방향이 먼저다. 시장 판단 띠가 목록보다 위에 있고 날씨 등급을 따른다.
        gate=page.locator('.pt-gate')
        expect(gate).to_be_visible()
        level=TREND['markets'][market]['weather']['level']
        expect(gate).to_have_class(_re.compile(r'\blevel-'+level.lower()+r'\b'))
        assert gate.inner_text().strip(),'시장 판단 띠가 비었습니다'
        # 지수 이름은 ftd 블록의 label 을 그대로 쓴다('지수'로 떨어지면 키가 바뀐 것이다).
        fresh=[i['label'] for i in TREND['markets'][market]['ftd']['indexes']
               if i.get('status')=='OK' and i.get('asOf')==DATA['markets'][market]['expectedSession']]
        for label in fresh: expect(gate).to_contain_text(label)
        # 띠가 목록보다 먼저 나와야 한다.
        assert gate.evaluate("(g)=>{const t=document.querySelector('.pt-top');return !t||g.compareDocumentPosition(t)&Node.DOCUMENT_POSITION_FOLLOWING}")
        page.locator('.pt-gate-go').click()
        expect(page.locator('#section-title')).to_have_text('추세 신호')
        go_tab(page,'patterns')
        # 단계 칸은 ③ 종목 찾기여야 한다.
        expect(page.locator('#section-step')).to_have_text('③ 종목 찾기')
        assert page.locator('.pt-stat').count()==4,page.locator('.pt-stat').count()
        cards=page.locator('.pt-card').count()
        if summary['withSetup']:
            assert cards>0,f'{market}: 셋업 {summary["withSetup"]}종목인데 카드가 없습니다'
            first=page.locator('.pt-card').first
            # 카드에는 셋업 이름, 단계 배지, 숫자가 있어야 한다.
            assert first.locator('.pt-setup h4').inner_text().strip(),'셋업 이름이 비었습니다'
            assert first.locator('.pt-phase').inner_text().strip() in ('돌파','임박','형성중')
            expect(first.locator('.pt-numbers dd').first).to_be_visible()
            # 패턴 설명·판정 기준·한계가 있어야 한다(근거 없는 신호를 내보내지 않는다).
            first.locator('.pt-why summary').click()
            assert first.locator('.pt-why dt').first.inner_text()=='패턴 설명'
            texts=first.locator('.pt-why dd').all_inner_texts()
            assert len(texts)>=3 and all(t.strip() and t.strip()!='—' for t in texts[:3]),texts
            first.locator('.pt-why summary').click()
            # 셋업북의 한 줄 논리는 접지 않고 보인다.
            expect(first.locator('.pt-logic')).to_be_visible()
            # 패턴 이름을 누르면 풀이 팝업이 뜬다: 한 줄 논리 + 모양·판정 기준·한계.
            name_btn=first.locator('.pt-setup h4 .term-help')
            name_btn.click()
            tip=page.locator('#term-tooltip')
            expect(tip).to_be_visible()
            assert tip.locator('strong').inner_text().startswith(name_btn.inner_text()),tip.locator('strong').inner_text()
            assert tip.locator('p.term-section b').all_inner_texts()==['어떤 모양인가','판정 기준','한계']
            check_help_bounds(page)
            name_btn.click()
            expect(tip).to_be_hidden()
            # 네이버·트레이딩뷰 링크는 스냅샷 주소 그대로, 새 탭으로 연다.
            shown=[s for s in DATA['markets'][market]['stocks'] if s.get('patterns')]
            card_links=first.locator('.pt-link')
            assert card_links.count()>=1,'외부 링크가 없습니다'
            for i in range(card_links.count()):
                a=card_links.nth(i)
                assert a.get_attribute('target')=='_blank' and 'noopener' in (a.get_attribute('rel') or '')
                href=a.get_attribute('href')
                assert any(href in (s.get('naverUrl'),s.get('tradingviewUrl')) for s in shown),href
            assert any('tradingview.com' in (card_links.nth(i).get_attribute('href') or '') for i in range(card_links.count()))
            # 종목 이름을 누르면 상세가 열린다.
            first.locator('.pt-name').click()
            expect(page.locator('#drawer')).to_be_visible()
            page.locator('#close-drawer').click()
        # 셋업 설명 상자: 셋업 수만큼 항목이 있고, 필터를 눌러도 펼친 상태가 유지된다.
        page.locator('.pt-guide summary').click()
        assert page.locator('.pt-guide-item').count()==len(DATA['patternMeta'])
        page.locator('.pt-chip', has_text='손절 8% 이내만').click()
        assert page.locator('.pt-guide').evaluate('(n)=>n.open')
        page.locator('.pt-chip', has_text='손절 8% 이내만').click()
        page.locator('.pt-guide summary').click()
        # 단계 칩으로 좁히면 결과 줄의 종목 수가 바뀐다.
        before=page.locator('.pt-result').inner_text()
        page.locator('.pt-chip.phase-watch').first.click()
        assert page.locator('.pt-chip.phase-watch.active').count()==1
        after=page.locator('.pt-result').inner_text()
        assert before!=after or summary['withSetup']==0,(before,after)
        # 셋업북 ①-6: 동반 강세 업종 필터. 걸러낸 뒤에는 모든 카드에 배지가 있어야 한다.
        page.locator('.pt-chip', has_text='동반 강세 업종만').click()
        shown=page.locator('.pt-card').count()
        assert page.locator('.pt-lead').count()==shown,(page.locator('.pt-lead').count(),shown)
        page.locator('.pt-chip', has_text='동반 강세 업종만').click()
        # 2단계 조건을 끄면 대상이 늘거나 같아야 한다(줄어들 수는 없다).
        page.locator('.pt-chip', has_text='전체').first.click()
        wide_before=int(_re.search(r'(\d+)',page.locator('.pt-result').inner_text()).group(1))
        page.locator('.pt-chip', has_text='2단계 종목만').click()
        wide_after=int(_re.search(r'(\d+)',page.locator('.pt-result').inner_text()).group(1))
        assert wide_after>=wide_before,(wide_before,wide_after)
        assert not errors,errors
        assert 'tab=patterns' in page.url
        context.close();print('RADAR PATTERNS PASS',market,f'cards={cards}')
    # 일본·대만은 셋업 판정을 제공하지 않는다. 빈 화면 대신 그 사실을 적어야 한다.
    context=browser.new_context();page=context.new_page()
    page.goto(f'{base}/radar.html?market=JP&tab=patterns',wait_until='networkidle')
    expect(page.locator('#content .empty')).to_contain_text('한국과 미국')
    context.close();print('RADAR PATTERNS LITE NOTICE PASS')
    for name,market in [('index.html','KR'),('us.html','US')]:
        page=browser.new_page()
        page.goto(base+'/'+name,wait_until='domcontentloaded');page.wait_for_url(f'**/radar.html?market={market}')
        page.close();print('RADAR ENTRY PASS',market)
    browser.close()
server.shutdown()
