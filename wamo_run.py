"""Run due market updates, retain good data on failure, publish one commit."""
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys

from wamo_runtime import UTC, KST, latest_slot, next_slot, patch_status_ui, write_json

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'wamo_refresh_state.json'
STATUS = ROOT / 'wamo_refresh_status.json'
MARKETS = {
    'KR': ('wamo_update_business_dart.py', 'index.html', 'korea', ['wamo_business_profiles.json', 'wamo_consensus_history.json', 'wamo_kis_cache.json']),
    'US': ('wamo_update_us_sec.py', 'us.html', 'us', ['wamo_us_sec_cache.json']),
}
SHARED = ['movers.html', 'wamo_movers_cache.json']


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def select_targets(state, now, requested='auto', force=False):
    targets = []
    for market in MARKETS:
        if requested not in ('auto', 'both', market):
            continue
        slot = latest_slot(market, now)
        entry = state.get(market, {})
        same_slot = entry.get('slot') == slot.isoformat()
        # A delayed noon trigger may arrive after the close. Refresh the latest
        # due slot once; never fabricate the missed noon snapshot.
        if not force:
            if now - slot > timedelta(hours=12):
                continue
            if same_slot and (entry.get('success') or entry.get('attempts', 0) >= 3):
                continue
        targets.append((market, slot))
    return targets


def snapshot(names):
    return {name: (ROOT / name).read_bytes() if (ROOT / name).exists() else None for name in names}


def restore(saved):
    for name, content in saved.items():
        path = ROOT / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)


def run_script(script, args=(), env=None, timeout=1800):
    subprocess.run([sys.executable, '-u', script, *args], cwd=ROOT, env=env,
                   timeout=timeout, check=True)


def publish(names):
    names = sorted(set(name for name in names if (ROOT / name).exists()))
    subprocess.run(['git', 'add', '--', *names], cwd=ROOT, check=True)
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT).returncode == 0:
        return
    subprocess.run(['git', 'commit', '-m', 'Update verified market data and refresh status'], cwd=ROOT, check=True)
    for attempt in range(3):
        result = subprocess.run(['git', 'push', 'origin', 'HEAD:main'], cwd=ROOT)
        if result.returncode == 0:
            return
        subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=ROOT, check=True)
        rebased = subprocess.run(['git', 'rebase', 'origin/main'], cwd=ROOT)
        if rebased.returncode:
            subprocess.run(['git', 'rebase', '--abort'], cwd=ROOT, check=True)
            raise RuntimeError('동시 수정 충돌: 원격 데이터를 덮어쓰지 않고 저장 중단')
    raise RuntimeError('GitHub 결과 저장 재시도 실패')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['auto', 'both', 'KR', 'US'], default='auto')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    now = datetime.now(UTC)
    event = os.getenv('GITHUB_EVENT_NAME', '')
    force = event in ('workflow_dispatch', 'push') or args.market != 'auto'
    state, status = read_json(STATE), read_json(STATUS)
    targets = select_targets(state, now, args.market, force)
    print('갱신 대상:', [(market, slot.astimezone(KST).isoformat()) for market, slot in targets], flush=True)
    if args.plan or not targets:
        return
    changed = ['wamo_refresh_state.json', 'wamo_refresh_status.json', *SHARED]
    failures = []
    for market, slot in targets:
        script, page, movers_market, caches = MARKETS[market]
        old_entry = state.get(market, {})
        same = old_entry.get('slot') == slot.isoformat()
        entry = {'slot': slot.isoformat(), 'attempts': old_entry.get('attempts', 0) + 1 if same else 1, 'success': False}
        start = datetime.now(UTC)
        env = dict(os.environ, WAMO_RUN_STARTED_AT=start.isoformat(),
                   WAMO_SCHEDULED_FOR=slot.isoformat(), PYTHONUNBUFFERED='1')
        report = dict(status.get(market, {}), status='FAILED',
                      attemptedAt=start.isoformat(), scheduledFor=slot.isoformat(),
                      nextScheduledFor=next_slot(market, start).isoformat(),
                      trigger={'schedule': '예약실행', 'push': '코드 반영 후 실행',
                               'workflow_dispatch': '수동실행'}.get(event, '직접실행'),
                      runUrl=(f"https://github.com/{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
                              if os.getenv('GITHUB_RUN_ID') else None))
        saved = snapshot([page, *caches])
        try:
            run_script(script, env=env)
            import wamo_update_business_dart as core
            from wamo_runtime import validate_freshness
            data = core.extract_old_payload((ROOT / page).read_text(encoding='utf-8'))
            fresh = validate_freshness(data, market)
            warnings = list(fresh['warnings'])
            # Optional movers cannot prevent fresh main-dashboard data from saving.
            movers_saved = snapshot(SHARED)
            try:
                run_script('wamo_update_movers.py', ['--market', movers_market], env, timeout=900)
                report['moversStatus'] = 'PASS'
            except (subprocess.SubprocessError, RuntimeError):
                restore(movers_saved)
                warnings.append('상승률 TOP 30 갱신 실패 · 이전 결과 유지')
                report['moversStatus'] = 'FAILED'
            report.update(status='WARNING' if warnings else 'PASS', warnings=warnings,
                          lastSuccessAt=datetime.now(UTC).isoformat(), asOf=data['meta']['asOf'],
                          freshness=fresh, count=len(data['stocks']), error=None,
                          kis=data['meta'].get('kisMeta'))
            entry['success'] = True
            changed.extend([page, *caches])
        except (subprocess.SubprocessError, RuntimeError, ValueError, KeyError) as exc:
            restore(saved)
            # Child process logs contain the provider's diagnostic; don't copy
            # arbitrary exception bodies or authentication responses into the site.
            report['error'] = '가격 갱신 또는 최신성 검사 실패 · 이전 정상 데이터 유지 · 실행 로그 확인'
            failures.append(market)
            print(f'{market} FAILED: {type(exc).__name__}', flush=True)
        state[market], status[market] = entry, report
        print(market, report['status'], '가격 기준일', report.get('asOf'), flush=True)
    # Apply navigation/status fixes even to the market whose provider failed.
    for market, (_, page, _, _) in MARKETS.items():
        path = ROOT / page
        path.write_text(patch_status_ui(path.read_text(encoding='utf-8'), market), encoding='utf-8')
        changed.append(page)
    write_json(STATE, state)
    write_json(STATUS, status)
    summary = '\n'.join(f"- {m}: {status[m]['status']} · 가격 기준일 {status[m].get('asOf', '확인 불가')} · 예약 {status[m]['scheduledFor']}" for m, _ in targets)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary + '\n', encoding='utf-8')
    if args.publish:
        publish(changed)
    if failures:
        raise SystemExit('갱신 실패: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
