"""셋업북 패턴 탐지기 검사.

각 셋업마다 교과서 형태의 합성 일봉을 만들어 기대한 단계가 나오는지 보고,
조건 하나를 깨면 탐지되지 않는지도 함께 본다. 실제 시장에서 몇 건 걸리는지는
시장 상태에 따라 달라지므로 검사 기준으로 쓰지 않는다.
"""
import json
import unittest
from datetime import date, timedelta

import wamo_patterns as wp


def rows(bars):
    """(종가, 거래량[, 고가, 저가]) 목록을 검증된 일봉 모양으로 바꾼다."""
    day = date(2025, 1, 1)
    out = []
    for item in bars:
        close, volume = item[0], item[1]
        high = item[2] if len(item) > 2 else close * 1.004
        low = item[3] if len(item) > 3 else close * 0.996
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append({'date': day.isoformat(), 'close': round(close, 4),
                    'high': round(high, 4), 'low': round(low, 4), 'volume': float(volume)})
        day += timedelta(days=1)
    return out


def ramp(start, end, count, volume=1000):
    """두 가격 사이를 직선으로 잇는다."""
    if count <= 0:
        return []
    step = (end - start) / count
    return [(start + step * (i + 1), volume) for i in range(count)]


def flat_run(level, count, volume=1000, wobble=0.0):
    out = []
    for i in range(count):
        offset = wobble * (1 if i % 2 else -1)
        out.append((level * (1 + offset), volume))
    return out


def status_of(result, key):
    if not result or not result.get('setups'):
        return 'NONE'
    for setup in result['setups']:
        if setup['key'] == key:
            return setup['status']
    return 'NONE'


def setup_of(result, key):
    for setup in (result or {}).get('setups') or []:
        if setup['key'] == key:
            return setup
    return None


def detect(bars, **kwargs):
    return wp.detect_patterns(rows(bars), **kwargs)


class CupTests(unittest.TestCase):
    def series(self, handle_drop=0.06, handle_len=8, bottom_len=14, breakout=True,
               handle_volume=400, prior=1.45):
        quiet = flat_run(100, 60, 1200)
        rise = ramp(100, 100 * prior, 40, 1600)             # 앞선 상승 45%
        peak = 100 * prior
        down = ramp(peak, peak * 0.76, 20, 1500)            # 컵 깊이 24%
        bottom = flat_run(peak * 0.76, bottom_len, 700)     # 둥근 바닥
        up = ramp(peak * 0.76, peak * 0.99, 22, 1500)       # 오른쪽 회복
        top = peak * 0.99
        handle = ramp(top, top * (1 - handle_drop), handle_len, handle_volume)
        bars = quiet + rise + down + bottom + up + handle
        if breakout:
            bars.append((top * 1.03, 4000))
        return bars

    def test_textbook_cup_breaks_out(self):
        result = detect(self.series())
        setup = setup_of(result, 'cup')
        self.assertIsNotNone(setup, '교과서 컵앤핸들이 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertTrue(all(setup['evidence']['checks'].values()))
        self.assertGreater(setup['evidence']['cupDepthPct'], 12)
        self.assertGreaterEqual(setup['evidence']['handleDays'], 5)

    def test_pivot_is_handle_high_and_stop_is_handle_low(self):
        bars = self.series(breakout=False)
        bars.append((bars[-1][0], 400))   # 핸들 저점 위에서 쉬는 날(손절선 아래로 내려가지 않음)
        setup = setup_of(detect(bars), 'cup')
        self.assertIsNotNone(setup)
        self.assertIn(setup['status'], ('WATCH', 'READY'))
        self.assertGreater(setup['pivot'], setup['stop'])
        self.assertGreater(setup['stopDistancePct'], 0)

    def test_v_bottom_is_rejected(self):
        """바닥에 머문 거래일이 짧으면 U자가 아니다(셋업북 오닐 항목)."""
        self.assertEqual(status_of(detect(self.series(bottom_len=2)), 'cup'), 'NONE')

    def test_deep_handle_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(handle_drop=0.30)), 'cup'), 'NONE')

    def test_handle_without_volume_dry_up_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(handle_volume=3000)), 'cup'), 'NONE')

    def test_cup_without_prior_advance_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(prior=1.05)), 'cup'), 'NONE')


class FlatBaseTests(unittest.TestCase):
    def series(self, width=0.05, length=40, breakout=True, down_volume=800):
        quiet = flat_run(100, 60, 1000)
        rise = ramp(100, 140, 40, 1500)
        base = []
        for i in range(length):
            # 하락일 거래량이 상승일보다 작아야 한다(셋업북 플랫 베이스).
            up = i % 2 == 0
            base.append((140 * (1 - width * (0 if up else 1)), 1900 if up else down_volume))
        bars = quiet + rise + base
        if breakout:
            bars.append((140 * 1.02, 4200))
        return bars

    def test_textbook_flat_base_breaks_out(self):
        setup = setup_of(detect(self.series()), 'flat')
        self.assertIsNotNone(setup, '교과서 플랫 베이스가 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertLessEqual(setup['evidence']['widthPct'], 15)
        self.assertGreaterEqual(setup['evidence']['priorAdvancePct'], 20)

    def test_wide_base_is_not_flat(self):
        self.assertEqual(status_of(detect(self.series(width=0.30)), 'flat'), 'NONE')

    def test_short_base_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(length=12)), 'flat'), 'NONE')

    def test_heavy_down_volume_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(down_volume=5000)), 'flat'), 'NONE')


class DoubleBottomTests(unittest.TestCase):
    def series(self, second=0.98, breakout=True):
        quiet = flat_run(80, 50, 1000)
        rise = ramp(80, 130, 30, 1400)
        first = ramp(130, 100, 14, 1600) + flat_run(100, 6, 900)
        middle = ramp(100, 118, 14, 1300) + flat_run(118, 5, 1000)
        drop = ramp(118, 100 * second, 14, 1500) + flat_run(100 * second, 6, 900)
        back = ramp(100 * second, 117, 10, 1200)
        bars = quiet + rise + first + middle + drop + back
        if breakout:
            bars.append((118 * 1.03, 4000))
        return bars

    def test_textbook_double_bottom_confirms(self):
        setup = setup_of(detect(self.series()), 'dbl')
        self.assertIsNotNone(setup, '교과서 더블바텀이 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertLessEqual(setup['evidence']['bottomGapPct'], 6)

    def test_undercut_is_flagged(self):
        setup = setup_of(detect(self.series(second=0.97)), 'dbl')
        self.assertIsNotNone(setup)
        self.assertTrue(setup['evidence']['undercut'])

    def test_far_apart_bottoms_are_rejected(self):
        self.assertEqual(status_of(detect(self.series(second=0.70)), 'dbl'), 'NONE')


class HighTightFlagTests(unittest.TestCase):
    def series(self, gain=2.0, drop=0.18, flag_len=20, flag_volume=600, breakout=True):
        quiet = flat_run(50, 70, 1000)
        pole = ramp(50, 50 * gain, 30, 6000)
        top = 50 * gain
        flag = flat_run(top * (1 - drop / 2), flag_len, flag_volume, wobble=drop / 2)
        bars = quiet + pole + flag
        if breakout:
            bars.append((top * 1.02, 9000))
        return bars

    def test_textbook_high_tight_flag_breaks_out(self):
        setup = setup_of(detect(self.series()), 'htf')
        self.assertIsNotNone(setup, '교과서 하이 타이트 플래그가 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertGreaterEqual(setup['evidence']['poleGainPct'], 90)
        self.assertLessEqual(setup['evidence']['flagDropPct'], 25)

    def test_weak_pole_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(gain=1.3)), 'htf'), 'NONE')

    def test_loose_flag_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(drop=0.45)), 'htf'), 'NONE')


class PocketPivotTests(unittest.TestCase):
    def series(self, volume=5000, pullback=True):
        rise = ramp(60, 118, 150, 1500)
        # 10일선 근처까지 조용히 눌린다. 하락일 거래량은 작다.
        dip = ramp(118, 114 if pullback else 100, 6, 700)
        return rise + dip + [(116.5, volume)]

    def test_textbook_pocket_pivot_fires(self):
        setup = setup_of(detect(self.series()), 'pp')
        self.assertIsNotNone(setup, '교과서 포켓 피벗이 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertGreater(setup['evidence']['volumeVsMaxDownDay'], 1)

    def test_quiet_volume_day_is_not_a_pocket_pivot(self):
        self.assertEqual(status_of(detect(self.series(volume=500)), 'pp'), 'NONE')

    def test_price_far_from_moving_average_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(pullback=False)), 'pp'), 'NONE')

    def test_stop_width_is_measured_from_the_close(self):
        """포켓 피벗은 피벗이 없다. 사는 가격은 그날 종가이고 손절 폭도 거기서 잰다."""
        setup = setup_of(detect(self.series()), 'pp')
        self.assertIsNone(setup['pivot'])
        self.assertEqual(setup['entry'], 116.5)
        expected = round((1 - setup['stop'] / 116.5) * 100, 1)
        self.assertEqual(setup['stopDistancePct'], expected)
        self.assertGreater(setup['evidence']['closeToMa50Pct'], 0)


class DarvasBoxTests(unittest.TestCase):
    def series(self, width=0.08, box_volume=500, breakout=True):
        quiet = flat_run(60, 60, 1500)
        rise = ramp(60, 120, 100, 2500)
        floor = 120 * (1 - width)
        # 윗변 확정 뒤 아랫변을 먼저 찍고, 그 위에서 좁게 움직인다.
        box = (ramp(120, floor, 5, box_volume)
               + ramp(floor, 120 * (1 - width / 4), 5, box_volume)
               + flat_run(120 * (1 - width / 3), 6, box_volume, wobble=width / 8))
        bars = quiet + rise + box
        if breakout:
            bars.append((120 * 1.02, 6000))
        return bars

    def test_textbook_box_breaks_out(self):
        setup = setup_of(detect(self.series()), 'darvas')
        self.assertIsNotNone(setup, '교과서 다바스 박스가 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertLessEqual(setup['evidence']['boxWidthPct'], 15)

    def test_wide_box_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(width=0.30)), 'darvas'), 'NONE')

    def test_box_without_volume_contraction_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(box_volume=6000)), 'darvas'), 'NONE')


class AscendingTriangleTests(unittest.TestCase):
    def series(self, flat_top=True, breakout=True):
        quiet = flat_run(60, 90, 1200)
        rise = ramp(60, 120, 90, 2200)
        top = 120.0
        second_top = top if flat_top else top * 1.15
        tri = (ramp(top, 104, 8, 900) + ramp(104, top, 8, 850)
               + ramp(top, 110, 8, 800) + ramp(110, second_top, 8, 750)
               + ramp(second_top, 115, 8, 700) + ramp(115, second_top * 0.998, 8, 650))
        bars = quiet + rise + tri
        if breakout:
            bars.append((second_top * 1.03, 5000))
        return bars

    def test_textbook_triangle_breaks_out(self):
        setup = setup_of(detect(self.series()), 'asc')
        self.assertIsNotNone(setup, '교과서 상승 삼각형이 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertGreaterEqual(setup['evidence']['touchesTop'], 2)
        self.assertGreaterEqual(setup['evidence']['touchesBottom'], 2)

    def test_caveat_records_the_weak_statistics(self):
        setup = setup_of(detect(self.series()), 'asc')
        self.assertIn('64%', setup['caveat'])
        self.assertEqual(setup['importance'], 2)

    def test_rising_top_is_not_an_ascending_triangle(self):
        self.assertEqual(status_of(detect(self.series(flat_top=False)), 'asc'), 'NONE')


class CheatEntryTests(unittest.TestCase):
    def series(self, level=0.42, pause_volume=500, breakout=True):
        quiet = flat_run(100, 50, 1200)
        rise = ramp(100, 150, 40, 1800)
        down = ramp(150, 112, 25, 1700)
        bottom = flat_run(112, 10, 900)
        target = 112 + (150 - 112) * level
        up = ramp(112, target, 14, 1500)
        pause = flat_run(target * 0.985, 10, pause_volume, wobble=0.015)
        bars = quiet + rise + down + bottom + up + pause
        if breakout:
            bars.append((target * 1.02, 4500))
        return bars

    def test_textbook_cheat_breaks_out(self):
        setup = setup_of(detect(self.series()), 'cheat')
        self.assertIsNotNone(setup, '교과서 3C 치트가 탐지되지 않았습니다')
        self.assertEqual(setup['status'], 'BREAKOUT')
        self.assertLessEqual(setup['evidence']['pauseWidthPct'], 8)

    def test_pause_too_high_in_the_cup_is_rejected(self):
        """컵 위쪽에서 멈추면 치트가 아니라 핸들이다(셋업북 미너비니 항목)."""
        self.assertEqual(status_of(detect(self.series(level=0.90)), 'cheat'), 'NONE')

    def test_pause_without_volume_dry_up_is_rejected(self):
        self.assertEqual(status_of(detect(self.series(pause_volume=4000)), 'cheat'), 'NONE')


class VcpPassthroughTests(unittest.TestCase):
    """VCP는 수집 단계(detect_vcp)의 값을 옮기기만 한다. 기준을 다시 계산하지 않는다."""

    def payload(self, status='READY'):
        return {'status': status, 'label': 'VCP 피봇 근접 후보', 'pivot': 120.0,
                'distanceToPivotPct': 1.2, 'shrinking': True, 'volumeDryRatio': 0.62,
                'range10Pct': 8.1, 'breakoutVolumeRatio': 1.1, 'criterion': '기준 문장',
                'contractions': [{'peakDate': '2026-01-05', 'troughDate': '2026-01-20',
                                  'peak': 118.0, 'trough': 100.0, 'depthPct': 15.3},
                                 {'peakDate': '2026-03-05', 'troughDate': '2026-03-18',
                                  'peak': 120.0, 'trough': 112.0, 'depthPct': 6.7}]}

    def test_collected_vcp_is_carried_through(self):
        result = detect(flat_run(118, 120, 1000), vcp=self.payload())
        setup = setup_of(result, 'vcp')
        self.assertIsNotNone(setup)
        self.assertEqual(setup['status'], 'READY')
        self.assertEqual(setup['pivot'], 120.0)
        self.assertEqual(setup['stop'], 112.0, '손절은 마지막 수축의 저점이어야 합니다')
        # 기준 문장은 수집 단계의 값을 되쓰지 않고 한 곳(CRITERIA)에서만 관리한다.
        self.assertEqual(setup['criterion'], wp.CRITERIA['vcp'])
        self.assertEqual(setup['evidence']['contractions'], 2)

    def test_inactive_vcp_is_dropped(self):
        for status in ('NONE', 'INSUFFICIENT'):
            result = detect(flat_run(100, 120, 1000), vcp=self.payload(status))
            self.assertEqual(status_of(result, 'vcp'), 'NONE')

    def test_malformed_vcp_is_ignored(self):
        for bad in (None, 'READY', {}, {'status': 'READY'}):
            result = detect(flat_run(100, 120, 1000), vcp=bad)
            self.assertEqual(status_of(result, 'vcp'), 'NONE')


class ShapeTests(unittest.TestCase):
    """화면이 차트 위에 패턴 모양을 그리도록 주요 지점·상자를 함께 싣는다. 날짜는 실제 봉의 날짜여야 한다."""

    def check(self, bars, key, points=0, boxes=0):
        data = rows(bars)
        days = {r['date'] for r in data}
        setup = setup_of(wp.detect_patterns(data), key)
        self.assertIsNotNone(setup, key)
        shape = setup['shape']
        self.assertGreaterEqual(len(shape.get('points', [])), points, key)
        self.assertGreaterEqual(len(shape.get('boxes', [])), boxes, key)
        for d, price, _ in shape.get('points', []):
            self.assertIn(d, days)
            self.assertGreater(price, 0)
        for b in shape.get('boxes', []):
            self.assertIn(b['from'], days)
            self.assertGreaterEqual(b['top'], b['bottom'])
        return shape

    def test_cup_draws_its_u_and_handle(self):
        shape = self.check(CupTests().series(), 'cup', points=4)
        self.assertTrue(shape['connect'])
        self.assertEqual([p[2] for p in shape['points']], ['왼쪽 고점', '컵 바닥', '오른쪽 고점', '핸들 저점'])

    def test_double_bottom_draws_a_w(self):
        shape = self.check(DoubleBottomTests().series(), 'dbl', points=4)
        self.assertEqual([p[2] for p in shape['points']][1:], ['바닥1', '가운데 봉우리', '바닥2'])

    def test_flat_base_and_box_draw_a_range(self):
        self.check(FlatBaseTests().series(), 'flat', boxes=1)
        self.check(DarvasBoxTests().series(), 'darvas', boxes=1)


class SettleTests(unittest.TestCase):
    """판정이 끝난 셋업을 오늘 종가로 맞추는 규칙(settle). 모든 셋업에 같은 규칙을 쓴다."""

    def bars(self, close, high=None, volume=1000):
        rows_ = rows(flat_run(100, 90) + [(close, volume, high or close * 1.004)])
        return wp.Bars(rows_)

    def setup(self, status='WATCH', pivot=110.0, stop=95.0, close=100.0):
        return wp._result('flat', status, '플랫 베이스 형성', pivot, stop,
                          (pivot / close - 1) * 100 if pivot else None)

    def test_close_below_the_stop_drops_the_setup(self):
        self.assertIsNone(wp.settle(self.setup(close=94), self.bars(94)))

    def test_unconfirmed_move_above_the_pivot_is_ready_not_watch(self):
        settled = wp.settle(self.setup(pivot=100, close=103), self.bars(103))
        self.assertEqual(settled['status'], 'READY')
        self.assertTrue(settled['unconfirmed'])
        self.assertEqual(settled['label'], wp.READY_ABOVE_LABEL)

    def test_unconfirmed_move_far_above_the_pivot_is_gone(self):
        self.assertIsNone(wp.settle(self.setup(pivot=100, close=108), self.bars(108)))

    def test_extended_breakout_is_flagged_not_dropped(self):
        settled = wp.settle(self.setup('BREAKOUT', pivot=100, close=108), self.bars(108))
        self.assertEqual(settled['status'], 'BREAKOUT')
        self.assertTrue(settled['extended'])

    def test_normal_setup_is_untouched(self):
        setup = self.setup(pivot=104, close=100)
        self.assertEqual(wp.settle(setup, self.bars(100)), setup)


class StageGateTests(unittest.TestCase):
    def checks(self, failures=0):
        keys = ['주가>150·200일선', '150일선>200일선', '200일선 상승', '50일선>150·200일선',
                '주가>50일선', '52주 저점 대비 +30%', '52주 고점 25% 이내']
        return {k: i >= failures for i, k in enumerate(keys)}

    def test_gate_counts_passed_checks(self):
        gate = wp.stage_gate(self.checks(), True)
        self.assertEqual(gate, {'pass': True, 'passed': 7, 'total': 7, 'failed': []})

    def test_gate_lists_failed_checks(self):
        gate = wp.stage_gate(self.checks(2), False)
        self.assertFalse(gate['pass'])
        self.assertEqual(gate['passed'], 5)
        self.assertEqual(len(gate['failed']), 2)

    def test_missing_checks_give_no_gate(self):
        self.assertIsNone(wp.stage_gate(None, True))
        self.assertIsNone(wp.stage_gate({}, True))

    def test_gate_survives_without_any_setup(self):
        result = detect(flat_run(100, 120, 1000), stage_checks=self.checks(), stage_core=True)
        self.assertEqual(result['setups'], [])
        self.assertTrue(result['stage']['pass'])


class ContractTests(unittest.TestCase):
    def test_short_history_returns_nothing(self):
        self.assertIsNone(detect(flat_run(100, 40, 1000)))
        self.assertIsNone(wp.detect_patterns(None))
        self.assertIsNone(wp.detect_patterns('not rows'))

    def test_quiet_series_finds_no_setup(self):
        self.assertIsNone(detect(flat_run(100, 200, 1000)))

    def test_result_is_json_safe(self):
        """wamo_publish_insights 는 allow_nan=False 로 직렬화한다."""
        result = detect(CupTests().series())
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_every_setup_carries_a_criterion_and_caveat(self):
        for maker in (CupTests, FlatBaseTests, DoubleBottomTests, HighTightFlagTests,
                      PocketPivotTests, DarvasBoxTests, AscendingTriangleTests,
                      CheatEntryTests):
            result = detect(maker().series())
            live = (result or {}).get('setups') or []
            self.assertTrue(live, f'{maker.__name__} 합성 데이터에서 셋업이 비었습니다')
            for setup in live:
                self.assertTrue(setup['criterion'], f"{setup['key']} criterion 누락")
                self.assertTrue(setup['caveat'], f"{setup['key']} caveat 누락")
                self.assertIn(setup['key'], wp.NAMES)
                self.assertEqual(setup['name'], wp.NAMES[setup['key']])

    def test_best_setup_is_the_most_advanced_one(self):
        result = detect(CupTests().series())
        best = setup_of(result, result['best'])
        self.assertEqual(best['status'], result['status'])
        ranks = [wp.RANK[s['status']] for s in result['setups']]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_wide_stop_is_flagged(self):
        """셋업북 ②-4: 피벗에서 손절선까지 7~8%를 넘으면 그 자리는 건너뛴다."""
        setup = setup_of(detect(FlatBaseTests().series(width=0.12)), 'flat')
        self.assertIsNotNone(setup)
        self.assertGreater(setup['stopDistancePct'], 8)
        self.assertTrue(setup['stopTooWide'], '손절 폭 8% 초과는 표시되어야 합니다')

    def test_narrow_stop_is_not_flagged(self):
        setup = setup_of(detect(FlatBaseTests().series(width=0.05)), 'flat')
        self.assertIsNotNone(setup)
        self.assertLess(setup['stopDistancePct'], 8)
        self.assertFalse(setup['stopTooWide'])

    def test_detector_failure_does_not_break_the_others(self):
        """한 탐지기가 터져도 나머지 셋업 판정은 그대로 나와야 한다."""
        original = wp.DETECTORS

        def explode(bars):
            raise ValueError('boom')

        explode.__name__ = 'detect_flat'
        wp.DETECTORS = tuple(explode if d is wp.detect_flat else d for d in original)
        try:
            result = detect(CupTests().series())
            self.assertIsNotNone(result, '다른 탐지기 결과까지 사라졌습니다')
            self.assertEqual(status_of(result, 'cup'), 'BREAKOUT')
            self.assertEqual(status_of(result, 'flat'), 'NONE')
        finally:
            wp.DETECTORS = original


class MetaTests(unittest.TestCase):
    """고정 설명은 스냅샷에 한 번만 싣는다. 종목마다 반복하면 화면 파일이 커진다."""

    def test_meta_covers_every_setup(self):
        meta = wp.meta()
        self.assertEqual(set(meta), set(wp.NAMES))
        for key, entry in meta.items():
            self.assertEqual(entry['name'], wp.NAMES[key])
            self.assertEqual(entry['master'], wp.MASTERS[key])
            self.assertTrue(entry['criterion'])
            self.assertTrue(entry['caveat'])
            # 화면의 '패턴 설명'은 셋업북의 한 줄 논리와 모양 설명을 그대로 쓴다.
            self.assertTrue(entry['logic'], f'{key} 한 줄 논리 누락')
            self.assertTrue(entry['description'], f'{key} 패턴 설명 누락')

    def test_vcp_text_matches_the_collection_stage(self):
        """VCP 문장은 detect_vcp 가 내보내는 것과 같아야 한다.

        수집 단계가 기준을 바꾸면 이 검사가 깨지고, 화면 설명도 함께 고치게 된다.
        """
        import wamo_update_business_dart as core
        bars = rows(flat_run(100, 120, 1000))
        collected = core.detect_vcp(bars)
        self.assertNotEqual(collected['status'], 'INSUFFICIENT', '기준 문장을 비교할 수 없습니다')
        self.assertEqual(wp.CRITERIA['vcp'], collected['criterion'])

    def test_compact_strips_static_text(self):
        result = detect(CupTests().series())
        packed = wp.compact(result)
        self.assertEqual(packed['best'], result['best'])
        for setup in packed['setups']:
            for key in wp.STATIC_KEYS:
                self.assertNotIn(key, setup)
            self.assertIn('status', setup)
            self.assertIn('pivot', setup)

    def test_compact_drops_empty_results(self):
        self.assertIsNone(wp.compact(None))
        self.assertIsNone(wp.compact({'setups': []}))

    def test_compact_output_is_smaller(self):
        result = detect(CupTests().series())
        full = len(json.dumps(result, ensure_ascii=False))
        packed = len(json.dumps(wp.compact(result), ensure_ascii=False))
        self.assertLess(packed, full)


class SwingTests(unittest.TestCase):
    def test_peaks_and_troughs_alternate(self):
        bars = rows(ramp(100, 130, 12) + ramp(130, 105, 12) + ramp(105, 140, 12))
        highs = [r['high'] for r in bars]
        lows = [r['low'] for r in bars]
        kinds = [k for k, _ in wp.swings(highs, lows)]
        self.assertTrue(kinds)
        for a, b in zip(kinds, kinds[1:]):
            self.assertNotEqual(a, b, '같은 종류의 스윙이 연달아 나오면 안 됩니다')

    def test_start_limits_the_scan(self):
        bars = rows(ramp(100, 130, 20) + ramp(130, 105, 20) + ramp(105, 140, 20))
        highs = [r['high'] for r in bars]
        lows = [r['low'] for r in bars]
        self.assertTrue(all(i >= 40 for _, i in wp.swings(highs, lows, 2, 40)))


if __name__ == '__main__':
    unittest.main()
