"""Validated, allowlisted bridge from private radar reports to public discovery data."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from wamo_runtime import write_json, patch_status_ui

CALENDARS = {'US-AMEX': 'XNYS', 'US-NASDAQ': 'XNYS', 'US-NYSE': 'XNYS',
             'KR-KOSPI': 'XKRX', 'KR-KOSDAQ': 'XKRX', 'JP-TSE': 'XTKS',
             'HK-HKEX': 'XHKG', 'CN-SH': 'XSHG', 'CN-SZ': 'XSHG'}
SIGNAL_FIELDS = ('key', 'market', 'country', 'symbol', 'name', 'currency', 'as_of',
                 'industry_path', 'micro_verified', 'classification_status',
                 'classification_caveats', 'current_close', 'entry_status')
FLAGS = ('intraday_63', 'intraday_126', 'intraday_252', 'intraday_ath',
         'close_63', 'close_126', 'close_252', 'close_ath')


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
    response = requests.get('https://api.github.com/repos/dhandori/global-high-radar/' + path,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github.raw+json'},
        timeout=60, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError('Radar read failed: HTTP ' + str(response.status_code))
    return response.json()


def main():
    token = os.environ.get('RADAR_READ_TOKEN')
    if not token:
        raise SystemExit('RADAR_READ_TOKEN is not configured')
    root = Path(__file__).resolve().parent
    dest = root / 'wamo_radar.json'
    previous = json.loads(dest.read_text(encoding='utf-8')) if dest.exists() else {}
    now = datetime.now(timezone.utc)
    editions = {}
    failed = False
    try:
        # Pin every input to one immutable source commit; do not mix report versions.
        ref = fetch_json('git/refs/heads/main', token)['object']['sha']
    except Exception:
        ref = None
    for edition in ('US', 'ASIA'):
        try:
            if not ref:
                raise RuntimeError('source unavailable')
            report = fetch_json(f'contents/reports/{edition.lower()}_latest.json?ref={ref}', token)
            status = fetch_json(f'contents/data/radar_{edition.lower()}_release_status.json?ref={ref}', token)
            required = {m for m in CALENDARS if m.startswith('US-') == (edition == 'US')}
            editions[edition] = public_edition(report, status, expected_sessions(required, now))
        except Exception:
            failed = True
            editions[edition] = dict(previous.get('editions', {}).get(edition, {}),
                status='FAILED', error='신고가 자료 연결·기준일·검증 확인 실패. 이전 결과는 현재 신호가 아닙니다.')
        print(edition, editions[edition]['status'])
    write_json(dest, dict(schemaVersion=1, checkedAt=now.isoformat(), editions=editions,
                         scope='확인된 종목군의 신고가 후보이며 전체 거래소 전수조사가 아닙니다.'))
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
