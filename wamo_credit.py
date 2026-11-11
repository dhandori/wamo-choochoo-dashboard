"""한국 빚투 자료: 신용융자 잔고와 반대매매 (금융투자협회 FreeSIS, 무료·매일).

2026-11-11 사용자 요청으로 데일리 마켓 레이더 '시장 날씨'에 '신용잔고와 반대매매' 카드를 더한다.

- 신용공여 잔고(STATSCU0100000070BO): 신용거래융자 전체·유가증권·코스닥 (백만 원)
- 위탁매매 미수금 대비 실제 반대매매(STATSCU0100000060BO): 미수금·실제 반대매매 금액·비중 (백만 원, %)

협회 사이트는 https 가 막혀 있어 http 로 받는다(2026-11-11 확인). 자료는 하루 늦게 올라온다.
받은 관측은 wamo_credit_KR.json 에 쌓아 두고(처음엔 4년 치, 협회 자료는 2008년부터 있다), 받기에 실패하면 쌓인 자료로 카드를 만든다.
반대매매 급증일 = 그날 금액이 직전 20거래일 평균의 1.5배 이상인 날.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
HISTORY = 'wamo_credit_KR.json'
URL = 'http://freesis.kofia.or.kr/meta/getMetaDataList.do'
CREDIT_OBJ = 'STATSCU0100000070BO'
FORCED_OBJ = 'STATSCU0100000060BO'
HISTORY_YEARS = 4             # 처음 받을 때 몇 년 치를 받을지(카드는 3년을 그린다)
SPIKE_RATIO = 1.5
AVG_DAYS = 20
WINDOW_DAYS = 365 * 3 + 2      # 카드에 싣는 기간(3년)
SOURCE = '금융투자협회 FreeSIS(신용공여 잔고 · 위탁매매 미수금 대비 실제 반대매매)'
SOURCE_URL = 'https://freesis.kofia.or.kr/'


def post(obj, start, end, opener=None, tries=5):
    body = json.dumps({'dmSearch': {'tmpV40': '1000000', 'tmpV41': '1', 'tmpV1': 'D',
                                    'tmpV45': start.strftime('%Y%m%d'), 'tmpV46': end.strftime('%Y%m%d'),
                                    'OBJ_NM': obj}}).encode()
    req = urllib.request.Request(URL, data=body, headers={'Content-Type': 'application/json',
                                                          'User-Agent': 'Mozilla/5.0'})
    last = None
    for i in range(tries):
        try:
            with (opener or urllib.request.urlopen)(req, timeout=60) as r:
                return json.loads(r.read().decode('utf-8')).get('ds1') or []
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(3 * (i + 1))
    raise RuntimeError(f'금융투자협회 자료 받기 실패: {type(last).__name__}')


def iso(d8):
    return f'{d8[:4]}-{d8[4:6]}-{d8[6:8]}'


def eok(v):
    """백만 원 → 억 원 (소수 첫째 자리)."""
    return None if v is None else round(float(v) / 100, 1)


def fetch_range(start, end, fetch=post):
    """({date: {...}}, [실패한 (시작, 끝)]) — 반년씩 나눠 받는다(한 번에 길게 받으면 응답이 잘린다).
    협회 서버가 가끔 연결을 끊어서, 실패한 구간은 돌려주고 다음 실행에서 다시 받는다."""
    rows, failed = {}, []
    cur = start
    while cur <= end:
        stop = min(end, date(cur.year, 6, 30) if cur.month <= 6 else date(cur.year, 12, 31))
        try:
            credit, forced = fetch(CREDIT_OBJ, cur, stop), fetch(FORCED_OBJ, cur, stop)
        except RuntimeError:
            failed.append((cur.isoformat(), stop.isoformat()))
            cur = stop + timedelta(days=1)
            continue
        for x in credit:
            rows.setdefault(iso(x['TMPV1']), {}).update(credit=eok(x.get('TMPV2')), kospi=eok(x.get('TMPV3')),
                                                       kosdaq=eok(x.get('TMPV4')))
        for x in forced:
            rows.setdefault(iso(x['TMPV1']), {}).update(misu=eok(x.get('TMPV5')), forced=eok(x.get('TMPV6')),
                                                       forcedPct=x.get('TMPV7'))
        cur = stop + timedelta(days=1)
    return rows, failed


def update_history(root, today=None, fetch=post):
    """쌓아 둔 자료에 새 관측을 더해 저장한다. 처음이면 4년 치를 받는다."""
    root = Path(root)
    path = root / HISTORY
    try:
        hist = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        hist = {}
    rows = {r['date']: r for r in hist.get('rows') or []}
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    ranges = [(today.replace(year=today.year - HISTORY_YEARS), today)] if len(rows) < 500 else [(today - timedelta(days=45), today)]
    ranges += [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in hist.get('pending') or []]
    pending = []
    for a, b in ranges:
        fresh, failed = fetch_range(a, b, fetch)
        pending += failed
        for d, v in fresh.items():
            rows[d] = {**rows.get(d, {}), 'date': d, **{k: x for k, x in v.items() if x is not None}}
    if not rows:
        raise RuntimeError('금융투자협회 자료를 하나도 받지 못함')
    recent_failed = any(b >= (today - timedelta(days=45)).isoformat() for a, b in pending)
    out = {'source': SOURCE, 'unit': '억 원(반대매매 비중은 %)', 'updatedAt': datetime.now(timezone.utc).isoformat(),
           'pending': sorted(set(pending)), 'rows': sorted(rows.values(), key=lambda r: r['date'])}
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    if recent_failed:
        print('신용잔고·반대매매 최근 자료 받기 실패 · 다음 실행에서 다시 받음', flush=True)
    return out


def card(hist, kospi=None):
    """화면 카드용 묶음. kospi: {date: 종가} (겹쳐 보기)."""
    rows = [r for r in hist.get('rows') or [] if r.get('credit') is not None or r.get('forced') is not None]
    if len(rows) < AVG_DAYS + 5:
        return {'status': 'UNAVAILABLE', 'note': '신용잔고·반대매매 자료가 아직 충분히 쌓이지 않았어요.'}
    series = []
    forced_hist = []
    for r in rows:
        f = r.get('forced')
        avg = sum(forced_hist[-AVG_DAYS:]) / AVG_DAYS if len(forced_hist) >= AVG_DAYS else None
        ratio = round(f / avg, 2) if f is not None and avg else None
        series.append({'date': r['date'], 'credit': r.get('credit'), 'creditKospi': r.get('kospi'),
                       'creditKosdaq': r.get('kosdaq'), 'forced': f, 'avg20': None if avg is None else round(avg, 1),
                       'ratio': ratio, 'spike': bool(ratio is not None and ratio >= SPIKE_RATIO - 1e-9),
                       'misu': r.get('misu'), 'forcedPct': r.get('forcedPct'),
                       'kospi': (kospi or {}).get(r['date'])})
        if f is not None:
            forced_hist.append(f)
    last = series[-1]
    cut = (date.fromisoformat(last['date']) - timedelta(days=WINDOW_DAYS)).isoformat()
    window = [p for p in series if p['date'] > cut]
    year_cut = (date.fromisoformat(last['date']) - timedelta(days=365)).isoformat()
    year = [p for p in window if p['date'] > year_cut]
    credits = [p for p in window if p['credit'] is not None]
    peak = max(credits, key=lambda p: p['credit']) if credits else None
    ago20 = series[-21] if len(series) > 20 else None
    return {
        'status': 'OK', 'asOf': last['date'], 'source': SOURCE, 'sourceUrl': SOURCE_URL,
        'spikeRatio': SPIKE_RATIO, 'avgDays': AVG_DAYS,
        'latest': last,
        'credit20dChangePct': (round((last['credit'] / ago20['credit'] - 1) * 100, 1)
                               if ago20 and ago20.get('credit') and last.get('credit') else None),
        'creditPeak': peak and {'date': peak['date'], 'credit': peak['credit']},
        'yearSpikeDays': sum(p['spike'] for p in year), 'yearDays': len(year),
        'recentSpikeDays': sum(p['spike'] for p in series[-20:]),
        'series': window,
        'note': '자료는 금융투자협회가 하루 늦게 올려요. 반대매매 금액은 코스피·코스닥을 합친 숫자예요.',
    }


NAVER_KOSPI = ('https://api.finance.naver.com/siseJson.naver?symbol=KOSPI&requestType=1'
               '&startTime={}&endTime={}&timeframe=day')


def naver_kospi(start, end):
    """{date: 종가} — 코스피 겹쳐 보기용(네이버 일봉)."""
    req = urllib.request.Request(NAVER_KOSPI.format(start.strftime('%Y%m%d'), end.strftime('%Y%m%d')),
                                 headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode('utf-8', 'replace')
    out = {}
    for line in text.splitlines():
        parts = [x.strip().strip("[]'\" ") for x in line.split(',')]
        if len(parts) >= 5 and parts[0].isdigit() and len(parts[0]) == 8:
            try:
                out[iso(parts[0])] = float(parts[4])
            except ValueError:
                pass
    return out


def build(root=None, kospi=None, fetch_network=True, fetch=post):
    root = Path(root or ROOT)
    hist = None
    kospi = dict(kospi or {})
    if fetch_network:
        try:
            today = datetime.now(timezone(timedelta(hours=9))).date()
            kospi = {**naver_kospi(today.replace(year=today.year - HISTORY_YEARS), today), **kospi}
        except Exception as exc:  # noqa: BLE001
            print(f'코스피 일봉(겹쳐 보기) 받기 실패: {type(exc).__name__}', flush=True)
        try:
            hist = update_history(root, fetch=fetch)
        except Exception as exc:  # noqa: BLE001
            print(f'신용잔고·반대매매 받기 실패 · 쌓인 자료로 표시: {exc}', flush=True)
    if hist is None:
        try:
            hist = json.loads((root / HISTORY).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'status': 'UNAVAILABLE', 'note': '신용잔고·반대매매 자료를 받지 못했어요. 다음 갱신 때 다시 받아요.'}
    return card(hist, kospi)


if __name__ == '__main__':
    out = build()
    print(json.dumps({k: v for k, v in out.items() if k != 'series'}, ensure_ascii=False, indent=1))
