"""정시 깨우개(tools/pacemaker.py) 일정 계산과 일정표 검사."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import pacemaker as p  # noqa: E402

KR = {'name': 'k', 'at': '15:52', 'tz': 'Asia/Seoul', 'days': [1, 2, 3, 4, 5], 'workflow': 'main.yml'}
NY = {'name': 'n', 'at': '16:12', 'tz': 'America/New_York', 'days': [1, 2, 3, 4, 5], 'workflow': 'main.yml'}


class PacemakerTests(unittest.TestCase):
    def test_table_is_valid(self):
        rows = p.load_table()
        self.assertTrue(rows)
        wf = Path(__file__).resolve().parents[1] / '.github' / 'workflows'
        for r in rows:
            self.assertTrue((wf / r['workflow']).exists(), r['workflow'])

    def test_weekdays_and_window(self):
        start = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)     # 금 09:00 KST
        end = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)       # 화 09:00 KST
        got = [t for t, _ in p.occurrences([KR], start, end)]
        # 금 15:52, 월 15:52 KST (토·일 없음)
        self.assertEqual([t.isoformat() for t in got], ['2026-11-20T06:52:00+00:00', '2026-11-23T06:52:00+00:00'])

    def test_new_york_follows_daylight_time(self):
        summer = p.occurrences([NY], datetime(2026, 6, 1, 12, tzinfo=timezone.utc), datetime(2026, 6, 2, 12, tzinfo=timezone.utc))
        winter = p.occurrences([NY], datetime(2026, 12, 1, 12, tzinfo=timezone.utc), datetime(2026, 12, 2, 12, tzinfo=timezone.utc))
        self.assertEqual(summer[0][0].hour, 20)   # EDT 16:12 = 20:12 UTC
        self.assertEqual(winter[0][0].hour, 21)   # EST 16:12 = 21:12 UTC

    def test_window_is_half_open(self):
        t = datetime(2026, 11, 20, 6, 52, tzinfo=timezone.utc)
        self.assertEqual(len(p.occurrences([KR], t, t.replace(minute=59))), 0)      # 앞 실행이 이미 건 시각은 다시 안 건다
        self.assertEqual(len(p.occurrences([KR], t.replace(minute=51), t)), 1)


if __name__ == '__main__':
    unittest.main()
