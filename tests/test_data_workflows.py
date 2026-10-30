"""자료를 쓰는 워크플로가 모두 data 브랜치로 올리는지 지킨다.

한 곳이라도 예전처럼 main 에 자료를 올리면 사이트가 두 갈래 자료를 번갈아 보여 준다.
이 시험은 그런 변경이 들어오면 CI 에서 막는다.
"""
import re
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / '.github' / 'workflows'
WRITERS = ['main.yml', 'asia.yml', 'macro.yml', 'radar-sync.yml', 'resume-once.yml']
READERS = ['ci.yml']  # 자료를 읽어 검사만 하는 워크플로


class DataWorkflowTest(unittest.TestCase):
    def text(self, name):
        return (WF / name).read_text(encoding='utf-8')

    def test_자료_작업은_자료_브랜치를_받는다(self):
        for name in WRITERS:
            self.assertIn('wamo_data_branch.py setup', self.text(name), name)

    def test_검사_워크플로도_최신_자료를_받는다(self):
        for name in READERS:
            self.assertIn('wamo_data_branch.py setup', self.text(name), name)

    def test_자료_작업은_main_에_직접_올리지_않는다(self):
        for name in WRITERS:
            for line in self.text(name).splitlines():
                code = line.split('#')[0]
                if name == 'radar-sync.yml' and 'TARGET_BRANCH' in code:
                    continue  # 시험 갈래(main 이 아닌 곳)용 예전 방식. 아래에서 순서를 확인한다
                self.assertNotRegex(code, r'git push origin ["\']?HEAD:main', f'{name}: {line.strip()}')
        text = self.text('radar-sync.yml')
        self.assertLess(text.index('wamo_data_branch.py publish'), text.index('git push origin "HEAD:$TARGET_BRANCH"'))

    def test_자료_작업이_자료를_받는_단계는_실행_단계_앞에_있다(self):
        for name in WRITERS:
            text = self.text(name)
            steps = text[text.index('steps:'):]
            first_use = steps.index('setup-python')  # 파이썬을 준비한 뒤의 모든 실행 단계보다 앞
            self.assertLess(steps.index('wamo_data_branch.py setup'), first_use, name)

    def test_배포는_자료_쪽을_우선한다(self):
        text = self.text('pages-deploy.yml')
        self.assertIn('--on-duplicate overlay', text)
        self.assertNotRegex(text, r'--on-duplicate (src|fail)')


if __name__ == '__main__':
    unittest.main()
