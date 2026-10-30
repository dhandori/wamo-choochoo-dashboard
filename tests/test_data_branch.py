"""wamo_data_branch.py: 자료를 data 브랜치에 올리고 받는 도구의 시험.

진짜 GitHub 대신 로컬 저장소를 원격으로 쓴다. 자료가 사라지거나 남의 변경을 덮어쓰면 사이트가 틀린 자료를
보여 주므로, 받기·올리기·동시에 올릴 때의 재시도·같은 파일 충돌 중단을 확인한다.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wamo_data_branch as db


def run(*args, cwd=None):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


class DataBranchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.remote, seed = t / 'remote.git', t / 'seed'
        run('init', '-q', '--bare', '-b', 'data', str(self.remote))
        seed.mkdir()
        run('init', '-q', '-b', 'data', cwd=seed)
        for i in range(25):
            (seed / f'wamo_{i}.json').write_text(f'{{"v": {i}}}', encoding='utf-8')
        run('add', '-A', cwd=seed)
        run('commit', '-q', '-m', 'seed', cwd=seed)
        run('push', '-q', str(self.remote), 'data', cwd=seed)
        self.root = t / 'work'
        self.root.mkdir()
        patch = mock.patch.dict(os.environ, {'WAMO_DATA_REMOTE': 'file://' + str(self.remote)})
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop('GITHUB_ENV', None)
        os.environ.pop('WAMO_DATA_REPO', None)
        self.dest = t / 'clone'

    def remote_file(self, name):
        return run('--git-dir', str(self.remote), 'show', f'data:{name}')

    def test_받으면_자료가_작업_폴더에_덮인다(self):
        db.setup(self.dest, self.root)
        self.assertEqual((self.root / 'wamo_3.json').read_text(encoding='utf-8'), '{"v": 3}')
        self.assertEqual(len(list(self.root.glob('wamo_*.json'))), 25)

    def test_자료가_너무_적으면_받기_실패(self):
        with mock.patch.object(db, 'MIN_FILES', 99), self.assertRaises(RuntimeError):
            db.setup(self.dest, self.root)

    def test_주소가_틀리면_재시도_끝에_실패한다(self):
        with mock.patch.dict(os.environ, {'WAMO_DATA_REMOTE': 'file://' + str(self.remote) + 'x'}), \
                mock.patch.object(db.time, 'sleep'), self.assertRaises(RuntimeError):
            db.setup(self.dest, self.root)

    def test_올리면_원격에_반영되고_바뀐_게_없으면_올리지_않는다(self):
        db.setup(self.dest, self.root)
        (self.root / 'wamo_1.json').write_text('{"v": 100}', encoding='utf-8')
        self.assertTrue(db.publish(['wamo_1.json'], 'm', self.root))
        self.assertEqual(self.remote_file('wamo_1.json'), '{"v": 100}')
        self.assertFalse(db.publish(['wamo_1.json'], 'm', self.root))

    def test_없는_파일은_건너뛴다(self):
        db.setup(self.dest, self.root)
        self.assertFalse(db.publish(['없음.json'], 'm', self.root))

    def test_새_파일도_올라간다(self):
        db.setup(self.dest, self.root)
        (self.root / 'wamo_new.json').write_text('{}', encoding='utf-8')
        self.assertTrue(db.publish(['wamo_new.json'], 'm', self.root))
        self.assertEqual(self.remote_file('wamo_new.json'), '{}')

    def other_writer(self, name, body):
        """다른 작업이 먼저 올린 상황을 만든다."""
        other = Path(self.tmp.name) / f'other_{name}'
        run('clone', '-q', '--branch', 'data', str(self.remote), str(other))
        (other / name).write_text(body, encoding='utf-8')
        run('add', '-A', cwd=other)
        run('commit', '-q', '-m', 'other', cwd=other)
        run('push', '-q', 'origin', 'data', cwd=other)

    def test_남이_먼저_다른_파일을_올렸으면_받아서_내_파일만_얹는다(self):
        db.setup(self.dest, self.root)
        self.other_writer('wamo_7.json', '{"other": true}')
        (self.root / 'wamo_1.json').write_text('{"v": 100}', encoding='utf-8')
        with mock.patch.object(db.time, 'sleep'):
            self.assertTrue(db.publish(['wamo_1.json'], 'm', self.root))
        self.assertEqual(self.remote_file('wamo_1.json'), '{"v": 100}')
        self.assertEqual(self.remote_file('wamo_7.json'), '{"other": true}')  # 남의 변경이 살아 있다

    def test_남이_같은_파일을_바꿨으면_덮어쓰지_않고_멈춘다(self):
        db.setup(self.dest, self.root)
        self.other_writer('wamo_1.json', '{"other": true}')
        (self.root / 'wamo_1.json').write_text('{"v": 100}', encoding='utf-8')
        with mock.patch.object(db.time, 'sleep'), self.assertRaises(RuntimeError) as cm:
            db.publish(['wamo_1.json'], 'm', self.root)
        self.assertIn('동시 수정 충돌', str(cm.exception))
        self.assertEqual(self.remote_file('wamo_1.json'), '{"other": true}')

    def test_내_push_가_이미_반영된_뒤_재시도해도_충돌로_보지_않는다(self):
        db.setup(self.dest, self.root)
        self.other_writer('wamo_1.json', '{"v": 100}')  # 내가 올리려던 내용과 같은 내용이 이미 원격에 있다
        (self.root / 'wamo_1.json').write_text('{"v": 100}', encoding='utf-8')
        with mock.patch.object(db.time, 'sleep'):
            db.publish(['wamo_1.json'], 'm', self.root)
        self.assertEqual(self.remote_file('wamo_1.json'), '{"v": 100}')

    def test_옛_main_직접_올리기는_막혀_있다(self):
        import wamo_run
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('WAMO_DATA_REPO', None)
            os.environ.pop('WAMO_ALLOW_MAIN_PUSH', None)
            with self.assertRaises(RuntimeError):
                wamo_run.publish(['wamo_macro.json'])

    def test_두_번_연달아_올려도_된다(self):
        db.setup(self.dest, self.root)
        (self.root / 'wamo_1.json').write_text('{"v": 100}', encoding='utf-8')
        self.assertTrue(db.publish(['wamo_1.json'], 'a', self.root))
        (self.root / 'wamo_2.json').write_text('{"v": 200}', encoding='utf-8')
        self.assertTrue(db.publish(['wamo_2.json'], 'b', self.root))
        self.assertEqual(self.remote_file('wamo_1.json'), '{"v": 100}')
        self.assertEqual(self.remote_file('wamo_2.json'), '{"v": 200}')


if __name__ == '__main__':
    unittest.main()
