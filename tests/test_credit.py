import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import wamo_credit as credit


def rows(forced):
    d = date(2026, 1, 2)
    out = []
    for f in forced:
        while d.weekday() > 4:
            d += timedelta(days=1)
        out.append({'date': d.isoformat(), 'credit': 300000.0, 'kospi': 230000.0, 'kosdaq': 70000.0, 'forced': f})
        d += timedelta(days=1)
    return out


class CreditCardTest(unittest.TestCase):
    def test_spike_is_one_and_half_times_prior_20_day_average(self):
        c = credit.card({'rows': rows([100.0] * 30 + [150.0])})
        self.assertEqual(c['status'], 'OK')
        self.assertTrue(c['latest']['spike'])             # 1.50x (average excludes the day itself)
        self.assertEqual(c['latest']['avg20'], 100.0)
        self.assertFalse(credit.card({'rows': rows([100.0] * 30 + [149.0])})['latest']['spike'])

    def test_too_little_history_is_unavailable(self):
        self.assertEqual(credit.card({'rows': rows([1.0] * 10)})['status'], 'UNAVAILABLE')

    def test_failed_half_year_is_retried_next_run(self):
        calls = []

        def fetch(obj, start, end):
            calls.append((obj, start))
            if start.year == 2023 and len([c for c in calls if c[1].year == 2023]) <= 2:
                raise RuntimeError('reset')
            return [{'TMPV1': start.strftime('%Y%m%d'), 'TMPV2': 100, 'TMPV3': 60, 'TMPV4': 40,
                     'TMPV5': 10, 'TMPV6': 5, 'TMPV7': 1.0}]
        with tempfile.TemporaryDirectory() as d:
            out = credit.update_history(d, today=date(2026, 11, 11), fetch=fetch)
            self.assertTrue(out['pending'])
            saved = json.loads((Path(d) / credit.HISTORY).read_text())
            self.assertEqual(saved['pending'], [list(x) for x in out['pending']])


if __name__ == '__main__':
    unittest.main()
