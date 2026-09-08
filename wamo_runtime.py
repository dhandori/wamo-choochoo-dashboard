"""Shared refresh metadata and checks. No account or order access."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import json
import os
import re

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
FULL_SLOTS = {'KR': ((0, 30), (3, 0), (7, 0)), 'US': ((13, 40), (16, 0), (21, 20))}
SLOTS = {'KR': ((0, 30), (1, 30), (2, 30), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0)),
         'US': ((13, 40), (14, 40), (16, 0), (17, 0), (18, 0), (19, 0), (20, 0), (21, 20))}
SCHEDULE_LABEL = {'KR': '한국 09:30 / 10:30 / 11:30 / 12:00 / 13:00 / 14:00 / 15:00 / 16:00',
                  'US': '미국 22:40 / 23:40 / 01:00 / 02:00 / 03:00 / 04:00 / 05:00 / 06:20'}

def price_only_slot(market, slot):
    return (slot.hour, slot.minute) not in FULL_SLOTS[market]



@lru_cache(maxsize=2)
def calendar(market):
    import exchange_calendars as xc
    return xc.get_calendar('XKRX' if market == 'KR' else 'XNYS')


def expected_session(market, now=None):
    now = now or datetime.now(UTC)
    # Allow the daily data provider 20 minutes after the opening auction.
    schedule = calendar(market).schedule
    eligible = schedule[schedule['open'] <= now - timedelta(minutes=20)]
    if eligible.empty or now.date() > calendar(market).last_session.date():
        raise RuntimeError('거래일 달력 범위를 확인해야 합니다')
    return eligible.index[-1].date().isoformat()


def slots_near(market, now):
    for offset in range(-7, 8):
        day = now.date() + timedelta(days=offset)
        if calendar(market).is_session(day.isoformat()):
            for hour, minute in SLOTS[market]:
                yield datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def latest_slot(market, now):
    return max(s for s in slots_near(market, now) if s <= now)


def next_slot(market, now):
    return min(s for s in slots_near(market, now) if s > now)


def latest_full_slot(market, now):
    return max(s for s in slots_near(market, now) if s <= now and not price_only_slot(market, s))


def use_price_only(market, slot, entry):
    # A late trigger must still perform the full run it missed before an extra slot.
    completed = entry.get('lastFullSlot')
    if not completed and entry.get('success') and entry.get('slot'):
        previous = datetime.fromisoformat(entry['slot'])
        if not price_only_slot(market, previous):
            completed = previous.isoformat()
    return price_only_slot(market, slot) and completed == latest_full_slot(market, slot).isoformat()


def run_meta(market):
    event = os.getenv('GITHUB_EVENT_NAME', '')
    return {
        'market': market,
        'runTrigger': {'schedule': '예약실행', 'workflow_dispatch': '수동실행',
                       'push': '코드 반영 후 실행'}.get(event, '직접실행'),
        'runStartedAt': os.getenv('WAMO_RUN_STARTED_AT'),
        'scheduledFor': os.getenv('WAMO_SCHEDULED_FOR'),
        'runUrl': (f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                   f"{os.environ['GITHUB_RUN_ID']}" if os.getenv('GITHUB_RUN_ID') else None),
        'scheduledUpdateKst': SCHEDULE_LABEL[market],
        'refreshMode': 'PRICE' if os.getenv('WAMO_PRICE_ONLY') == '1' else 'FULL',
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
    current = sum(s['date'] >= expected and s.get('dataStatus') == 'LIVE' for s in stocks)
    pct = round(current / len(stocks) * 100, 1) if stocks else 0
    if pct < 90:
        raise RuntimeError(f'가격 최신성 부족: 기대 거래일 {expected}, 신규수집 {current}/{len(stocks)} ({pct}%). 기존 화면 유지')
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
    html = re.sub(r'\s*<a\b[^>]*href=["\x27](?:\./)?kkangto\.html["\x27][^>]*>.*?</a>', '', html, flags=re.S)
    html = html.replace('한국 12:00 / 16:00', '한국 09:30 / 12:00 / 16:00')
    for old in ('미국 00:00 / 05:00', '미국 00:00 / 06:20'):
        html = html.replace(old, '미국 22:40 / 01:00 / 06:20')
    html = html.replace('한국 09:30 / 12:00 / 16:00', SCHEDULE_LABEL['KR'])
    html = html.replace('미국 22:40 / 01:00 / 06:20', SCHEDULE_LABEL['US'])
    html = html.replace('시장별 하루 2회', '시장별 거래일 8회').replace('시장별 거래일 3회', '시장별 거래일 8회')
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


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(path)
