"""미국 빚투 자료: FINRA 신용융자 잔고(월별) + S&P 500 투매일(매일).

2026-11-11 사용자 요청. 한국 '신용잔고와 반대매매' 카드의 미국판이다.
- 미국은 반대매매(마진콜 강제 매도) 금액을 공개하는 곳이 없다. 그래서 막대 자리에는
  지수 자료로 계산한 '투매일'을 그린다: S  지수 자료로 계산한 '투매일'을 그린다: S&P 500이 하루 2% 이상 내리고, 거래량이P 500이 하루 2% 이상 내리고, 거래량이
  직전 20거래일 평균의 1.2배 이상인 날. 반대매매를 직접 잰 숫자가 아니라는 점을 카드에 적는다.
- 신용융자 잔고는 FINRA 'Debit Balances in Customers' Securities Margin Accounts'(백만 달러, 매월,
  약 3~6주 늦게 발표). 받은 값은 wamo_credit_US.json 에 남겨, 받기에 실패하면 그 값으로 그린다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import io
import json
import re
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
HISTORY = 'wamo_credit_US.json'
FINRA_URL = 'https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx'
YAHOO_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=5y&interval=1d'
UA = {'User-Agent': 'Mozilla/5.0'}
DROP_PCT = -2.0
VOL_RATIO = 1.2   # 1.5배면 3년에 하루뿐이라(2025-04-04), 2024-08-05·2025-04·2026-11-05 급락이 잡히는 1.2배로 정함
AVG_DAYS = 20
WINDOW_DAYS = 365 * 3 + 2
SOURCE = 'FINRA 신용융자 잔고(매월) · S&P 500 일봉(야후)'
SOURCE_URL = 'https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics'


def fetch_bytes(url, tries=3, timeout=60):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(4 * (i + 1))
    raise RuntimeError(f'받기 실패: {type(last).__name__}')


def finra_margin(raw=None):
    """[[YYYY-MM, 백만 달러]] 오래된 순. 엑셀(xlsx)을 표준 라이브러리로 읽는다."""
    z = zipfile.ZipFile(io.BytesIO(raw if raw is not None else fetch_bytes(FINRA_URL)))
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % ns['m'])))
    out = {}
    for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).iter('{%s}row' % ns['m']):
        cells = {}
        for c in row.findall('m:c', ns):
            col = re.match(r'[A-Z]+', c.get('r')).group()
            v = c.find('m:v', ns)
            if v is None:
                t = c.find('m:is/m:t', ns)
                cells[col] = t.text if t is not None else None
            else:
                cells[col] = shared[int(v.text)] if c.get('t') == 's' else v.text
        ym, debit = cells.get('A'), cells.get('B')
        if ym and re.fullmatch(r'\d{4}-\d{2}', str(ym).strip()) and debit:
            out[ym.strip()] = float(debit)
    if len(out) < 100:
        raise RuntimeError('FINRA 자료가 너무 적음')
    return sorted([k, v] for k, v in out.items())


def yahoo_spx(raw=None):
    """[{date, close, volume}] 오래된 순 (뉴욕 날짜)."""
    j = json.loads(raw if raw is not None else fetch_bytes(YAHOO_URL, timeout=30))
    r = j['chart']['result'][0]
    q = r['indicators']['quote'][0]
    out = []
    for i, t in enumerate(r.get('timestamp') or []):
        c, v = q['close'][i], q['volume'][i]
        if c is None:
            continue
        d = (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=5)).date().isoformat()
        out.append({'date': d, 'close': round(c, 2), 'volume': v})
    return out


def card(margin, spx):
    """화면 카드용 묶음. margin: [[YYYY-MM, 백만 달러]], spx: [{date, close, volume}]."""
    if len(spx) < AVG_DAYS + 5 or not margin:
        return {'status': 'UNAVAILABLE', 'note': '미국 신용잔고·투매일 자료가 아직 충분하지 않아요.'}
    series, vols = [], []
    for i, r in enumerate(spx):
        prev = spx[i - 1]['close'] if i else None
        chg = round((r['close'] / prev - 1) * 100, 2) if prev else None
        avg = sum(vols[-AVG_DAYS:]) / AVG_DAYS if len(vols) >= AVG_DAYS else None
        ratio = round(r['volume'] / avg, 2) if avg and r.get('volume') else None
        flush = bool(chg is not None and ratio is not None and chg <= DROP_PCT + 1e-9 and ratio >= VOL_RATIO - 1e-9)
        series.append({'date': r['date'], 'close': r['close'], 'changePct': chg, 'volRatio': ratio, 'flush': flush})
        if r.get('volume'):
            vols.append(r['volume'])
    last = series[-1]
    cut = (date.fromisoformat(last['date']) - timedelta(days=WINDOW_DAYS)).isoformat()
    window = [p for p in series if p['date'] > cut]
    year_cut = (date.fromisoformat(last['date']) - timedelta(days=365)).isoformat()
    year = [p for p in window if p['date'] > year_cut]
    by_month = {ym: v for ym, v in margin}
    months = [[ym, v] for ym, v in margin if ym >= cut[:7]]
    ym, level = margin[-1]
    y_ago = by_month.get(f'{int(ym[:4]) - 1}{ym[4:]}')
    m_ago = margin[-2][1] if len(margin) > 1 else None
    recent12 = margin[-12:]
    peak = max(recent12, key=lambda x: x[1])
    flushes = [p for p in window if p['flush']]
    return {
        'status': 'OK', 'asOf': last['date'], 'source': SOURCE, 'sourceUrl': SOURCE_URL,
        'dropPct': DROP_PCT, 'volRatio': VOL_RATIO, 'avgDays': AVG_DAYS,
        'margin': {'month': ym, 'level': level, 'yoyPct': round((level / y_ago - 1) * 100, 1) if y_ago else None,
                   'momPct': round((level / m_ago - 1) * 100, 1) if m_ago else None,
                   'peak12': {'month': peak[0], 'level': peak[1]},
                   'fromPeakPct': round((level / peak[1] - 1) * 100, 1)},
        'months': months,
        'lastFlush': flushes[-1] if flushes else None,
        'yearFlushDays': sum(p['flush'] for p in year), 'yearDays': len(year),
        'recentFlushDays': sum(p['flush'] for p in series[-20:]),
        'series': window,
        'note': ('미국은 반대매매(마진콜 강제 매도) 금액을 공개하지 않아요. 막대는 S&P 500 하락폭이고, '
                 '주황 막대(투매일)는 지수 자료로 계산한 대신 보는 숫자예요. 신용잔고는 FINRA가 한 달에 한 번, '
                 '3~6주 늦게 발표해요.'),
    }


def build(root=None, fetch_network=True):
    root = Path(root or ROOT)
    try:
        hist = json.loads((root / HISTORY).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        hist = {}
    margin, spx = hist.get('margin') or [], hist.get('spx') or []
    if fetch_network:
        try:
            margin = finra_margin()
        except Exception as exc:  # noqa: BLE001
            print(f'FINRA 신용잔고 받기 실패 · 저장된 값 사용: {exc}', flush=True)
        try:
            spx = yahoo_spx()
        except Exception as exc:  # noqa: BLE001
            print(f'S&P 500 일봉 받기 실패 · 저장된 값 사용: {exc}', flush=True)
        if margin or spx:
            (root / HISTORY).write_text(json.dumps({'source': SOURCE, 'updatedAt': datetime.now(timezone.utc).isoformat(),
                                                    'margin': margin, 'spx': spx}, separators=(',', ':')) + '\n',
                                        encoding='utf-8')
    return card(margin, spx)


if __name__ == '__main__':
    out = build()
    print(json.dumps({k: v for k, v in out.items() if k not in ('series', 'months')}, ensure_ascii=False, indent=1))
