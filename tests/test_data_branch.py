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


DAY = 86400
NOW = 1_793_000_000   # 시험용 '지금' (KST 기준 어느 날 오후)


class PruneTest(unittest.TestCase):
    """data 브랜치 기록 줄이기: 오늘 기록은 전부, 지난 7일은 하루 마지막 1개, 그 전은 한 덩어리.

    기록을 줄여도 최신 자료 내용은 그대로여야 하고, 정리 전에 받아 둔 갱신 작업도 이어서 올릴 수 있어야 한다.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.remote, self.seed = t / 'remote.git', t / 'seed'
        run('init', '-q', '--bare', '-b', 'data', str(self.remote))
        self.seed.mkdir()
        run('init', '-q', '-b', 'data', cwd=self.seed)
        # 10일 전부터 오늘까지 하루 3번씩(오전·오후·밤) 자료를 올린 기록
        self.history = []
        for day in range(10, -1, -1):
            for k, hour in enumerate((1, 5, 9)):          # UTC 01·05·09시 = KST 10·14·18시
                ts = NOW - NOW % DAY - day * DAY + hour * 3600
                if ts > NOW:
                    continue
                for i in range(22):
                    (self.seed / f'wamo_{i}.json').write_text(f'{{"day": {day}, "k": {k}, "i": {i}}}', encoding='utf-8')
                env = {**os.environ, 'GIT_COMMITTER_DATE': f'@{ts} +0000', 'GIT_AUTHOR_DATE': f'@{ts} +0000'}
                subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '-A'], cwd=self.seed, check=True)
                subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', f'd{day}k{k}'],
                               cwd=self.seed, check=True, env=env)
                self.history.append(ts)
        run('push', '-q', str(self.remote), 'data', cwd=self.seed)
        patch = mock.patch.dict(os.environ, {'WAMO_DATA_REMOTE': 'file://' + str(self.remote)})
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop('GITHUB_ENV', None)
        os.environ.pop('WAMO_DATA_REPO', None)

    def remote_log(self):
        return run('--git-dir', str(self.remote), 'log', '--format=%s', 'data').splitlines()

    def test_오늘은_전부_지난_7일은_하루_하나_그전은_합친다(self):
        before_tree = run('--git-dir', str(self.remote), 'rev-parse', 'data^{tree}')
        self.assertTrue(db.prune(now=NOW, dest=Path(self.tmp.name) / 'p'))
        log = self.remote_log()
        today = [s for s in log if s.startswith('d0k')]
        self.assertEqual(today, ['d0k2', 'd0k1', 'd0k0'][-len(today):])         # 오늘 기록은 전부
        self.assertEqual([s for s in log if s[:1] == 'd' and s[1:2].isdigit() and not s.startswith('d0')],
                         [f'd{d}k2' for d in range(1, 8)])                       # 지난 7일은 그날 마지막 1개
        self.assertTrue(log[-1].startswith('data'))                              # 그 전은 한 덩어리
        self.assertEqual(run('--git-dir', str(self.remote), 'rev-parse', 'data^{tree}'), before_tree)
        # 합친 첫 기록은 남긴 가장 오래된 기록(7일 전 밤) 바로 앞 상태다.
        root = run('--git-dir', str(self.remote), 'rev-list', '--max-parents=0', 'data').strip()
        self.assertIn('"day": 7, "k": 1', run('--git-dir', str(self.remote), 'show', f'{root}:wamo_0.json'))

    def test_두_번째_정리는_할_일이_없다(self):
        self.assertTrue(db.prune(now=NOW, dest=Path(self.tmp.name) / 'p'))
        self.assertFalse(db.prune(now=NOW, dest=Path(self.tmp.name) / 'p2'))

    def test_정리_전에_받아_둔_갱신도_이어서_올린다(self):
        root = Path(self.tmp.name) / 'work'
        root.mkdir()
        db.setup(Path(self.tmp.name) / 'clone', root)
        db.prune(now=NOW, dest=Path(self.tmp.name) / 'p')
        (root / 'wamo_3.json').write_text('{"new": 1}', encoding='utf-8')
        with mock.patch.object(db.time, 'sleep'):
            self.assertTrue(db.publish(['wamo_3.json'], 'after prune', root))
        self.assertEqual(run('--git-dir', str(self.remote), 'show', 'data:wamo_3.json'), '{"new": 1}')
        self.assertEqual(self.remote_log()[0], 'after prune')

    def test_계획은_최신_기록을_반드시_남긴다(self):
        commits = [('a', NOW - 30 * DAY), ('b', NOW - 20 * DAY)]
        base, keep = db.plan_keep(commits, NOW)
        self.assertEqual((base, keep), ('a', ['b']))
        self.assertEqual(db.plan_keep([], NOW), (None, []))
