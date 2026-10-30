"""tools/pages_package.py: 배포 꾸러미를 만들고 실제 사이트와 견주는 도구의 시험.

이 도구가 틀리면 '새 방식이 지금 사이트와 같다'는 확인 자체가 틀린다. 그래서 숨김 규칙,
꾸러미 내용, 비교 결과(같음/사라짐/달라짐)를 작은 가짜 저장소와 가짜 사이트로 확인한다.
"""
import argparse
import contextlib
import functools
import http.server
import importlib.util
import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('pages_package', ROOT / 'tools' / 'pages_package.py')
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)


def make_repo(root, files):
    """files({경로: 내용})을 가진 작은 git 저장소를 만든다."""
    for rel, body in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')
    run = lambda *a: subprocess.run(['git', '-C', str(root), *a], check=True, capture_output=True)
    run('init', '-q')
    run('add', '-A')
    run('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'init')


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class HiddenPathTest(unittest.TestCase):
    def test_jekyll_처럼_점과_밑줄로_시작하는_경로를_숨긴다(self):
        for p in ('.github/workflows/a.yml', '.gitignore', 'docs/_draft.md', '_site/x.html',
                  'node_modules/a.js', 'Gemfile'):
            self.assertTrue(pp.is_hidden(p), p)

    def test_보통_파일은_숨기지_않는다(self):
        for p in ('index.html', 'radar/view.js', 'wamo_insights.json', 'tests/fixtures/radar/a.json',
                  'docs/sec-relay.md', 'a_b/c_d.txt'):
            self.assertFalse(pp.is_hidden(p), p)


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / 'src'
        self.files = {'index.html': '<h1>hi</h1>', 'data/a.json': '{"a": 1}', 'radar/한글.js': 'x=1',
                      '.github/workflows/w.yml': 'on: push', '.gitignore': '*.pyc', '_hidden/z.txt': 'z'}
        make_repo(self.src, self.files)

    def build(self):
        dest, manifest = self.root / 'site', self.root / 'm.json'
        quiet(pp.cmd_build, argparse.Namespace(src=str(self.src), dest=str(dest), manifest=str(manifest)))
        return dest, json.loads(manifest.read_text(encoding='utf-8'))

    def test_사이트에_올라갈_파일만_담는다(self):
        dest, built = self.build()
        got = {str(p.relative_to(dest)) for p in dest.rglob('*') if p.is_file()}
        self.assertEqual(got, {'index.html', 'data/a.json', 'radar/한글.js'})
        self.assertEqual(set(built['files']), got)

    def test_목록의_크기와_해시가_실제_파일과_맞는다(self):
        dest, built = self.build()
        for rel, meta in built['files'].items():
            self.assertEqual(meta['size'], (dest / rel).stat().st_size)
            self.assertEqual(meta['sha256'], pp.sha256_file(dest / rel))

    def test_이전_꾸러미_찌꺼기는_지운다(self):
        dest = self.root / 'site'
        dest.mkdir()
        (dest / 'old.html').write_text('낡음', encoding='utf-8')
        self.build()
        self.assertFalse((dest / 'old.html').exists())


class CompareTest(unittest.TestCase):
    """꾸러미를 진짜 작은 웹서버로 띄워 놓고 비교 결과를 확인한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / 'src'
        make_repo(self.src, {'index.html': '<h1>hi</h1>', 'data/a.json': '{"a": 1}'})
        self.site, self.manifest = self.root / 'site', self.root / 'm.json'
        quiet(pp.cmd_build, argparse.Namespace(src=str(self.src), dest=str(self.site), manifest=str(self.manifest)))
        handler = functools.partial(Quiet, directory=str(self.site))
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def compare(self):
        report = self.root / 'r.json'
        args = argparse.Namespace(src=str(self.src), manifest=str(self.manifest), url=self.url,
                                  ref='HEAD', report=str(report))
        code = quiet(pp.cmd_compare, args)
        return code, json.loads(report.read_text(encoding='utf-8'))

    def test_같으면_통과한다(self):
        code, rows = self.compare()
        self.assertEqual(code, 0)
        self.assertEqual(len(rows['same']), 2)

    def test_사이트에_없는_파일은_잡아낸다(self):
        (self.site / 'data' / 'a.json').unlink()
        code, rows = self.compare()
        self.assertEqual(code, 1)
        self.assertEqual([r['path'] for r in rows['missing']], ['data/a.json'])

    def test_내용이_다르고_설명이_안_되면_잡아낸다(self):
        (self.site / 'index.html').write_text('<h1>다른 내용</h1>', encoding='utf-8')
        code, rows = self.compare()
        self.assertEqual(code, 1)
        self.assertEqual([r['path'] for r in rows['unexplained']], ['index.html'])

    def test_사이트가_직전_버전이면_시차로_설명한다(self):
        # 사이트에는 옛 내용이 있고, 저장소는 그 뒤 새 내용으로 한 번 더 커밋된 상황
        old = (self.site / 'index.html').read_text(encoding='utf-8')
        (self.src / 'index.html').write_text('<h1>새 내용</h1>', encoding='utf-8')
        run = lambda *a: subprocess.run(['git', '-C', str(self.src), *a], check=True, capture_output=True)
        run('add', '-A')
        run('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'update')
        quiet(pp.cmd_build, argparse.Namespace(src=str(self.src), dest=str(self.root / 'new'),
                                               manifest=str(self.manifest)))
        self.assertEqual(old, '<h1>hi</h1>')
        code, rows = self.compare()
        self.assertEqual(code, 0)
        self.assertEqual([r['path'] for r in rows['timing']], ['index.html'])


if __name__ == '__main__':
    unittest.main()
