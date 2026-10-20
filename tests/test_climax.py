import json
import unittest
from pathlib import Path

import wamo_climax as W

ROOT = Path(__file__).resolve().parents[1]


def day(d, c, h=None, l=None, v=1000, o=None):
    return {'date': d, 'open': o, 'close': c, 'high': h if h is not None else c * 1.01,
            'low': l if l is not None else c * 0.99, 'volume': v}


def dates(n):
    from datetime import date, timedelta
    start, out = date(2025, 1, 1), []
    while len(out) < n:
        if start.weekday() < 5:
            out.append(start.isoformat())
        start += timedelta(days=1)
    return out


def uptrend(n=260, step=0.002):
    ds, rows, c = dates(n), [], 100.0
    for d in ds:
        c *= 1 + step
        rows.append(day(d, c))
    return rows


class ReversalTest(unittest.TestCase):
    def test_top_reversal_needs_new_high_lower_close_and_low_half(self):
        rows = uptrend()
        last = rows[-1]['close']
        rows.append(day('2026-01-01', last * 0.985, h=last * 1.04, l=last * 0.975, v=3000, o=last * 1.025))
        rv = W.reversal_at(rows, len(rows) - 1)
        self.assertEqual(rv['kind'], 'TOP')
        self.assertTrue(rv['strong'])
        codes = {p['code'] for p in rv['points']}
        self.assertIn('GAP_TRAP', codes)
        self.assertIn('VOLUME', codes)

    def test_close_in_upper_half_is_not_a_top_reversal(self):
        rows = uptrend()
        last = rows[-1]['close']
        rows.append(day('2026-01-01', last * 0.999, h=last * 1.01, l=last * 0.96))
        self.assertIsNone(W.reversal_at(rows, len(rows) - 1))

    def test_narrow_day_is_ignored(self):
        rows = uptrend()
        last = rows[-1]['close']
        rows.append(day('2026-01-01', last * 0.998, h=last * 1.0101, l=last * 0.997))
        self.assertIsNone(W.reversal_at(rows, len(rows) - 1))

    def test_bottom_reversal_in_uptrend_pullback(self):
        rows = uptrend()
        for k in range(5):
            c = rows[-1]['close'] * 0.98
            rows.append(day(f'2026-01-0{k + 1}', c))
        c = rows[-1]['close']
        rows.append(day('2026-01-08', c * 1.01, h=c * 1.015, l=c * 0.96))
        rv = W.reversal_at(rows, len(rows) - 1)
        self.assertEqual(rv['kind'], 'BOTTOM')


class ClimaxTest(unittest.TestCase):
    def test_vertical_run_after_uptrend_is_a_climax(self):
        rows = uptrend()
        c = rows[-1]['close']
        for k in range(10):
            prev_h = rows[-1]['high']
            c *= 1.035 if k != 5 else 1.09
            low = prev_h * 1.01 if k == 5 else c * 0.99      # one unfilled gap
            rows.append(day(f'2026-02-{k + 1:02d}', c, h=c * 1.01, l=low, v=5000 if k == 5 else 1000))
        cl = W.climax_at(rows, len(rows) - 1)
        self.assertTrue(cl['met'])
        codes = {p['code'] for p in cl['points']}
        self.assertTrue({'BIG_DAY', 'UP_DAYS', 'GAP', 'VOL_CLIMAX'} <= codes)

    def test_steady_trend_is_not_a_climax(self):
        rows = uptrend()
        self.assertFalse(W.climax_at(rows, len(rows) - 1)['met'])

    def test_top_warning_links_climax_and_reversal(self):
        rows = uptrend()
        c = rows[-1]['close']
        for k in range(10):
            c *= 1.035 if k != 5 else 1.09
            rows.append(day(f'2026-02-{k + 1:02d}', c, v=5000 if k == 5 else 1000))
        rows.append(day('2026-02-20', c * 0.97, h=c * 1.05, l=c * 0.96, v=6000))
        sc = W.scan(rows)
        self.assertIsNotNone(sc['climax'])
        self.assertEqual(sc['topWarning']['date'], '2026-02-20')


class KospiJuneTest(unittest.TestCase):
    """사용자가 짚은 2026년 6월 코스피 천장: 6/19 갭 상승 뒤 반전 → 6/23 -10%."""

    def test_kospi_2026_06_19_is_a_top_warning(self):
        hist = json.loads((ROOT / 'wamo_ftd_history.json').read_text(encoding='utf-8')).get('^KS11', [])
        rows = [r for r in hist if r.get('close')]
        if not any(r['date'] == '2026-06-19' for r in rows) or rows[0]['date'] > '2026-02-01':
            self.skipTest('2026년 6월 코스피 이력이 저장 범위를 벗어남')
        rep = W.index_report(rows, '^KS11', 'KOSPI', days=len(rows))
        ev = {e['date']: e for e in rep['events']}
        self.assertEqual(ev['2026-06-19']['type'], 'TOP_WARNING')
        self.assertLess(ev['2026-06-19']['worst20'], -20)


if __name__ == '__main__':
    unittest.main()
