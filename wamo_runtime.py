"""Shared refresh metadata and checks. No account or order access."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import re

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
NEW_YORK = ZoneInfo('America/New_York')
# Logical market-local publication times. Two US UTC cron candidates preserve
# 16:20 America/New_York across daylight-saving transitions.
SLOTS = {'KR': ((16, 0),), 'US': ((16, 20),)}
MARKET_TZ = {'KR': KST, 'US': NEW_YORK}
SCHEDULE_LABEL = {'KR': '한국 16:00',
                  'US': '미국 뉴욕 16:20 이후 (한국 05:20/06:20)'}



@lru_cache(maxsize=2)
def calendar(market):
    import exchange_calendars as xc
    return xc.get_calendar('XKRX' if market == 'KR' else 'XNYS')


def expected_session(market, now=None):
    now = now or datetime.now(UTC)
    # A session becomes publishable only after its official close plus buffer.
    schedule = calendar(market).schedule
    eligible = schedule[schedule['close'] <= now - timedelta(minutes=20)]
    if eligible.empty or now.date() > calendar(market).last_session.date():
        raise RuntimeError('거래일 달력 범위를 확인해야 합니다')
    return eligible.index[-1].date().isoformat()


def slots_near(market, now):
    # Search actual sessions, not ±7 calendar days: Korean holiday closures
    # can be longer, and must not block the other market's recovery.
    sessions = calendar(market).sessions
    local_day = now.astimezone(MARKET_TZ[market]).date().isoformat()
    position = sessions.searchsorted(local_day)
    for session in sessions[max(0, position - 2):position + 3]:
        day = session.date()
        for hour, minute in SLOTS[market]:
            local = datetime(day.year, day.month, day.day, hour, minute,
                             tzinfo=MARKET_TZ[market])
            yield local.astimezone(UTC)


def latest_slot(market, now):
    return max(s for s in slots_near(market, now) if s <= now)


def next_slot(market, now):
    return min(s for s in slots_near(market, now) if s > now)


def run_meta(market):
    event = os.getenv('GITHUB_EVENT_NAME', '')
    return {
        'market': market,
        'highScreenBasis': '52_WEEK',
        'runTrigger': {'schedule': '예약실행', 'workflow_dispatch': '수동실행',
                       'push': '코드 반영 후 실행'}.get(event, '직접실행'),
        'runStartedAt': os.getenv('WAMO_RUN_STARTED_AT'),
        'scheduledFor': os.getenv('WAMO_SCHEDULED_FOR'),
        'runUrl': (f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                   f"{os.environ['GITHUB_RUN_ID']}" if os.getenv('GITHUB_RUN_ID') else None),
        'scheduledUpdateKst': SCHEDULE_LABEL[market],
        'refreshMode': 'FULL',
        'priceDateLabel': '가격 기준일' if market == 'KR' else '미국 현지 가격 기준일',
    }


def validate_freshness(payload, market, now=None):
    now = now or datetime.now(UTC)
    stocks = payload.get('stocks') or []
    expected = expected_session(market, now)
    local_day = now.astimezone(KST if market == 'KR' else __import__('zoneinfo').ZoneInfo('America/New_York')).date().isoformat()
    invalid = [s for s in stocks if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(s.get('date', '')))
               or s['date'] > local_day]
    if invalid:
        raise RuntimeError('가격 날짜 형식 또는 미래 날짜 오류')
    current = sum(s['date'] == expected and s.get('dataStatus') == 'LIVE' for s in stocks)
    pct = round(current / len(stocks) * 100, 1) if stocks else 0
    if pct < 90:
        from collections import Counter
        dates = dict(Counter(f"{s['date']} / {s.get('dataStatus')}" for s in stocks))
        raise RuntimeError(f'가격 최신성 부족: 기대 거래일 {expected}, 신규수집 {current}/{len(stocks)} ({pct}%). 기존 화면 유지 · 날짜별 상태 {dates}')
    warnings = []
    meta = payload['meta']
    energy = meta.get('marketEnergy') or {}
    if energy.get('status') != 'LIVE':
        warnings.append('시장 에너지 확인 불가: ' + str(energy.get('fallbackReason') or energy.get('regimeNote') or '자료 없음'))
    if current < len(stocks):
        warnings.append(f'당일 신규수집 제외 {len(stocks) - current}종목 · 종목별 날짜 확인')
    kis = meta.get('kisMeta') or {}
    if kis.get('status') in ('PARTIAL', 'FAILED', 'AUTH_FAILED'):
        warnings.append('한국투자 보조조회 일부 실패 · 가격 갱신과 별도 확인')
    if market == 'US' and not (meta.get('secMeta') or {}).get('connected'):
        warnings.append('SEC 직접연결 안 됨 · Nasdaq 대체자료와 미확인 공시 구분')
    result = {'status': 'PASS', 'expectedSession': expected, 'currentCount': current,
              'currentPct': pct, 'checkedAt': now.astimezone(KST).isoformat(timespec='seconds'),
              'warnings': warnings, 'calendar': 'XKRX' if market == 'KR' else 'XNYS'}
    meta['freshness'] = result
    return result


def patch_status_ui(html, market):
    html = patch_52week_ui(html)
    if 'window.WAMO_OPEN_DETAIL' not in html:
        html = html.replace('function openDetail(x){',
            "window.WAMO_OPEN_DETAIL = ticker => { const stock = data.stocks.find(s => s.ticker === ticker); if (!stock) return false; openDetail(stock); return true; };\nfunction openDetail(x){", 1)
    html = re.sub(r'\s*<a\b[^>]*href=["\x27](?:\./)?kkangto\.html["\x27][^>]*>.*?</a>', '', html, flags=re.S)
    for old in ('한국 12:00 / 16:00', '한국 09:30 / 12:00 / 16:00',
                '한국 09:30 / 10:30 / 11:30 / 12:00 / 13:00 / 14:00 / 15:00 / 16:00'):
        html = html.replace(old, SCHEDULE_LABEL['KR'])
    for old in ('미국 00:00 / 05:00', '미국 00:00 / 06:20',
                '미국 22:40 / 01:00 / 06:20',
                '미국 22:40 / 23:40 / 01:00 / 02:00 / 03:00 / 04:00 / 05:00 / 06:20'):
        html = html.replace(old, SCHEDULE_LABEL['US'])
    html = re.sub(r'시장별 (?:하루|거래일) \d+회', '시장별 거래일 1회', html)
    html = html.replace('미국 현지 장 마감 기준일', '미국 현지 가격 기준일')
    # Replace the whole legacy assignment; previously the US variant evaded the patch.
    html = re.sub(r"\$\('#qualitySummary'\)\.textContent\s*=\s*`[^`]*`;",
                  "$('#qualitySummary').textContent='표시 정합성 검사 · 갱신 상태 확인 중';", html)
    html = html.replace('${data.meta.successCount||0}/${data.meta.eligibleUniverseCount||data.meta.universeCount||0}개 기업·리츠 정밀계산',
                        '정밀계산 ${data.meta.successCount||0}개 · 기업·리츠 ${data.meta.eligibleUniverseCount||data.meta.universeCount||0}개 중 시총·유동성 기준 통과')
    marker = '<script src="wamo_status.js" defer></script>'
    if marker not in html:
        html = html.replace('</body>', marker + '\n</body>')
    return html


def patch_52week_ui(html):
    """Keep existing pages/templates on the user's 52-week-only screen."""
    html = html.replace('52주 또는 역사적 고점 대비 7% 이내', '52주 고점 대비 7% 이내 · 252거래일 이상')
    html = html.replace('52주 또는 역사적 신고가권', '52주 신고가권')
    html = html.replace('현재가가 52주 또는 전체 수집이력 고점의 93% 이상', '252거래일 이상 · 현재가가 52주 고점의 93% 이상')
    html = re.sub(r'<details><summary>역사적 신고가권</summary>.*?</details>',
        '<details><summary>52주 신고가권</summary><p>최근 252거래일 고점에서 7% 이내인 종목을 선별합니다. 252거래일 미만의 신규 상장주는 이 선별에서 제외합니다. 추세 지표에는 최근 약 3년의 가격을 사용합니다.</p></details>', html, flags=re.S)
    html = html.replace('52주 고점 또는 전체 수집이력의 역사적 고점 중 하나라도 현재가가 7% 이내면 해당합니다. 목록에는 52주·역사적·둘 다를 구분해 표시합니다.',
        '최근 252거래일 고점 대비 현재가가 7% 이내인 종목입니다. 실제 신고가 돌파와 고점 부근 후보를 구분합니다.')
    html = html.replace('역사적 고점比', '52주 고점까지 거리')
    html = html.replace("return {h52:(x.high52Ratio||0)>=93,hist:(x.historicalHighRatio||0)>=93};",
        "return {h52:(x.high52WindowDays||Math.min(252,(x.history||[]).length))>=252&&(x.high52Ratio||0)>=93,hist:false};")
    html = html.replace("    if(h.h52&&h.hist) return '52주·역사적';\n", '')
    html = html.replace("    if(h.hist) return '역사적';\n", '')
    html = html.replace("$('#dHistoricalHigh').textContent=x.historicalHighRatio==null?'—':`${fmt(x.historicalHighRatio,1)}%`;",
        "$('#dHistoricalHigh').textContent=x.high52Ratio==null?'—':`${fmt(Math.max(0,100-x.high52Ratio),1)}%`;")
    html = html.replace("$('#dHistoryRange').textContent=x.historyStartDate?`${x.historyStartDate}부터 · 고점 ${x.historicalHighDate||'—'}`:'전체 이력 갱신 필요';",
        "$('#dHistoryRange').textContent=highDays<252?'252거래일 미만 · 신고가 선별 제외':'최근 252거래일 장중 고점 기준';")
    html = html.replace('Math.max(b.high52Ratio||0,b.historicalHighRatio||0)-Math.max(a.high52Ratio||0,a.historicalHighRatio||0)',
        '(b.high52Ratio||0)-(a.high52Ratio||0)')
    return html


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(path)
