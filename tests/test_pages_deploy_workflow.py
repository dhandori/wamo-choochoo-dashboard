"""pages-deploy.yml이 기다리는 워크플로 이름이 실제로 있는지 지킨다.

배포는 자료 갱신 작업이 끝날 때 이어서 걸린다(workflow_run). 그 연결은 작업의 `name:` 문자열로
맺어져서, 누가 이름을 바꾸면 그 작업의 갱신이 사이트에 안 올라가는데도 아무 오류가 나지 않는다.
"""
import re
import unittest
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / '.github' / 'workflows'


def listed_names():
    text = (WF / 'pages-deploy.yml').read_text(encoding='utf-8')
    block = text.split('workflow_run:', 1)[1].split('types:', 1)[0]
    return re.findall(r'^\s+-\s+(.+?)\s*$', block, flags=re.M)


def existing_names():
    names = {}
    for f in WF.glob('*.yml'):
        m = re.search(r'^name:\s*(.+?)\s*$', f.read_text(encoding='utf-8'), flags=re.M)
        if m:
            names[m.group(1)] = f.name
    return names


class PagesDeployWorkflowTest(unittest.TestCase):
    def test_배포가_기다리는_워크플로_이름이_실제로_있다(self):
        existing, listed = existing_names(), listed_names()
        self.assertGreaterEqual(len(listed), 5)
        for name in listed:
            self.assertIn(name, existing, f'{name!r} 이름의 워크플로가 없다. 이름을 바꿨다면 '
                                          'pages-deploy.yml의 workflow_run 목록도 같이 고칠 것')

    def test_배포_워크플로는_저장소에_올리지_않는다(self):
        text = (WF / 'pages-deploy.yml').read_text(encoding='utf-8')
        for word in ('git push', 'git commit', 'contents: write'):
            self.assertNotIn(word, text)


if __name__ == '__main__':
    unittest.main()
