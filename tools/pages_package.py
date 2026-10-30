#!/usr/bin/env python3
"""GitHub Pages 배포 꾸러미를 만들고 지금 사이트와 비교한다.

사이트를 바꾸지 않는다. 읽고, 복사하고, 비교만 한다.
자료 분리 1단계: 새 배포 방식이 지금 사이트와 같은 파일을 내보내는지 확인하는 용도다.

  build   SRC DEST --manifest M     사이트에 올라갈 파일만 DEST에 복사하고 목록(M)을 남긴다
  compare SRC M URL --report R      M의 파일을 URL의 실제 사이트와 하나씩 견준다

올라갈 파일은 지금 사이트(브랜치에서 바로 내보내는 Pages)와 같게 맞춘다. 그 방식은
Jekyll을 거치며 이름이 점(.)이나 밑줄(_)로 시작하는 경로를 숨긴다.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HIDDEN_TOP = {'Gemfile', 'Gemfile.lock', 'node_modules', 'vendor'}
HISTORY_DEPTH = 40
WORKERS = 6


def is_hidden(path):
    parts = path.split('/')
    return parts[0] in HIDDEN_TOP or any(p.startswith(('.', '_')) for p in parts)


def git(src, *args):
    return subprocess.run(['git', '-C', src, *args], check=True, capture_output=True).stdout


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def cmd_build(a):
    names = [n for n in git(a.src, 'ls-files', '-z').decode('utf-8').split('\0') if n]
    files = sorted(n for n in names if not is_hidden(n))
    skipped = sorted(n for n in names if is_hidden(n))
    dest = os.path.abspath(a.dest)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    manifest, frontmatter = {}, []
    for p in files:
        s, d = os.path.join(a.src, p), os.path.join(dest, p)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(s, d)
        with open(s, 'rb') as f:
            if f.read(4) in (b'---\n', b'---\r'):
                frontmatter.append(p)
        manifest[p] = {'size': os.path.getsize(s), 'sha256': sha256_file(s)}
    head = git(a.src, 'rev-parse', 'HEAD').decode().strip()
    with open(a.manifest, 'w', encoding='utf-8') as f:
        json.dump({'source_commit': head, 'files': manifest}, f, ensure_ascii=False)
    total = sum(m['size'] for m in manifest.values())
    print(f'꾸러미: {len(files)}개 파일, {total / 1e6:.1f}MB (원본 커밋 {head[:7]})')
    print(f'숨김 처리 {len(skipped)}개: ' + ', '.join(skipped[:12]) + (' ...' if len(skipped) > 12 else ''))
    if frontmatter:
        print('주의: Jekyll이 가공할 수 있는 파일(맨 위가 ---):', ', '.join(frontmatter))


def fetch(base, path):
    url = f'{base.rstrip("/")}/{urllib.parse.quote(path)}?cb={int(time.time())}'
    last = ''
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={'Cache-Control': 'no-cache', 'User-Agent': 'wamo-pages-dryrun'})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
            return {'status': 200, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {'status': 404}
            last = f'HTTP {e.code}'
        except Exception as e:  # 네트워크 일시 오류
            last = f'{type(e).__name__}: {e}'
        time.sleep(2 * (i + 1))
    return {'status': 'error', 'detail': last}


def past_versions(src, ref, path):
    """ref에서 이 파일이 최근에 가졌던 내용들(최신순): (짧은 커밋, 시각, 해시)."""
    try:
        shas = git(src, 'log', '-n', str(HISTORY_DEPTH), '--format=%H', ref, '--', path).decode().split()
    except subprocess.CalledProcessError:
        return []
    versions = []
    for sha in shas:
        try:
            blob = git(src, 'show', f'{sha}:{path}')
        except subprocess.CalledProcessError:
            continue  # 그 커밋에서 지워진 파일
        when = git(src, 'show', '-s', '--format=%cI', sha).decode().strip()
        versions.append((sha[:7], when, hashlib.sha256(blob).hexdigest()))
    return versions


def cmd_compare(a):
    with open(a.manifest, encoding='utf-8') as f:
        built = json.load(f)
    files = built['files']
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        live = dict(zip(files, pool.map(lambda p: fetch(a.url, p), files)))
    rows = {'same': [], 'timing': [], 'unexplained': [], 'missing': [], 'error': []}
    for p, want in files.items():
        got = live[p]
        if got['status'] == 404:
            rows['missing'].append({'path': p})
        elif got['status'] == 'error':
            rows['error'].append({'path': p, 'detail': got['detail']})
        elif got['sha256'] == want['sha256']:
            rows['same'].append({'path': p})
        else:
            hit = next((v for v in past_versions(a.src, a.ref, p) if v[2] == got['sha256']), None)
            if hit:
                rows['timing'].append({'path': p, 'live_is_commit': hit[0], 'committed_at': hit[1]})
            else:
                rows['unexplained'].append({'path': p, 'live_size': got['size'], 'built_size': want['size']})
    names = {'same': '같음', 'timing': '시차로 설명됨', 'unexplained': '설명 안 됨',
             'missing': '사이트에 없음', 'error': '확인 실패'}
    lines = [f'## 사이트 비교 (원본 커밋 {built["source_commit"][:7]}, 파일 {len(files)}개)', '',
             '| 결과 | 개수 |', '|---|---|']
    lines += [f'| {names[k]} | {len(rows[k])} |' for k in rows]
    for k in ('timing', 'unexplained', 'missing', 'error'):
        if rows[k]:
            lines += ['', f'### {names[k]}']
            lines += ['- ' + ', '.join(f'{k2}={v}' for k2, v in r.items()) for r in rows[k]]
    text = '\n'.join(lines)
    print(text)
    if a.report:
        with open(a.report, 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write(text + '\n')
    bad = len(rows['unexplained']) + len(rows['missing']) + len(rows['error'])
    print('\n결과:', '통과' if bad == 0 else f'확인 필요 {bad}건')
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('src')
    b.add_argument('dest')
    b.add_argument('--manifest', required=True)
    c = sub.add_parser('compare')
    c.add_argument('src')
    c.add_argument('manifest')
    c.add_argument('url')
    c.add_argument('--ref', default='origin/main')
    c.add_argument('--report')
    a = ap.parse_args()
    if a.cmd == 'build':
        cmd_build(a)
        return 0
    return cmd_compare(a)


if __name__ == '__main__':
    sys.exit(main())
