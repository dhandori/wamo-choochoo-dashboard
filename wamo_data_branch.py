#!/usr/bin/env python3
"""자료 브랜치(data)에 결과를 올리고 받아 온다.

자동 갱신이 만든 자료 파일이 main 기록에 쌓이지 않게, 코드는 main에 두고 자료는 data 브랜치에 둔다.
같은 파일을 두 작업이 동시에 바꾸려 하면 덮어쓰지 않고 멈춘다(예전 rebase 충돌 때와 같은 약속).

  setup                       data 브랜치를 받아 작업 폴더(main 코드 옆)에 자료를 덮어 놓는다
  publish [-m 메시지] 파일...  작업 폴더의 파일을 data 브랜치에 올린다
  prune [--keep-days 7]       data 브랜치 기록을 줄인다: 오늘(KST) 기록은 전부, 그 전 7일은 하루 마지막 1개씩,
                              그보다 오래된 기록은 한 덩어리로 합친다. 최신 자료 내용은 바뀌지 않는다

환경변수
  WAMO_DATA_REPO    data 브랜치를 받아 둘 폴더. setup 이 GITHUB_ENV 에 적어 이후 단계가 이어 쓴다
  WAMO_DATA_REMOTE  받아 올 주소(시험용). 없으면 GITHUB_REPOSITORY 와 토큰으로 만든다
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BRANCH = 'data'
ROOT = Path(__file__).resolve().parent
MIN_FILES = 20  # 자료가 이보다 적게 들어 있으면 받기에 실패한 것으로 본다
DEFAULT_MESSAGE = 'Update verified market data and refresh status'
IDENTITY = ['-c', 'user.name=wamo-dashboard-bot', '-c', 'user.email=actions@users.noreply.github.com']


def git(repo, *args, check=True):
    return subprocess.run(['git', *IDENTITY, '-C', str(repo), *args], check=check, capture_output=True, text=True)


def data_repo():
    path = os.environ.get('WAMO_DATA_REPO')
    if not path:
        raise RuntimeError('WAMO_DATA_REPO 가 없습니다. 먼저 setup 을 실행하세요')
    return Path(path)


def remote_url():
    url = os.environ.get('WAMO_DATA_REMOTE')
    if url:
        return url
    repo, token = os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not repo or not token:
        raise RuntimeError('GITHUB_REPOSITORY 와 GITHUB_TOKEN 이 필요합니다')
    return f'https://x-access-token:{token}@github.com/{repo}.git'


def tracked(repo):
    return [n for n in git(repo, 'ls-files', '-z').stdout.split('\0') if n]


def setup(dest=None, root=ROOT):
    """data 브랜치를 받아 두고, 그 파일들을 root 에 덮어 놓는다. 끝내 못 받으면 예외를 낸다."""
    dest = Path(dest or os.environ.get('WAMO_DATA_REPO') or tempfile.mkdtemp(prefix='wamo-data-'))
    last = ''
    for attempt in range(1, 4):
        shutil.rmtree(dest, ignore_errors=True)
        done = subprocess.run(['git', 'clone', '-q', '--depth=1', '--branch', BRANCH, remote_url(), str(dest)],
                              capture_output=True, text=True)
        if done.returncode == 0:
            break
        last = done.stderr.strip()
        for secret in (os.environ.get('GITHUB_TOKEN'), os.environ.get('GH_TOKEN')):
            if secret:
                last = last.replace(secret, '***')
        print(f'자료 브랜치 받기 실패({attempt}/3): {last}', flush=True)
        time.sleep(attempt * 5)
    else:
        raise RuntimeError(f'자료 브랜치를 받지 못했습니다: {last}')
    names = tracked(dest)
    if len(names) < MIN_FILES:
        raise RuntimeError(f'자료 브랜치에 파일이 {len(names)}개뿐입니다(최소 {MIN_FILES}개)')
    for name in names:
        target = Path(root) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dest / name, target)
    env_file = os.environ.get('GITHUB_ENV')
    if env_file:
        with open(env_file, 'a', encoding='utf-8') as f:
            f.write(f'WAMO_DATA_REPO={dest}\n')
    os.environ['WAMO_DATA_REPO'] = str(dest)
    print(f'자료 {len(names)}개를 data 브랜치({git(dest, "rev-parse", "--short", "HEAD").stdout.strip()})에서 받아 덮었습니다', flush=True)
    return dest


def publish(names, message=DEFAULT_MESSAGE, root=ROOT, retries=4):
    """root 의 names 를 data 브랜치에 올린다. 올렸으면 True, 바뀐 게 없으면 False."""
    repo = data_repo()
    names = sorted(set(n for n in names if (Path(root) / n).exists()))
    if not names:
        return False
    base = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    for name in names:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(root) / name, target)
    git(repo, 'add', '--', *names)
    if git(repo, 'diff', '--cached', '--quiet', check=False).returncode == 0:
        return False
    git(repo, 'commit', '-q', '-m', message)
    for attempt in range(retries):
        pushed = git(repo, 'push', 'origin', f'HEAD:{BRANCH}', check=False)
        if pushed.returncode == 0:
            return True
        print(f'data 브랜치 올리기 거절({attempt + 1}/{retries}): {pushed.stderr.strip()[-200:]}', flush=True)
        git(repo, 'fetch', '-q', '--depth=1', 'origin', BRANCH)
        # 내가 바꾸려는 파일을 그 사이에 다른 작업이 바꿨으면 덮어쓰지 않고 멈춘다
        # (내 push 가 서버에선 성공했는데 응답만 끊긴 경우처럼, 남은 내용이 내 파일과 같으면 충돌이 아니다)
        for name in names:
            theirs = git(repo, 'rev-parse', '-q', '--verify', f'FETCH_HEAD:{name}', check=False).stdout.strip()
            before = git(repo, 'rev-parse', '-q', '--verify', f'{base}:{name}', check=False).stdout.strip()
            mine = git(repo, 'hash-object', name).stdout.strip()
            if theirs != before and theirs != mine:
                raise RuntimeError('동시 수정 충돌: 원격 데이터를 덮어쓰지 않고 저장 중단')
        # 남이 올린 최신 위에 내 파일만 다시 얹는다(내 작업 폴더 파일은 그대로 두고 인덱스만 맞춘다)
        git(repo, 'reset', '-q', '--mixed', 'FETCH_HEAD')
        git(repo, 'add', '--', *names)
        if git(repo, 'diff', '--cached', '--quiet', check=False).returncode == 0:
            return False
        git(repo, 'commit', '-q', '-m', message)
        time.sleep(attempt * 3)
    raise RuntimeError('GitHub 결과 저장 재시도 실패')


KST = timezone(timedelta(hours=9))
KEEP_DAYS = 7


def plan_keep(commits, now, keep_days=KEEP_DAYS):
    """남길 기록을 고른다. commits = [(sha, 시각(초)), ...] 오래된 것부터.

    돌려주는 값: (합칠 기준 sha 또는 None, 남길 sha 목록). 기준은 남길 기록보다 앞선 마지막 기록으로,
    그 내용(그 시점의 자료 전체)을 첫 기록으로 삼는다. 남길 기록은 항상 최신 기록을 포함한다.
    - 오늘(KST) 기록: 전부(방금 생긴 문제를 바로 되돌리는 용도)
    - 어제부터 keep_days 일 전까지: 그날 마지막 기록 1개씩(되돌릴 때 필요한 건 그날 마지막 정상 상태)
    """
    today = datetime.fromtimestamp(now, KST).date()
    oldest = today - timedelta(days=keep_days)
    last_of_day = {}
    for sha, ts in commits:
        last_of_day[datetime.fromtimestamp(ts, KST).date()] = sha
    keep = []
    for sha, ts in commits:
        day = datetime.fromtimestamp(ts, KST).date()
        if day >= today or (day >= oldest and last_of_day[day] == sha):
            keep.append(sha)
    if commits and commits[-1][0] not in keep:
        keep.append(commits[-1][0])   # 시계가 어긋나도 최신 기록은 반드시 남긴다
    first = next(i for i, (sha, _) in enumerate(commits) if sha in keep) if keep else len(commits)
    base = commits[first - 1][0] if first > 0 else None
    return base, keep


def prune(now=None, keep_days=KEEP_DAYS, dest=None, retries=3):
    """data 브랜치 기록을 plan_keep 대로 줄여 올린다. 줄일 게 없으면 False.

    최신 기록의 내용은 그대로라서, 그 사이 다른 작업이 자료를 올려도 publish 의 재시도가 새 기록 위에 다시 얹는다.
    올리는 순간 누가 먼저 올렸으면(--force-with-lease) 다시 받아서 처음부터 한다.
    """
    dest = Path(dest or tempfile.mkdtemp(prefix='wamo-prune-'))
    for attempt in range(retries):
        shutil.rmtree(dest, ignore_errors=True)
        done = subprocess.run(['git', 'clone', '-q', '--single-branch', '--branch', BRANCH, remote_url(), str(dest)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError('자료 브랜치를 받지 못했습니다')
        old_head = git(dest, 'rev-parse', 'HEAD').stdout.strip()
        log = git(dest, 'log', '--first-parent', '--reverse', '--format=%H %ct').stdout.split()
        commits = [(log[i], int(log[i + 1])) for i in range(0, len(log), 2)]
        base, keep = plan_keep(commits, time.time() if now is None else now, keep_days)
        if base is None and len(keep) == len(commits):
            print(f'정리할 기록 없음(기록 {len(commits)}개)', flush=True)
            return False
        parent = None
        if base:
            day = datetime.fromtimestamp(dict(commits)[base], KST).strftime('%Y-%m-%d %H:%M')
            parent = git(dest, 'commit-tree', f'{base}^{{tree}}', '-m',
                         f'data 브랜치 정리: {day} KST 까지의 기록을 하나로 합침').stdout.strip()
        for sha in keep:
            meta = git(dest, 'log', '-1', '--format=%an%x00%ae%x00%aI%x00%cI%x00%B', sha).stdout.split('\0', 4)
            env = {**os.environ, 'GIT_AUTHOR_NAME': meta[0], 'GIT_AUTHOR_EMAIL': meta[1], 'GIT_AUTHOR_DATE': meta[2],
                   'GIT_COMMITTER_NAME': meta[0], 'GIT_COMMITTER_EMAIL': meta[1], 'GIT_COMMITTER_DATE': meta[3]}
            args = ['git', '-C', str(dest), 'commit-tree', f'{sha}^{{tree}}', '-m', meta[4].strip() or 'data']
            if parent:
                args[4:4] = ['-p', parent]
            parent = subprocess.run(args, check=True, capture_output=True, text=True, env=env).stdout.strip()
        if git(dest, 'rev-parse', f'{parent}^{{tree}}').stdout != git(dest, 'rev-parse', 'HEAD^{tree}').stdout:
            raise RuntimeError('정리한 최신 기록의 내용이 원래와 다릅니다(올리지 않음)')
        pushed = git(dest, 'push', f'--force-with-lease={BRANCH}:{old_head}', 'origin', f'{parent}:{BRANCH}', check=False)
        if pushed.returncode == 0:
            print(f'기록 {len(commits)}개 → {len(keep) + (1 if base else 0)}개로 정리', flush=True)
            return True
        print(f'정리 올리기 거절({attempt + 1}/{retries}): 그 사이 새 자료가 올라와 다시 합니다', flush=True)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError('data 브랜치 정리 재시도 실패')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('setup')
    p = sub.add_parser('publish')
    p.add_argument('-m', '--message', default=DEFAULT_MESSAGE)
    p.add_argument('names', nargs='+')
    q = sub.add_parser('prune')
    q.add_argument('--keep-days', type=int, default=KEEP_DAYS)
    a = ap.parse_args()
    try:
        if a.cmd == 'setup':
            setup()
        elif a.cmd == 'prune':
            prune(keep_days=a.keep_days)
        else:
            print('올림' if publish(a.names, a.message) else '바뀐 내용 없음', flush=True)
    except RuntimeError as exc:
        print(f'오류: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
