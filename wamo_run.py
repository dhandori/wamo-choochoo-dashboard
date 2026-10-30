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
INSIGHTS = ['radar.html', 'wamo_insights.json']
RADAR_WARNING = '52주 신고가 목록 갱신 미완료 · 가격 화면은 별도 확인'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def set_action_output(updated):
    output = os.getenv('GITHUB_OUTPUT')
    if output:
        with Path(output).open('a', encoding='utf-8') as stream:
            stream.write(f"updated={'true' if updated else 'false'}\n")


class PartialRegression(RuntimeError):
    """A missing-stock rerun produced fewer current prices than before."""


PARTIAL_AUTO_RETRIES = 2
PARTIAL_RETRY_GAP = timedelta(hours=2)


def missing_count(entry, report=None):
    """Stocks not refreshed in the completed close (state, or older status files)."""
    if entry.get('missingCount') is not None:
        return int(entry.get('missingCount') or 0)
    report = report or {}
    fresh = report.get('freshness') or {}
    try:
        if fresh.get('expectedSession') and report.get('asOf') == fresh.get('expectedSession'):
            return max(0, int(report.get('count')) - int(fresh.get('currentCount')))
    except (TypeError, ValueError):
        pass
    return 0


def partial_rerun_due(entry, now, force=False, recovery=False, report=None):
    """Whether a completed close with missing stocks should be collected again."""
    if not entry.get('success') or missing_count(entry, report) <= 0:
        return False
    if force:
        return True
    if not recovery or int(entry.get('partialAttempts') or 0) >= PARTIAL_AUTO_RETRIES:
        return False
    try:
        return now >= datetime.fromisoformat(entry['lastAttemptAt']) + PARTIAL_RETRY_GAP
    except (KeyError, TypeError, ValueError, OverflowError):
        return True


def build_trend(markets):
    """Optional trend signals; never blocks or rolls back verified prices."""
    try:
        from wamo_trend import build, OUTPUTS
        build(ROOT, markets=tuple(markets))
        return [name for name in OUTPUTS if (ROOT / name).exists()]
    except Exception as exc:  # noqa: BLE001
        print(f'추세 신호 생성 미완료 · 가격 자료는 정상 유지: {type(exc).__name__}', flush=True)
        return []


def build_sp500(market):
    """S&P 500 상승·하락 상위(특징주 탭 카드). 실패해도 가격 갱신은 그대로 둔다."""
    if market != 'US':
        return []
    try:
        from wamo_sp500_movers import build, OUTPUT, MEMBERS
        build(ROOT)
        return [n for n in (OUTPUT, MEMBERS) if (ROOT / n).exists()]
    except Exception as exc:  # noqa: BLE001
        print(f'S&P 500 상승·하락 상위 미완료 · 이전 결과 유지: {type(exc).__name__}: {exc}', flush=True)
        return []


def send_alerts(markets):
    try:
        from wamo_alerts import send_for_markets
        return send_for_markets(ROOT, markets)
    except Exception as exc:  # noqa: BLE001
        print(f'알림 전송 미완료: {type(exc).__name__}', flush=True)
        return []


def trend_stale_markets():
    """Markets whose trend file is missing or behind the verified price page."""
    try:
        trend = read_json(ROOT / 'wamo_trend.json').get('markets', {})
    except (OSError, ValueError, AttributeError):
        trend = {}
    stale = []
    for market, (_, page, _, _) in MARKETS.items():
        try:
            from wamo_update import extract_old_payload
            asof = (extract_old_payload((ROOT / page).read_text(encoding='utf-8')).get('meta') or {}).get('asOf')
            block = trend.get(market) or {}
            from wamo_trend import TREND_VERSION
            if asof and (block.get('asOf') != asof or block.get('version') != TREND_VERSION):
                stale.append(market)
        except OSError:
            continue
    return stale


def select_targets(state, now, requested='auto', force=False, recovery=False, status=None):
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
            # A "successful" close can still miss a few stocks. Re-collect those
            # on a manual run, or automatically at most twice, 2 hours apart.
            if partial_rerun_due(entry, now, force, recovery, (status or {}).get(market)):
                targets.append((market, slot))
            continue
        if same_slot:
            if entry.get('success'):
                continue
            # Failures remain recoverable. Back off, rather than permanently
            # exhausting a session's retry budget after one transient outage.
            attempted = entry.get('lastAttemptAt')
            if attempted and not force:
                try:
                    # A recovered provider must not wait up to six more hours
                    # just because earlier attempts failed. Limit collection
                    # to once per 30 minutes until this close is verified.
                    delay = 30
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


def refresh_insights():
    """Project prices locally, independently of optional network enrichments."""
    try:
        from wamo_publish_insights import publish_snapshot
        publish_snapshot(ROOT)
        return INSIGHTS
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        print('레이더 생성 미완료 · 이전 검증 자료 유지', flush=True)
        return []


def publish(names):
    if os.environ.get('WAMO_DATA_REPO'):
        import wamo_data_branch
        wamo_data_branch.publish(names)
        return
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
        if report.get('radarStatus') == 'RUNNING':
            report['radarStatus'] = 'FAILED'
            if RADAR_WARNING not in report.setdefault('warnings', []):
                report['warnings'].append(RADAR_WARNING)
            if report.get('status') == 'PASS':
                report['status'] = 'WARNING'
            changed = True
    if changed:
        write_json(STATE, state)
        write_json(STATUS, status)
        if do_publish:
            publish([STATE.name, STATUS.name])


def refresh_radar(status, markets):
    """Refresh discovery after verified prices; never roll prices back for radar."""
    try:
        run_script('wamo_radar.py', timeout=180)
    except (subprocess.SubprocessError, OSError, RuntimeError):
        print('신고가 갱신 작업 오류 · 시장별 결과 검증', flush=True)
    try:
        result = read_json(ROOT / 'wamo_radar.json')
        editions = result.get('editions', {})
        checked = datetime.fromisoformat(result.get('checkedAt', '').replace('Z', '+00:00'))
        fresh = timedelta(0) <= datetime.now(UTC) - checked <= timedelta(hours=3)
        if not isinstance(editions, dict):
            raise ValueError('invalid radar editions')
    except (ValueError, TypeError, OSError, AttributeError):
        editions, fresh = {}, False
    warning = RADAR_WARNING
    for market in markets:
        report = status[market]
        edition = editions.get('ASIA' if market == 'KR' else 'US') or {}
        if not isinstance(edition, dict):
            edition = {}
        session_map = edition.get('sessions') or {}
        sessions = [day for name, day in session_map.items()
                    if isinstance(name, str) and name.startswith(market + '-')] if isinstance(session_map, dict) else []
        expected = expected_session(market).replace('-', '')
        ready = fresh and edition.get('status') == 'PASS' and bool(sessions) and all(day == expected for day in sessions)
        report['radarStatus'] = 'PASS' if ready else 'FAILED'
        report['warnings'] = [w for w in report.get('warnings', []) if w != warning]
        if not ready:
            report['warnings'].append(warning)
        report['status'] = 'WARNING' if report['warnings'] or any(report.get(key) == 'FAILED' for key in ('moversStatus', 'catalogStatus')) else 'PASS'
    write_json(STATUS, status)


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
        retry_radar = report.get('radarStatus') in ('FAILED', 'RUNNING')
        if not (retry_movers or retry_catalog or retry_radar):
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
        if retry_radar:
            refresh_radar(status, [market])
            names.extend(['wamo_radar.json', *[v[1] for v in MARKETS.values()]])
        write_json(STATUS, status)
        names.extend(refresh_insights())
        if do_publish:
            publish(names)


def confirm_volume(slot, do_publish=False, builder=None, now=None):
    """Evening recheck of US index volume (Yahoo revises the first, preliminary figure later).

    Rebuilds only the US trend block, then records whether the latest session's volume
    was final by this check, so the page can say when it is usually confirmed.
    """
    from wamo_trend import volume_schedule
    now = now or datetime.now(UTC)
    trend_path, hist_path = ROOT / 'wamo_trend.json', ROOT / 'wamo_ftd_history.json'
    block = (read_json(trend_path).get('markets') or {}).get('US') or {}
    asof = block.get('asOf')
    history = read_json(hist_path) if hist_path.exists() else {}
    checks = history.get('_confirmChecks', {}).get('US', [])
    if not asof or any(c.get('session') == asof and c.get('confirmed') for c in checks):
        print('미국 거래량 확정 확인: 이미 확정 값으로 계산됨 · 건너뜀', flush=True)
        return []
    names = (builder or build_trend)(['US'])
    if not names:
        return []
    trend = read_json(trend_path)
    block = (trend.get('markets') or {}).get('US') or {}
    groups = (block.get('liquidity') or {}).get('groups') or []
    dist = (block.get('weather') or {}).get('distribution') or {}
    prelim = bool(dist.get('prelim') or any(g.get('volumePrelim') for g in groups))
    final = dist.get('confirmed') or next((g['volumeConfirmed'] for g in groups if g.get('volumeConfirmed')), None)
    record = {'session': block.get('asOf'), 'slot': slot, 'at': now.astimezone(KST).isoformat(timespec='minutes'),
              'confirmed': bool(final) and not prelim, 'scale': (final or {}).get('scale')}
    history = read_json(hist_path)
    log = [c for c in history.setdefault('_confirmChecks', {}).get('US', []) if not (c.get('session') == record['session'] and c.get('slot') == slot)]
    history['_confirmChecks']['US'] = (log + [record])[-40:]
    write_json(hist_path, history)
    block['volumeSchedule'] = volume_schedule(history['_confirmChecks']['US'])
    write_json(trend_path, trend)
    print(f"미국 거래량 확정 확인({slot}): {'확정됨' if record['confirmed'] else '아직 확정 전'}", flush=True)
    names = sorted(set(names + ['wamo_trend.json', 'wamo_ftd_history.json'] + refresh_insights()))
    if do_publish:
        publish(names)
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--market', choices=['auto', 'both', 'KR', 'US'], default='auto')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--recovery', action='store_true')
    parser.add_argument('--finalize-interrupted', action='store_true')
    parser.add_argument('--confirm-volume', choices=['first', 'second'])
    args = parser.parse_args()
    if args.confirm_volume:
        confirm_volume(args.confirm_volume, args.publish)
        return
    if args.finalize_interrupted:
        finalize_interrupted(args.publish)
        return
    now = datetime.now(UTC)
    event = os.getenv('GITHUB_EVENT_NAME', '')
    force = event == 'workflow_dispatch' or int(os.getenv('GITHUB_RUN_ATTEMPT', '1')) > 1
    # Explicit reruns use the updated checkout and can recover the latest close
    # even after 12 hours. Regular DST cron candidates still run only once.
    recovery = args.recovery or force or int(os.getenv('GITHUB_RUN_ATTEMPT', '1')) > 1
    state, status = read_json(STATE), read_json(STATUS)
    targets = select_targets(state, now, args.market, force, recovery=recovery, status=status)
    print('갱신 대상:', [(market, slot.astimezone(KST).isoformat()) for market, slot in targets], flush=True)
    if args.plan or not targets:
        if not args.plan and recovery:
            recover_auxiliary(state, status, now, args.market, args.publish)
        if not args.plan:
            # Trend signals are derived locally; catch up if a page moved on
            # without them (first deploy, or an earlier optional failure).
            stale = trend_stale_markets()
            if stale:
                names = build_trend(stale)
                if names:
                    # Also re-render the radar page so template updates appear.
                    names.extend(refresh_insights())
                if names and args.publish:
                    publish(names)
                    alert_files = send_alerts(stale)
                    if alert_files:
                        publish(alert_files)
        set_action_output(False)
        return
    changed = ['wamo_refresh_state.json', 'wamo_refresh_status.json', *SHARED]
    failures = []
    refreshed = []
    for market, slot in targets:
        script, page, movers_market, caches = MARKETS[market]
        old_entry = dict(state.get(market, {}))
        old_report = dict(status.get(market, {}))
        same = old_entry.get('slot') == slot.isoformat()
        old_missing = missing_count(old_entry, old_report)
        partial_rerun = bool(same and old_entry.get('success') and old_missing > 0)
        entry = {'slot': slot.isoformat(), 'attempts': old_entry.get('attempts', 0) + 1 if same else 1, 'success': False}
        if partial_rerun:
            entry['partialAttempts'] = int(old_entry.get('partialAttempts') or 0) + 1
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
            missing = max(0, len(data['stocks']) - int(fresh.get('currentCount', len(data['stocks']))))
            if partial_rerun and missing > old_missing:
                raise PartialRegression(missing)
            warnings = list(fresh['warnings'])
            report['moversStatus'] = 'RUNNING'
            report.update(status='WARNING' if warnings else 'PASS', warnings=warnings,
                          radarStatus='RUNNING',
                          lastSuccessAt=datetime.now(UTC).isoformat(), asOf=data['meta']['asOf'],
                          freshness=fresh, count=len(data['stocks']), error=None,
                          kis=data['meta'].get('kisMeta'))
            entry['success'] = True
            entry['lastFullSlot'] = slot.isoformat()
            entry['missingCount'] = missing
            if partial_rerun:
                report['partialRerun'] = f"누락 종목 재수집 · {old_missing} → {missing}종목"
            changed.extend([page, *caches])
            refreshed.append(market)
        except (PartialRegression, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, OSError) as exc:
            restore(saved)
            if partial_rerun:
                # The earlier close was already verified and published. Keep it,
                # record the attempt and stop after the retry budget.
                entry = {**old_entry, 'lastAttemptAt': entry['lastAttemptAt'],
                         'partialAttempts': entry['partialAttempts'], 'missingCount': old_missing}
                note = ('누락 종목 재수집 결과가 이전보다 나빠 이전 결과 유지' if isinstance(exc, PartialRegression)
                        else '누락 종목 재수집 실패 · 이전 정상 데이터 유지')
                report = {**old_report, 'partialRerun': note,
                          'warnings': [w for w in old_report.get('warnings', []) if not w.startswith('누락 종목 재수집')] + [note]}
                print(f'{market} 누락 재수집 미반영: {type(exc).__name__}', flush=True)
            else:
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
        insight_files = refresh_insights()
        changed.extend(insight_files)
        trend_files = build_trend([market]) if market in refreshed else []
        trend_files += build_sp500(market) if market in refreshed else []
        changed.extend(trend_files)
        if args.publish:
            publish([page, *caches, STATE.name, STATUS.name, *insight_files, *trend_files])
        if market in refreshed:
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
    summary = '\n'.join(f"- {m}: {status[m].get('status')} · 가격 기준일 {status[m].get('asOf', '확인 불가')} · 예약 {status[m].get('scheduledFor', '확인 불가')}" for m, _ in targets)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary + '\n', encoding='utf-8')
    if args.publish:
        # The catalog is auxiliary; a projection failure must not roll back or
        # prevent already validated prices from being served.
        successful = [market for market, _ in targets if state[market].get('success')]
        if successful:
            refresh_radar(status, successful)
            changed.append('wamo_radar.json')
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
        try:
            run_script('wamo_news.py', timeout=120)
            changed.append('wamo_news.json')
        except (subprocess.SubprocessError, OSError):
            print('관련 헤드라인 연결 미완료 · 가격 자료는 정상 유지', flush=True)
        changed.extend(refresh_insights())
        publish(changed)
        alert_files = send_alerts(refreshed) if refreshed else []
        if alert_files:
            publish(alert_files)
        # The other market's trend may still lag (e.g. first deploy).
        stale = trend_stale_markets()
        if stale:
            trend_files = build_trend(stale)
            if trend_files:
                publish(trend_files)
    set_action_output(any(state[market].get('success') for market, _ in targets))
    if failures:
        raise SystemExit('갱신 실패: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
