"""Validated, allowlisted bridge from private radar reports to public discovery data."""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from wamo_runtime import write_json, patch_status_ui
from wamo_runtime import expected_session

CALENDARS = {'US-AMEX': 'XNYS', 'US-NASDAQ': 'XNYS', 'US-NYSE': 'XNYS',
             'KR-KOSPI': 'XKRX', 'KR-KOSDAQ': 'XKRX', 'JP-TSE': 'XTKS',
             'HK-HKEX': 'XHKG', 'CN-SH': 'XSHG', 'CN-SZ': 'XSHG'}
SIGNAL_FIELDS = ('key', 'market', 'country', 'symbol', 'name', 'currency', 'as_of',
                 'industry_path', 'micro_verified', 'classification_status',
                 'classification_caveats', 'current_close', 'entry_status')
FLAGS = ('intraday_63', 'intraday_126', 'intraday_252', 'intraday_ath',
         'close_63', 'close_126', 'close_252', 'close_ath')


def only_52week(edition):
    """Filter the verified upstream release and recompute its displayed counts."""
    from collections import defaultdict
    signals, groups = [], defaultdict(set)
    for item in edition['signals']:
        flags = {key: item['flags'].get(key) for key in ('intraday_252', 'close_252')}
        if not any(value is True for value in flags.values()):
            continue
        signals.append({**item, 'flags': flags})
        groups[(item['market'].split('-')[0], tuple(item.get('industry_path') or []))].add(item['symbol'])
    industries = []
    for item in edition['industries']:
        count = len(groups[(item['market'], tuple(item.get('industry_path') or []))])
        if count:
            industries.append({**item, 'current_signal_issuers': count,
                               'phase': '52주 신고가 관측', 'comparison_scope': '52주 신고가 · 검증 종목군'})
    return {**edition, 'signals': signals, 'industries': industries}


def local_edition(payload, market, now):
    """Recompute actual 52-week highs from verified WAMO prices, no remote dependency."""
    from collections import defaultdict
    from wamo_price_sources import validate_history
    expected = expected_session(market, now)
    stocks = payload.get('stocks') or []
    fresh = [s for s in stocks if s.get('date') == expected and s.get('dataStatus') == 'LIVE']
    if payload.get('meta', {}).get('asOf') != expected or not stocks or len(fresh)/len(stocks) < .9:
        raise ValueError('local prices not current')
    signals, groups, sessions = [], defaultdict(lambda: [0, 0]), {}
    exclusions, validated, eligible = [], 0, 0
    seen = set()
    for stock in fresh:
        symbol = stock.get('ticker') if market == 'US' else stock.get('stock_code')
        if not symbol or symbol in seen:
            raise ValueError('local identity missing/duplicated')
        seen.add(symbol)
        try:
            rows = validate_history(stock.get('history') or [], expected)
            if abs(rows[-1]['close'] - float(stock['close'])) > .011:
                raise ValueError('local close/history mismatch')
        except (ValueError, KeyError, TypeError, OverflowError):
            exclusions.append(dict(symbol=symbol, reason='HISTORY_VALIDATION'))
            continue
        validated += 1
        if len(rows) < 252:
            continue
        eligible += 1
        rows = rows[-252:]
        exchange = stock.get('krx_market') if market == 'KR' else ('NASDAQ' if stock.get('exchange') == 'NASDAQ' else 'AMEX' if stock.get('exchange') == 'NYSE AMERICAN' else 'NYSE')
        code = market + '-' + exchange
        sessions[code] = expected.replace('-', '')
        sector = stock.get('sector') or '산업 미분류'
        group = groups[(sector,)]
        group[0] += 1
        flags = dict(intraday_252=rows[-1]['high'] >= max(r['high'] for r in rows),
                     close_252=rows[-1]['close'] >= max(r['close'] for r in rows))
        if not any(flags.values()):
            continue
        group[1] += 1
        signals.append(dict(key=code+':'+symbol, market=code, country=market,
            symbol=symbol, name=stock.get('name') or symbol,
            currency='KRW' if market == 'KR' else 'USD', as_of=expected.replace('-', ''),
            industry_path=[sector], micro_verified=False, classification_status='WAMO_SECTOR',
            classification_caveats=['WAMO 기존 업종 분류'], current_close=stock['close'],
            entry_status='52주 신고가 · 매매판단은 별도', flags=flags))
    if validated / len(stocks) < .9:
        raise ValueError('local verified coverage below 90%')
    industries = [dict(market=market, industry_path=list(path),
        eligible_classified_issuers=counts[0], current_signal_issuers=counts[1],
        micro_verified=False, phase='52주 신고가 관측', comparison_scope='WAMO 검증 종목군')
        for path, counts in groups.items() if counts[1]]
    return dict(status='PASS', generatedAt=now.isoformat(), sessions=sessions,
        source='WAMO_52W', scope=f'WAMO {market} 검증 종목군 · 최근 252거래일',
        coverage=dict(totalCount=len(stocks), currentCount=len(fresh), validatedCount=validated,
                      eligible52WeekCount=eligible, excludedCount=len(exclusions)),
        exclusions=exclusions,
        signals=signals, industries=industries)


def public_edition(report, status, expected):
    if (status.get('report_ready') is not True or status.get('release_policy_version') != 3
            or status.get('problems') or report.get('problems')
            or report.get('schema_version') != 1 or report.get('data_ready') is not True
            or report.get('analysis_ready') is not True):
        raise ValueError('report not verified')
    for field in ('release_key', 'analysis_fingerprint'):
        if not report.get(field) or report[field] != status.get(field):
            raise ValueError('release mismatch')
    dates = {m: s['date'] for m, s in report['expected_sessions'].items()}
    if dates != expected or set(report['coverage']) != set(expected):
        raise ValueError('session mismatch')
    for market, day in expected.items():
        coverage = report['coverage'][market]
        if (coverage.get('as_of') != day or not coverage.get('target')
                or coverage.get('price_confirmed') != coverage['target']):
            raise ValueError('coverage incomplete')
    signals, seen = [], set()
    for item in report['signals']:
        key = item['key']
        if (key in seen or item.get('status') != 'ok' or item.get('signal_active') is not True
                or item.get('as_of') != expected.get(item.get('market'))):
            raise ValueError('invalid candidate')
        seen.add(key)
        flags = {k: item.get('flags', {}).get(k) for k in FLAGS}
        if any(v is not None and type(v) is not bool for v in flags.values()):
            raise ValueError('invalid signal value')
        signals.append({**{k: item.get(k) for k in SIGNAL_FIELDS}, 'flags': flags})
    if len(signals) != report.get('signal_count'):
        raise ValueError('candidate count mismatch')
    industries = []
    for item in report['industries']:
        industries.append({k: item.get(k) for k in (
            'market', 'industry_path', 'eligible_classified_issuers', 'current_signal_issuers',
            'micro_verified', 'phase', 'comparison_scope')})
    return dict(status='PASS', generatedAt=report['generated_at_utc'], sessions=dates,
                releaseKey=report['release_key'], fingerprint=report['analysis_fingerprint'],
                signals=signals, industries=industries)


def expected_sessions(markets, now):
    import exchange_calendars as xc
    result = {}
    for market in markets:
        cal = xc.get_calendar(CALENDARS[market])
        if now.date() > cal.last_session.date():
            raise ValueError('calendar out of range')
        eligible = cal.schedule[cal.schedule['close'] <= now - timedelta(minutes=10)]
        result[market] = eligible.index[-1].strftime('%Y%m%d')
    return result


def fetch_json(path, token):
    for attempt in range(3):
        try:
            response = requests.get('https://api.github.com/repos/dhandori/global-high-radar/' + path,
                headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github.raw+json'},
                timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            if attempt == 2:
                raise RuntimeError('NETWORK') from None
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError('HTTP_' + str(response.status_code))
        time.sleep(2 ** attempt)


ERROR_LABELS = {
    'MISSING_TOKEN': '신고가 연동 인증 설정 없음',
    'HTTP_401': '신고가 연동 인증 만료 또는 오류',
    'HTTP_403': '신고가 원본 접근 권한 또는 호출 제한 확인 필요',
    'HTTP_404': '신고가 원본 파일 또는 접근 권한 확인 필요',
    'UPSTREAM_NOT_READY': '신고가 원본의 가격·분석 검증 대기',
    'VALIDATION': '신고가 자료 기준일·내용 검증 실패',
    'NETWORK': '신고가 원본 연결 실패',
}


def save_result(dest, result, previous, now):
    """No-op on identical output; keep a one-hour connection heartbeat."""
    same = {k: v for k, v in result.items() if k != 'checkedAt'} == {
        k: v for k, v in previous.items() if k != 'checkedAt'}
    if same:
        try:
            if now - datetime.fromisoformat(previous['checkedAt']) < timedelta(hours=1):
                return False
        except (KeyError, TypeError, ValueError):
            pass
    write_json(dest, result)
    return True


def main():
    token = os.environ.get('RADAR_READ_TOKEN')
    root = Path(__file__).resolve().parent
    dest = root / 'wamo_radar.json'
    previous = json.loads(dest.read_text(encoding='utf-8')) if dest.exists() else {}
    now = datetime.now(timezone.utc)
    editions = {}
    failed = False
    source_error = None
    try:
        # Pin every input to one immutable source commit; do not mix report versions.
        if not token:
            raise RuntimeError('MISSING_TOKEN')
        ref = fetch_json('git/refs/heads/main', token)['object']['sha']
    except Exception as exc:
        ref = None
        source_error = str(exc) if str(exc) in ERROR_LABELS else 'NETWORK'
    for edition in ('US', 'ASIA'):
        try:
            if not ref:
                raise RuntimeError(source_error)
            status = fetch_json(f'contents/data/radar_{edition.lower()}_release_status.json?ref={ref}', token)
            if status.get('report_ready') is not True:
                raise RuntimeError('UPSTREAM_NOT_READY')
            report = fetch_json(f'contents/reports/{edition.lower()}_latest.json?ref={ref}', token)
            required = {m for m in CALENDARS if m.startswith('US-') == (edition == 'US')}
            editions[edition] = only_52week(public_edition(report, status, expected_sessions(required, now)))
        except Exception as exc:
            code = str(exc) if str(exc) in ERROR_LABELS else 'VALIDATION'
            try:
                from wamo_update_business_dart import extract_old_payload
                market, page = ('US', 'us.html') if edition == 'US' else ('KR', 'index.html')
                payload = extract_old_payload((root/page).read_text(encoding='utf-8'))
                editions[edition] = local_edition(payload, market, now)
                editions[edition]['upstreamError'] = code
            except (ValueError, KeyError, TypeError, OSError, RuntimeError):
                failed = True
                prior = previous.get('editions', {}).get(edition, {})
                retained = only_52week({**prior, 'signals': prior.get('signals') or [],
                                       'industries': prior.get('industries') or []})
                editions[edition] = dict(retained,
                    status='FAILED', errorCode=code,
                    error=ERROR_LABELS[code] + ' · 이전 결과는 현재 신호가 아닙니다.')
        print(edition, editions[edition]['status'], editions[edition].get('errorCode', ''))
    save_result(dest, dict(schemaVersion=1, checkedAt=now.isoformat(), editions=editions,
                         scope='52주 신고가 기준. 원본 대기 시 WAMO 한국·미국 검증 종목군으로 대체합니다. 대체 결과는 일본·중국·홍콩을 포함하지 않습니다.'), previous, now)
    for page, market in (('index.html', 'KR'), ('us.html', 'US')):
        path = root / page
        original = path.read_text(encoding='utf-8')
        updated = patch_status_ui(original, market)
        if original != updated:
            temporary = path.with_suffix('.html.tmp')
            temporary.write_text(updated, encoding='utf-8')
            temporary.replace(path)
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
