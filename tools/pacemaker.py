#!/usr/bin/env python3
"""정시 깨우개: 깃허브 예약(cron)이 몇 시간씩 밀려서(2026-11-19 측정 2~9시간), 정해진 시각에 이 저장소의
다른 작업을 직접(workflow_dispatch) 시작시킨다. 직접 시작은 밀리지 않는다.

한 번 실행은 최대 BUDGET 분 동안 다음 시각을 기다렸다 거는 일을 되풀이하고, 시간이 다 되면 자기 자신을
다시 걸어(이어 달리기) 끝난다. 사슬이 끊기면 예약 실행(백업)이 다시 잇는다.
일정표는 같은 폴더의 pacemaker.json 이다. 2026-11-20 사용자 결정(밀림 보강 B안, Claude 예약 '30 깃허브 깨우기'를 돕는 보조 장치).

일정표 한 줄: {"name": 이름, "at": "HH:MM", "tz": "Asia/Seoul"|"America/New_York", "days": [1..7](그 시간대의 요일, 1=월),
             "workflow": "작업.yml", "inputs": {키: 값}}
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
KST = ZoneInfo('Asia/Seoul')
LOOKBACK_MIN = 15      # 사슬이 새로 시작될 때 이만큼 지난 시각까지는 놓친 것으로 보고 건다


def load_table(path=None):
    rows = json.loads(Path(path or HERE / 'pacemaker.json').read_text(encoding='utf-8'))
    for r in rows:
        if r.get('_comment'):
            continue
        h, m = map(int, r['at'].split(':'))
        assert 0 <= h < 24 and 0 <= m < 60, r
        assert set(r['days']) <= set(range(1, 8)), r
        ZoneInfo(r['tz'])
        assert r['workflow'].endswith('.yml'), r
    return [r for r in rows if not r.get('_comment')]


def occurrences(table, start, end):
    """(start, end] 사이에 오는 일정 시각을 시간순으로 낸다. start·end 는 UTC aware."""
    out = []
    for r in table:
        tz = ZoneInfo(r['tz'])
        h, m = map(int, r['at'].split(':'))
        d = start.astimezone(tz).date() - timedelta(days=1)
        last = end.astimezone(tz).date()
        while d <= last:
            if d.isoweekday() in r['days']:
                t = datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(timezone.utc)
                if start < t <= end:
                    out.append((t, r))
            d += timedelta(days=1)
    out.sort(key=lambda x: (x[0], x[1]['workflow']))
    return out


def dispatch(workflow, inputs, dry=False):
    cmd = ['gh', 'workflow', 'run', workflow, '--ref', os.environ.get('PACEMAKER_REF', 'main')]
    for k, v in (inputs or {}).items():
        cmd += ['-f', f'{k}={str(v).lower() if isinstance(v, bool) else v}']
    if dry:
        print('  (시험) ' + ' '.join(cmd), flush=True)
        return True
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return True
        print(f'  걸기 실패({attempt + 1}/3): {r.stderr.strip()[:200]}', flush=True)
        time.sleep(10)
    return False


def kst(t):
    return t.astimezone(KST).strftime('%m/%d %H:%M KST')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--since', default='', help='앞 실행이 어디까지 걸었는지(유닉스 초). 비우면 지금부터 15분 전')
    ap.add_argument('--budget', type=int, default=330, help='이 실행이 기다릴 최대 분(작업 한도 6시간보다 짧게)')
    ap.add_argument('--self', default='pacemaker.yml', help='이어 달리기로 다시 걸 자기 작업 파일')
    ap.add_argument('--table')
    ap.add_argument('--plan', type=float, default=0, help='시험: 앞으로 이 시간(시간) 안의 일정만 보여 주고 끝냄')
    ap.add_argument('--dry', action='store_true', help='시험: 실제로 걸지 않음')
    a = ap.parse_args(argv)
    table = load_table(a.table)
    now = datetime.now(timezone.utc)
    if a.plan:
        for t, r in occurrences(table, now, now + timedelta(hours=a.plan)):
            print(f"{kst(t)}  {r['workflow']} {r.get('inputs') or ''}  {r['name']}")
        return 0
    cursor = (datetime.fromtimestamp(float(a.since), timezone.utc) if a.since
              else now - timedelta(minutes=LOOKBACK_MIN))
    cursor = max(cursor, now - timedelta(hours=2))      # 오래된 since 는 믿지 않는다(한꺼번에 몰아 걸지 않게)
    deadline = now + timedelta(minutes=a.budget)
    print(f'정시 깨우개 시작 {kst(now)} · {kst(cursor)} 뒤부터 {kst(deadline)} 까지 맡음', flush=True)
    for t, r in occurrences(table, cursor, deadline):
        wait = (t - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        ok = dispatch(r['workflow'], r.get('inputs'), a.dry)
        print(f"{kst(t)} {r['name']}: {'걸었음' if ok else '실패'} (실제 {kst(datetime.now(timezone.utc))})", flush=True)
        cursor = t
    rest = (deadline - datetime.now(timezone.utc)).total_seconds()
    if rest > 0:
        time.sleep(rest)
    cursor = max(cursor, deadline)
    ok = dispatch(a.self, {'since': f'{cursor.timestamp():.0f}'}, a.dry)
    print(f"이어 달리기 {'걸었음' if ok else '실패(백업 예약이 다시 잇는다)'} · since={kst(cursor)}", flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
