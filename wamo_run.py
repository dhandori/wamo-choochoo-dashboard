"""Run due market updates, retain good data on failure, publish each market promptly."""
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys

from wamo_runtime import UTC, KST, MARKET_TZ, expected_session, latest_slot, next_slot, patch_status_ui, write_json

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'wamo_refresh_state.json'
STATUS = ROOT / 'wamo_refresh_status.json'
MARKETS = {
    'KR': ('wamo_update_business_dart.py', 'index.html', 'korea', ['wamo_business_profiles.json', 'wamo_consensus_history.json', 'wamo_kis_cache.json', 'wamo_kis_estimate_history.json']),
    'US': ('wamo_update_us_sec.py', 'us.html', 'us', ['wamo_us_sec_cache.json', 'wamo_kis_us_cache.json']),
}
SHARED = ['movers.html', 'wamo_movers_cache.json']


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def set_action_output(updated):
    output = os.getenv('GITHUB_OUTPUT')
    if output:
        with Path(output).open('a', encoding='utf-8') as stream:
            stream.write(f"updated={'true' if updated else 'false'}\n")


def select_targets(state, now, requested='auto', force=False, recovery=False):
    targets = []
    for market in MARKETS:
        if requested not in ('auto', 'both', market):
            continue
        slot = latest_slot(market, now)
        entry = state.get(market, {})
        same_slot = entry.get('slot') == slot.isoformat()
        if now - slot > timedelta(hours=12) and not recovery:
            continue
        session = slot.astimezone(MARKET_TZ[market]).date().isoformat()
        if expected_session(market, now) != session:
            continue
        completed_session = None
        if entry.get('success') and entry.get('slot'):
            try:
                completed_session = datetime.fromisoformat(entry['slot']).astimezone(
                    MARKET_TZ[market]).date().isoformat()
            except (TypeError, ValueError, OverflowError):
                pass
        if completed_session == session:
            continue
        if same_slot:
            if entry.get('success'):
                continue
            # Failures remain recoverable. Back off, rather than permanently
            # exhausting a session's retry budget after one transient outage.
            attempted = entry.get('lastAttemptAt')
            if attempted and not force:
                try:
                    delay = min(360, 30 * 2 ** min(max(int(entry.get('attempts', 1)) - 1, 0), 4))
                    if now < datetime.fromisoformat(attempted) + timedelta(minutes=delay):
                        continue
                except (TypeError, ValueError, OverflowError):
                    pass
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


def run_script(script, args=(), env=None, timeout=1500):
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


def finalize_interrupted(do_publish=False):
    """Persist an honest failure after a timed-out/cancelled collection step."""
    state, status = read_json(STATE), read_json(STATUS)
    changed = False
    for market, report in status.items():
        if report.get('status') == 'RUNNING':
            report.update(status='FAILED', error='실행 중단 또는 시간 초과 · 이전 정상 데이터 유지 · 자동 복구 대기')
            state.setdefault(market, {})['success'] = False
            changed = True
        if report.get('moversStatus') == 'RUNNING':
            report['moversStatus'] = 'FAILED'
            report.setdefault('warnings', []).append('상승률 TOP 30 실행 중단 · 이전 결과 유지')
            if report.get('status') == 'PASS':
                report['status'] = 'WARNING'
            changed = True
    if changed:
        write_json(STATE, state)
        write_json(STATUS, status)
        if do_publish:
            publish([STATE.name, STATUS.name])


def recover_auxiliary(state, status, now, requested, do_publish):
    """Retry only failed auxiliary outputs of a completed, still-current close."""
    for market, (_, _, movers_market, _) in MARKETS.items():
        entry, report = state.get(market, {}), status.get(market, {})
        if requested not in ('auto', 'both', market) or not entry.get('success'):
            continue
        slot = latest_slot(market, now)
        if entry.get('slot') != slot.isoformat() or expected_session(market, now) != slot.astimezone(MARKET_TZ[market]).date().isoformat():
            continue
        retry_movers = report.get('moversStatus') in ('FAILED', 'RUNNING')
        retry_catalog = report.get('catalogStatus') == 'FAILED'
        if not (retry_movers or retry_catalog):
            continue
        try:
            if now - datetime.fromisoformat(entry['auxLastAttemptAt']) < timedelta(hours=2):
                continue
        except (KeyError, TypeError, ValueError):
            pass
        entry['auxLastAttemptAt'] = now.isoformat()
        write_json(STATE, state)
        if do_publish:
            publish([STATE.name])
        names = [STATE.name, STATUS.name]
        for needed, key, script, arguments, outputs, warning in (
            (retry_movers, 'moversStatus', 'wamo_update_movers.py', ['--market', movers_market],
             [*SHARED, *[v[1] for v in MARKETS.values()]], '상승률 TOP 30'),
            (retry_catalog, 'catalogStatus', 'wamo_catalog.py', [], ['wamo_catalog.json'], '종목 검색 목록'),
        ):
            if not needed:
                continue
            saved = snapshot(outputs)
            try:
                run_script(script, arguments, timeout=600 if key == 'moversStatus' else 120)
                report[key] = 'PASS'
                report['warnings'] = [w for w in report.get('warnings', []) if not w.startswith(warning)]
                if key == 'moversStatus':
                    report['moversCompletedAt'] = datetime.now(UTC).isoformat()
                names.extend(outputs)
            except (subprocess.SubprocessError, RuntimeError, ValueError, OSError):
                restore(saved)
                report[key] = 'FAILED'
            report['status'] = 'WARNING' if report.get('warnings') or any(report.get(k) == 'FAILED' for k in ('moversStatus', 'catalogStatus')) else 'PASS'
        write_json(STATUS, status)
        if do_publish:
            publish(names)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['auto', 'both', 'KR', 'US'], default='auto')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--recovery', action='store_true')
    parser.add_argument('--finalize-interrupted', action='store_true')
    args = parser.parse_args()
    if args.finalize_interrupted:
        finalize_interrupted(args.publish)
        return
    now = datetime.now(UTC)
    event = os.getenv('GITHUB_EVENT_NAME', '')
    force = event == 'workflow_dispatch'
    # Explicit reruns use the updated checkout and can recover the latest close
    # even after 12 hours. Regular DST cron candidates still run only once.
    recovery = args.recovery or force or int(os.getenv('GITHUB_RUN_ATTEMPT', '1')) > 1
    state, status = read_json(STATE), read_json(STATUS)
    targets = select_targets(state, now, args.market, force, recovery=recovery)
    print('갱신 대상:', [(market, slot.astimezone(KST).isoformat()) for market, slot in targets], flush=True)
    if args.plan or not targets:
        if not args.plan and recovery:
            recover_auxiliary(state, status, now, args.market, args.publish)
        set_action_output(False)
        return
    changed = ['wamo_refresh_state.json', 'wamo_refresh_status.json', *SHARED]
    failures = []
    for market, slot in targets:
        script, page, movers_market, caches = MARKETS[market]
        old_entry = state.get(market, {})
        same = old_entry.get('slot') == slot.isoformat()
        entry = {'slot': slot.isoformat(), 'attempts': old_entry.get('attempts', 0) + 1 if same else 1, 'success': False}
        start = datetime.now(UTC)
        entry['lastAttemptAt'] = start.isoformat()
        env = dict(os.environ, WAMO_RUN_STARTED_AT=start.isoformat(),
                   WAMO_SCHEDULED_FOR=slot.isoformat(), WAMO_PRICE_ONLY='0', PYTHONUNBUFFERED='1')
        report = dict(status.get(market, {}), status='RUNNING', error=None,
                      attemptedAt=start.isoformat(), scheduledFor=slot.isoformat(),
                      refreshMode='FULL',
                      nextScheduledFor=next_slot(market, start).isoformat(),
                      trigger={'schedule': '예약실행', 'push': '코드 반영 후 실행',
                               'workflow_dispatch': '수동실행'}.get(event, '직접실행'),
                      runUrl=(f"https://github.com/{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
                              if os.getenv('GITHUB_RUN_ID') else None))
        saved = snapshot([page, *caches])
        # Persist before the child starts so a killed runner does not disappear
        # without an attempt record. Never publish unvalidated child output.
        state[market], status[market] = entry, report
        write_json(STATE, state)
        write_json(STATUS, status)
        if args.publish:
            publish([STATE.name, STATUS.name])
        try:
            run_script(script, env=env)
            import wamo_update_business_dart as core
            from wamo_runtime import validate_freshness
            data = core.extract_old_payload((ROOT / page).read_text(encoding='utf-8'))
            fresh = validate_freshness(data, market)
            warnings = list(fresh['warnings'])
            report['moversStatus'] = 'RUNNING'
            report.update(status='WARNING' if warnings else 'PASS', warnings=warnings,
                          lastSuccessAt=datetime.now(UTC).isoformat(), asOf=data['meta']['asOf'],
                          freshness=fresh, count=len(data['stocks']), error=None,
                          kis=data['meta'].get('kisMeta'))
            entry['success'] = True
            entry['lastFullSlot'] = slot.isoformat()
            changed.extend([page, *caches])
        except (subprocess.SubprocessError, RuntimeError, ValueError, KeyError, OSError) as exc:
            restore(saved)
            report['status'] = 'FAILED'
            # Child process logs contain the provider's diagnostic; don't copy
            # arbitrary exception bodies or authentication responses into the site.
            report['error'] = '가격 갱신 또는 최신성 검사 실패 · 이전 정상 데이터 유지 · 실행 로그 확인'
            failures.append(market)
            print(f'{market} FAILED: {type(exc).__name__}', flush=True)
        state[market], status[market] = entry, report
        print(market, report['status'], '가격 기준일', report.get('asOf'), flush=True)
        # Publish each validated market before slow optional movers or the other market.
        path = ROOT / page
        path.write_text(patch_status_ui(path.read_text(encoding='utf-8'), market), encoding='utf-8')
        write_json(STATE, state)
        write_json(STATUS, status)
        if args.publish:
            publish([page, *caches, STATE.name, STATUS.name])
        if entry['success']:
            movers_saved = snapshot(SHARED)
            try:
                run_script('wamo_update_movers.py', ['--market', movers_market], env, timeout=600)
                report['moversStatus'] = 'PASS'
                report['moversCompletedAt'] = datetime.now(UTC).isoformat()
            except (subprocess.SubprocessError, RuntimeError, ValueError, OSError):
                restore(movers_saved)
                report['warnings'].append('상승률 TOP 30 갱신 실패 · 이전 결과 유지')
                report['moversStatus'] = 'FAILED'
                report['status'] = 'WARNING'
            write_json(STATUS, status)
            if args.publish:
                # Finish this market before the next checkpoint. Leaving these
                # tracked files dirty makes a concurrent push's rebase unsafe.
                publish([*SHARED, STATUS.name, *[v[1] for v in MARKETS.values()]])
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
        # The catalog is auxiliary; a projection failure must not roll back or
        # prevent already validated prices from being served.
        try:
            run_script('wamo_catalog.py', timeout=120)
            changed.append('wamo_catalog.json')
            for market, _ in targets:
                status[market]['catalogStatus'] = 'PASS'
            write_json(STATUS, status)
        except (subprocess.SubprocessError, OSError):
            for market, _ in targets:
                status[market]['catalogStatus'] = 'FAILED'
                status[market].setdefault('warnings', []).append('종목 검색 목록 갱신 실패 · 가격 화면은 별도 확인')
                if status[market]['status'] == 'PASS':
                    status[market]['status'] = 'WARNING'
            write_json(STATUS, status)
        publish(changed)
    set_action_output(any(state[market].get('success') for market, _ in targets))
    if failures:
        raise SystemExit('갱신 실패: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
