"""추세추종 셋업북 12개 패턴의 정량 후보 탐지.

검증된 일봉(close/high/low/volume)만 읽는 순수 함수 모듈이다. 네트워크도
파일도 쓰지 않으므로 wamo_insights.py 의 오프라인 투영 단계에서 호출된다.

판정 어휘와 스윙 고저점 탐지(radius=3)는 wamo_update_business_dart.detect_vcp
와 같다. 두 화면이 다른 답을 내지 않게 하려는 것이고, VCP 자체는 수집 단계가
이미 계산해 두므로 여기서 다시 구하지 않는다(attach_patterns 가 그 값을 받는다).

사람의 차트 판독을 대체하지 않는다. 각 셋업은 셋업북이 적어 둔 수치 조건만
확인하며, '둥근 바닥'처럼 눈으로 보는 형태는 근사치로만 거른다. 그래서 모든
결과에 criterion(판정 기준)과 caveat(한계)을 함께 남긴다.

시가(open)는 수집되지 않는다. 쿨라매기 모멘텀 돌파와 에피소딕 피벗의 '장 초반
시가 범위 돌파'는 일봉으로 확인할 수 없으므로, 두 셋업은 돌파 직전 상태까지만
판정하고 진입 시점은 사람이 장중에 본다.
"""
import statistics

# 셋업북 중요도(★). 같은 단계의 셋업이 여러 개 걸릴 때 대표를 고르는 데만 쓴다.
IMPORTANCE = {
    'stage': 5, 'vcp': 5, 'cup': 5,
    'flat': 4,
    'dbl': 3, 'htf': 3, 'pp': 3, 'darvas': 3, 'cheat': 3,
    'asc': 2,
}
NAMES = {
    'stage': '2단계 상승 추세', 'vcp': '변동성 수축(VCP)', 'cup': '컵앤핸들',
    'flat': '플랫 베이스',
    'dbl': '더블바텀', 'htf': '하이 타이트 플래그', 'pp': '포켓 피벗',
    'darvas': '다바스 박스', 'cheat': '3C 치트 진입', 'asc': '상승 삼각형',
}
MASTERS = {
    'stage': '와인스타인·미너비니', 'vcp': '미너비니', 'cup': '오닐',
    'flat': '오닐',
    'dbl': '오닐·불코우스키', 'htf': '오닐·미너비니', 'pp': '모랄레스·카처',
    'darvas': '다바스', 'cheat': '미너비니', 'asc': '불코우스키',
}
# 진행 단계. 셋업북 '세 단계로 나눈 공통 체크'의 ①셋업 형성 → ②돌파일에 대응한다.
RANK = {'BREAKOUT': 3, 'READY': 2, 'WATCH': 1, 'NONE': 0, 'INSUFFICIENT': 0}
LIVE = ('WATCH', 'READY', 'BREAKOUT')

MIN_ROWS = 80            # detect_vcp 와 같은 최소 이력
BREAKOUT_VOLUME = 1.50   # 셋업북 ②-1: 50일 평균보다 40~50% 이상
DRY_VOLUME = 0.80        # detect_vcp 와 같은 거래량 건조 기준
MAX_STOP_PCT = 8.0       # 셋업북 ②-4: 피벗에서 손절선까지 7~8% 넘으면 사지 않는다


def _f(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float('inf') else None


def _r(value, digits=2):
    value = _f(value)
    return None if value is None else round(value, digits)


def _ma(values, n):
    return statistics.mean(values[-n:]) if len(values) >= n else None


def _mean(values):
    return statistics.mean(values) if values else None


def swings(highs, lows, radius=3, start=0):
    """국지 고점(P)·저점(T)을 번갈아 뽑는다. detect_vcp 와 같은 규칙이다."""
    events = []
    for i in range(max(radius, start), len(highs) - radius):
        window_h = highs[i - radius:i + radius + 1]
        window_l = lows[i - radius:i + radius + 1]
        kind = 'P' if highs[i] >= max(window_h) else 'T' if lows[i] <= min(window_l) else None
        if not kind:
            continue
        if events and events[-1][0] == kind:
            prev = events[-1]
            if (kind == 'P' and highs[i] > highs[prev[1]]) or (kind == 'T' and lows[i] < lows[prev[1]]):
                events[-1] = (kind, i)
        elif not events or i - events[-1][1] >= 3:
            events.append((kind, i))
    return events


class Bars:
    """한 종목의 일봉과 자주 쓰는 파생값. 검증은 호출자(history)가 이미 했다."""

    def __init__(self, rows):
        self.rows = rows
        self.dates = [r['date'] for r in rows]
        self.closes = [float(r['close']) for r in rows]
        self.highs = [float(r['high']) for r in rows]
        self.lows = [float(r['low']) for r in rows]
        self.volumes = [float(r.get('volume') or 0) for r in rows]
        self.n = len(rows)
        self.close = self.closes[-1]
        self.ma10 = _ma(self.closes, 10)
        self.ma20 = _ma(self.closes, 20)
        self.ma50 = _ma(self.closes, 50)
        self.ma150 = _ma(self.closes, 150)
        self.ma200 = _ma(self.closes, 200)
        self.ma50_prev = _mean(self.closes[-55:-5]) if self.n >= 55 else None
        self.avg_vol50 = _mean(self.volumes[-50:]) if self.n >= 50 else _mean(self.volumes)
        self.high52 = max(self.highs[-252:])
        self.low52 = min(self.lows[-252:])
        self.swings = swings(self.highs, self.lows)

    def volume_ratio(self, index=-1):
        return self.volumes[index] / self.avg_vol50 if self.avg_vol50 else None

    def base(self, start, end=None):
        """형성된 베이스의 고점·저점.

        판정 대상인 마지막 봉(오늘)은 넣지 않는다. 피벗은 오늘 넘어야 할 저항이므로
        오늘 고가가 섞이면 돌파가 영원히 성립하지 않는다. detect_vcp 가 피벗을
        highs[-20:-1] 로 잡는 것과 같은 이유다.
        """
        end = self.n - 1 if end is None else end
        if end <= start or start < 0:
            return None, None
        return max(self.highs[start:end]), min(self.lows[start:end])

    def dry(self, bars):
        """최근 bars 거래일(오늘 제외) 거래량이 50일 평균보다 말랐는지.

        50일 평균에는 그 구간 자신이 섞이므로, 구간이 길면 비율이 1에 끌려간다.
        10거래일 안팎의 짧은 구간에만 쓰고, 베이스 전체는 dry_in 을 쓴다.
        """
        window = self.volumes[max(0, self.n - 1 - bars):self.n - 1]
        if not self.avg_vol50 or not window:
            return None, False
        ratio = _mean(window) / self.avg_vol50
        return ratio, ratio <= DRY_VOLUME

    def dry_in(self, start):
        """베이스(start부터 어제까지) 거래량이 그 직전 구간보다 말랐는지.

        셋업북이 말하는 '베이스 안에서 거래량이 마른다'는 앞선 상승 구간과의 비교다.
        구간 자신이 섞인 50일 평균과 비교하면 긴 베이스에서 건조를 놓친다. 돌파일의
        급증 거래량은 베이스가 아니라 확인 신호이므로 오늘은 넣지 않는다.
        """
        before = self.volumes[max(0, start - 50):start]
        inside = self.volumes[start:self.n - 1]
        if not before or not inside:
            return None, False
        base = _mean(before)
        if not base:
            return None, False
        ratio = _mean(inside) / base
        return ratio, ratio <= DRY_VOLUME

    def close_position(self, index=-1):
        """종가가 그날 범위의 어디에서 끝났는지(0=저가, 1=고가). 셋업북 ②-2."""
        span = self.highs[index] - self.lows[index]
        return (self.closes[index] - self.lows[index]) / span if span > 0 else 1.0

    def advance_before(self, index, window=120):
        """셋업북 ①-3: 베이스 직전에 앞선 상승이 있었는지."""
        start = max(0, index - window)
        if index <= start:
            return None
        base = min(self.closes[start:index])
        return (self.closes[index] / base - 1) * 100 if base > 0 else None


def _confirm(bars, pivot):
    """셋업북 ② 돌파하는 날: 피벗 상향 + 거래량 증가 + 종가가 범위 위쪽."""
    ratio = bars.volume_ratio()
    return bool(
        pivot and bars.close > pivot
        and ratio is not None and ratio >= BREAKOUT_VOLUME
        and bars.close_position() >= 0.5
    )


def _stage(bars, pivot, confirmed, near_low=-1.5, near_high=5.0):
    """피벗까지 거리로 형성중/임박/돌파를 가른다. 거리 부호는 detect_vcp 와 같다."""
    distance = (pivot / bars.close - 1) * 100 if pivot and bars.close else None
    if confirmed:
        return 'BREAKOUT', distance
    if distance is not None and near_low <= distance <= near_high:
        return 'READY', distance
    return 'WATCH', distance


def _result(key, status, label, pivot=None, stop=None, distance=None, base_days=None,
            evidence=None, criterion='', caveat=''):
    stop_pct = None
    if pivot and stop and pivot > 0:
        stop_pct = (1 - stop / pivot) * 100
    return {
        'key': key, 'name': NAMES[key], 'master': MASTERS[key], 'importance': IMPORTANCE[key],
        'status': status, 'label': label,
        'pivot': _r(pivot), 'stop': _r(stop),
        'distanceToPivotPct': _r(distance, 1), 'stopDistancePct': _r(stop_pct, 1),
        # 셋업북 ②-4. 손절 폭이 넘치면 그 자리는 건너뛴다는 표시만 남기고 판정은 바꾸지 않는다.
        'stopTooWide': bool(stop_pct is not None and stop_pct > MAX_STOP_PCT),
        'baseDays': base_days,
        'evidence': {k: v for k, v in (evidence or {}).items() if v is not None},
        'criterion': criterion, 'caveat': caveat,
    }


def _none(key):
    return {'key': key, 'status': 'NONE'}


# ── 컵앤핸들 ────────────────────────────────────────────────────────────────
CUP_CRITERION = ('앞선 상승 30%↑ · 컵 깊이 12~50% · 저점 +3% 안에 7거래일↑ 연속 체류(U자) · '
                 '오른쪽이 왼쪽 고점 93%↑ 회복 · 핸들 컵 위쪽 절반·깊이 3~15%·5거래일↑·거래량 건조')
CUP_CAVEAT = ('U자 둥글기를 저점 근처 연속 체류 거래일로 근사한다. 실제 컵의 형태와 핸들 기울기는 '
              '차트에서 최종 확인해야 하고, 65주를 넘는 긴 컵은 260거래일 이력으로 볼 수 없다.')


def _cup_shape(bars, left, right):
    """컵 한 구간의 기하. 왼쪽 고점·바닥·오른쪽 회복과 U자 근사치를 함께 돌려준다."""
    if right - left < 20:
        return None
    lows = bars.lows[left:right + 1]
    bottom_rel = lows.index(min(lows))
    bottom = left + bottom_rel
    peak, trough = bars.highs[left], bars.lows[bottom]
    if peak <= 0 or trough <= 0:
        return None
    depth = (peak - trough) / peak * 100
    recovery = bars.highs[right] / peak * 100
    # U자 근사: 저점 +3% 안에 '연속으로' 머문 거래일. 합계로 세면 완만한 V자도 통과한다.
    # 직선 V자는 하루에 깊이/구간만큼 움직이므로 이 구간이 길게 이어지지 않는다.
    run = best_run = 0
    for value in lows:
        run = run + 1 if value <= trough * 1.03 else 0
        best_run = max(best_run, run)
    span = right - left
    centered = 0.2 <= bottom_rel / span <= 0.8 if span else False
    return {'left': left, 'bottom': bottom, 'right': right, 'peak': peak, 'trough': trough,
            'depthPct': depth, 'recoveryPct': recovery, 'bottomDays': best_run,
            'rounded': best_run >= 7 and centered, 'span': span}


def _cups(bars):
    """가장 최근 쪽부터 컵 후보를 만든다. 왼쪽 고점은 스윙 고점에서만 고른다."""
    peaks = [i for kind, i in bars.swings if kind == 'P']
    found = []
    for left in peaks:
        if bars.n - left < 25:
            continue
        for right in [i for i in peaks if i > left and i - left >= 20]:
            shape = _cup_shape(bars, left, right)
            if shape and 12 <= shape['depthPct'] <= 50 and shape['recoveryPct'] >= 93:
                found.append(shape)
    return sorted(found, key=lambda s: s['right'], reverse=True)


def detect_cup(bars):
    """컵앤핸들. 피벗은 핸들 고점, 손절은 핸들 저점(셋업북 오닐 항목)."""
    if bars.n < 60:
        return _none('cup')
    for shape in _cups(bars):
        right = shape['right']
        # 핸들은 컵 오른쪽 고점부터 어제까지. 오늘은 그 핸들 고점을 넘는지 판정할 봉이다.
        hs, he = right, bars.n - 1
        handle_len = he - hs
        if handle_len < 5:
            continue
        handle_high, handle_low = bars.base(hs, he)
        if handle_high is None:
            continue
        handle_depth = (bars.highs[right] - handle_low) / bars.highs[right] * 100
        midpoint = shape['trough'] + (shape['peak'] - shape['trough']) * 0.5
        advance = bars.advance_before(shape['left'])
        ratio, dry = bars.dry_in(hs)
        # 셋업북: 핸들이 위로 기울면(쐐기) 실패가 잦다. 앞·뒤 절반 고가로 본다.
        half = hs + handle_len // 2
        wedge = bool(half > hs and max(bars.highs[half:he]) > max(bars.highs[hs:half]) * 1.02)
        checks = {
            '앞선 상승 30%↑': bool(advance is not None and advance >= 30),
            '둥근 U자 바닥': shape['rounded'],
            '핸들 컵 위쪽 절반': handle_low >= midpoint,
            '핸들 깊이 3~15%': 3 <= handle_depth <= 15,
            '핸들 거래량 건조': dry,
            '쐐기형 아님': not wedge,
        }
        if not all(checks.values()):
            continue
        pivot = handle_high
        confirmed = _confirm(bars, pivot)
        status, distance = _stage(bars, pivot, confirmed)
        label = {'BREAKOUT': '컵앤핸들 돌파', 'READY': '핸들 고점 근접', 'WATCH': '핸들 형성'}[status]
        return _result(
            'cup', status, label, pivot, handle_low, distance, shape['span'] + handle_len,
            {'cupDepthPct': _r(shape['depthPct'], 1), 'cupDays': shape['span'],
             'bottomDays': shape['bottomDays'], 'recoveryPct': _r(shape['recoveryPct'], 1),
             'handleDepthPct': _r(handle_depth, 1), 'handleDays': handle_len,
             'handleVolumeRatio': _r(ratio), 'priorAdvancePct': _r(advance, 1),
             'breakoutVolumeRatio': _r(bars.volume_ratio()), 'checks': checks},
            CUP_CRITERION, CUP_CAVEAT)
    return _none('cup')


# ── 플랫 베이스 ─────────────────────────────────────────────────────────────
FLAT_CRITERION = ('앞선 상승 20%↑ · 25거래일↑ 옆걸음 · 폭 15% 이내 · '
                  '하락일 거래량 ≤ 상승일 거래량 · 50일선 위 · 베이스 고점이 52주 고가 90%↑')
FLAT_CAVEAT = ('베이스 차수(상승 이후 몇 번째 베이스인지)는 세지 않는다. 셋업북은 세 번째 '
               '이후 베이스의 실패가 잦다고 적었으므로 차수는 차트에서 확인해야 한다.')


def detect_flat(bars):
    """플랫 베이스. 가장 좁은 옆걸음 구간을 찾는다.

    '가장 긴' 구간을 고르면 앞선 상승의 끝부분을 삼켜 베이스가 기울고, 손절선이 실제
    베이스 저점보다 낮아진다. 좁은 폭이 이 셋업의 존재 이유이므로 폭을 먼저 본다.
    """
    if bars.n < 60 or not bars.ma50 or bars.close <= bars.ma50:
        return _none('flat')
    candidates = []
    for length in range(25, min(81, bars.n - 29)):
        # 베이스는 어제까지 형성된 구간이고, 오늘은 그 고점을 넘는지 판정할 봉이다.
        start = bars.n - 1 - length
        window_high, window_low = bars.base(start)
        if not window_high or window_high <= 0:
            continue
        width = (window_high - window_low) / window_high * 100
        if width > 15:
            continue
        # 셋업북: '거의 옆으로만 움직이며 쉬는' 모양이다. 옆걸음이면 구간의 앞도 뒤도
        # 밴드 아래쪽을 한 번은 찍는다. 상승 중인 구간은 저점을 앞에서만 찍으므로 걸러진다.
        third = max(1, length // 3)
        floor = window_low + (window_high - window_low) / 3
        if min(bars.lows[start:start + third]) > floor:
            continue
        if min(bars.lows[bars.n - 1 - third:bars.n - 1]) > floor:
            continue
        # 50일선에서 지지를 받는다.
        if min(bars.closes[start:bars.n - 1]) < bars.ma50 * 0.90:
            continue
        # 이미 한 번 돌파에 성공한 종목이 쉬는 자리이므로 베이스는 신고가 근처에 생긴다.
        if window_high < bars.high52 * 0.90:
            continue
        candidates.append((length, start, window_high, window_low, width))
    if not candidates:
        return _none('flat')
    # 가장 좁은 폭을 고르고, 폭이 1%p 안에서 비슷하면 그중 긴 구간을 택한다.
    tightest = min(c[4] for c in candidates)
    length, start, window_high, window_low, width = max(
        (c for c in candidates if c[4] <= tightest + 1.0), key=lambda c: c[0])
    inside = range(start, bars.n - 1)
    up = [bars.volumes[i] for i in inside if bars.closes[i] > bars.closes[i - 1]]
    down = [bars.volumes[i] for i in inside if bars.closes[i] < bars.closes[i - 1]]
    quiet = bool(up and down and _mean(down) <= _mean(up))
    advance = bars.advance_before(start, 90)
    if not (advance is not None and advance >= 20 and quiet):
        return _none('flat')
    pivot = window_high
    confirmed = _confirm(bars, pivot)
    status, distance = _stage(bars, pivot, confirmed)
    label = {'BREAKOUT': '플랫 베이스 돌파', 'READY': '베이스 고점 근접', 'WATCH': '플랫 베이스 형성'}[status]
    return _result(
        'flat', status, label, pivot, window_low, distance, length,
        {'widthPct': _r(width, 1), 'priorAdvancePct': _r(advance, 1),
         'downVolumeRatio': _r(_mean(down) / _mean(up)) if up and down and _mean(up) else None,
         'breakoutVolumeRatio': _r(bars.volume_ratio())},
        FLAT_CRITERION, FLAT_CAVEAT)


# ── 더블바텀 ────────────────────────────────────────────────────────────────
DBL_CRITERION = ('200일선 위(하락 추세 제외) · 앞선 상승 추세 · 두 바닥 가격차 6% 이내 · '
                 '가운데 봉우리가 왼쪽 고점보다 낮고 두 바닥보다 10%↑ 높음 · 전체 35거래일↑ · '
                 '가운데 봉우리 종가 돌파로 확정')
DBL_CAVEAT = ('바닥 모양(뾰족한 아담 / 둥근 이브)은 구분하지 않는다. 셋업북 통계는 둥근 바닥이 '
              '더 좋았다고 적었으므로 모양은 차트에서 확인해야 한다.')


def detect_dbl(bars):
    """더블바텀. 피벗은 가운데 봉우리, 언더컷이면 셋업북대로 가산 표시만 남긴다."""
    # 셋업북의 실패 조건: 하락 추세 한가운데의 W는 대상이 아니다. 머리 위 매물이 두꺼워
    # 가운데 봉우리를 넘어도 이어지지 않는다.
    if bars.ma200 and bars.close < bars.ma200:
        return _none('dbl')
    events = bars.swings
    for j in range(len(events) - 3, -1, -1):
        kinds = [events[j + k][0] for k in range(3)]
        if kinds != ['T', 'P', 'T']:
            continue
        t1, mid, t2 = (events[j + k][1] for k in range(3))
        # 셋업북: 전체 기간 7주(35거래일) 이상이고, 두 바닥이 몇 주로 너무 가까우면 신뢰도가 낮다.
        if bars.n - 1 - t2 > 40 or bars.n - 1 - t1 < 35 or t2 - t1 < 15:
            continue
        low1, low2, peak = bars.lows[t1], bars.lows[t2], bars.highs[mid]
        if low1 <= 0 or low2 <= 0 or peak <= 0:
            continue
        gap = abs(low2 - low1) / low1 * 100
        # 베이스 왼쪽 고점은 첫 바닥으로 떨어지기 전의 실제 고점이다. 직전 스윙 고점만
        # 쓰면 바닥 안쪽의 잡음 고점을 집어 가운데 봉우리와 잘못 비교한다.
        window = range(max(0, t1 - 60), t1)
        if not window:
            continue
        left_index = max(window, key=lambda i: bars.highs[i])
        left_high = bars.highs[left_index]
        # 앞선 상승은 그 고점에서 잰다. 첫 바닥에서 재면 이미 되돌려진 가격이다.
        advance = bars.advance_before(left_index)
        # 가운데 봉우리가 두 바닥보다 뚜렷이 높아야 W이고, 아니면 그냥 바닥의 잔 흔들림이다.
        prominence = (peak / max(low1, low2) - 1) * 100
        checks = {
            '두 바닥 6% 이내': gap <= 6,
            '가운데 봉우리가 왼쪽 고점보다 낮음': bool(left_high and peak < left_high),
            '앞선 상승 추세': bool(advance is not None and advance >= 20),
            '가운데 봉우리 10%↑ 돌출': prominence >= 10,
        }
        if not all(checks.values()):
            continue
        pivot = peak
        confirmed = _confirm(bars, pivot)
        status, distance = _stage(bars, pivot, confirmed)
        label = {'BREAKOUT': '더블바텀 확정', 'READY': '가운데 봉우리 근접', 'WATCH': 'W 바닥 형성'}[status]
        return _result(
            'dbl', status, label, pivot, low2, distance, t2 - t1,
            {'bottomGapPct': _r(gap, 1), 'undercut': bool(low2 < low1),
             'prominencePct': _r(prominence, 1),
             'priorAdvancePct': _r(advance, 1), 'firstBottom': bars.dates[t1],
             'secondBottom': bars.dates[t2], 'breakoutVolumeRatio': _r(bars.volume_ratio()),
             'checks': checks},
            DBL_CRITERION, DBL_CAVEAT)
    return _none('dbl')


# ── 하이 타이트 플래그 ──────────────────────────────────────────────────────
HTF_CRITERION = ('20~40거래일 안 +90%↑ 급등 · 급등 직전 급락 아님 · 깃발 15~25거래일·'
                 '하락폭 10~25% · 깃발 거래량 급감 · 깃발 최고가 돌파')
HTF_CAVEAT = ('셋업북은 이 패턴이 불코우스키 최신 순위에서 39개 중 30위로 떨어졌고 변동성이 크다고 '
              '적었다. 급등 직전 급락했던 종목의 성과가 가장 나빴다는 점도 함께 본다.')


def detect_htf(bars):
    """하이 타이트 플래그. 깃대(급등) 뒤 촘촘한 깃발만 통과시킨다."""
    if bars.n < 70:
        return _none('htf')
    for flag_len in range(15, 26):
        flag_start = bars.n - flag_len
        if flag_start < 45:
            continue
        pole_top = max(bars.highs[max(0, flag_start - 3):flag_start + 1])
        best = None
        for pole_len in range(20, 41):
            pole_start = flag_start - pole_len
            if pole_start < 5:
                continue
            pole_low = min(bars.lows[pole_start:flag_start])
            if pole_low <= 0:
                continue
            gain = (pole_top / pole_low - 1) * 100
            if gain >= 90 and (best is None or gain > best[0]):
                best = (gain, pole_start, pole_low)
        if not best:
            continue
        gain, pole_start, pole_low = best
        flag_high, flag_low = bars.base(flag_start)
        if not flag_high:
            continue
        drop = (flag_high - flag_low) / flag_high * 100
        # 셋업북: 급등 직전 추세가 완만했던 것이 좋다. 직전 급락은 실패가 가장 잦았다.
        before = bars.closes[max(0, pole_start - 40)]
        prior = (bars.closes[pole_start] / before - 1) * 100 if before > 0 else None
        ratio, dry = bars.dry_in(flag_start)
        if not (drop is not None and 10 <= drop <= 25 and dry and prior is not None and prior > -30):
            continue
        pivot = flag_high
        confirmed = _confirm(bars, pivot)
        status, distance = _stage(bars, pivot, confirmed)
        label = {'BREAKOUT': '깃발 고점 돌파', 'READY': '깃발 고점 근접', 'WATCH': '깃발 형성'}[status]
        return _result(
            'htf', status, label, pivot, flag_low, distance, flag_len,
            {'poleGainPct': _r(gain, 1), 'poleDays': flag_start - pole_start,
             'flagDropPct': _r(drop, 1), 'flagDays': flag_len,
             'flagVolumeRatio': _r(ratio), 'priorTrendPct': _r(prior, 1),
             'breakoutVolumeRatio': _r(bars.volume_ratio())},
            HTF_CRITERION, HTF_CAVEAT)
    return _none('htf')


# ── 포켓 피벗 ───────────────────────────────────────────────────────────────
PP_CRITERION = ('50일선 위·50일선 상승 · 10일선 또는 50일선 2% 이내로 눌림 · 당일 상승 · '
                '당일 거래량 > 직전 10거래일 하락일 최대 거래량')
PP_CAVEAT = ('베이스 고점 돌파 전에 먼저 사는 방법이라 신호가 잦고 더 자주 틀린다. 셋업북은 '
             '업종과 시장이 받쳐 줄 때만 쓰라고 적었다. 피벗 돌파가 아니므로 매수점은 당일 종가 '
             '근처이고 손절은 50일선 아래 종가다.')


def detect_pp(bars):
    """포켓 피벗. 셋업북의 정의가 그대로 수식이라 가장 정확하게 판정된다."""
    if bars.n < 60 or not (bars.ma50 and bars.ma10 and bars.ma50_prev):
        return _none('pp')
    if not (bars.close > bars.ma50 and bars.ma50 > bars.ma50_prev):
        return _none('pp')
    if bars.closes[-1] <= bars.closes[-2]:
        return _none('pp')
    down = [bars.volumes[i] for i in range(bars.n - 11, bars.n - 1) if bars.closes[i] < bars.closes[i - 1]]
    if not down:
        return _none('pp')
    strongest_down = max(down)
    if bars.volumes[-1] <= strongest_down:
        return _none('pp')
    near_ma = min(abs(bars.lows[i] / ma - 1) * 100
                  for i in range(bars.n - 3, bars.n) for ma in (bars.ma10, bars.ma50))
    if near_ma > 2:
        return _none('pp')
    # 셋업북: 직전 며칠이 V자 급반등이면 신뢰도가 낮다.
    rebound = (bars.close / min(bars.lows[-5:]) - 1) * 100 if min(bars.lows[-5:]) > 0 else None
    if rebound is not None and rebound >= 12:
        return _none('pp')
    return _result(
        'pp', 'BREAKOUT', '포켓 피벗 출현', None, bars.ma50, None, None,
        {'volumeVsMaxDownDay': _r(bars.volumes[-1] / strongest_down) if strongest_down else None,
         'distanceToMaPct': _r(near_ma, 1), 'volumeRatio': _r(bars.volume_ratio()),
         'reboundPct': _r(rebound, 1), 'ma10': _r(bars.ma10), 'ma50': _r(bars.ma50)},
        PP_CRITERION, PP_CAVEAT)


# ── 다바스 박스 ─────────────────────────────────────────────────────────────
DARVAS_CRITERION = ('박스 윗변이 52주 고가 95%↑ · 윗변이 3거래일↑ 깨지지 않음 · '
                    '아랫변도 3거래일↑ 유지 · 박스 폭 15% 이내 · 종가가 박스 안 · '
                    '박스 안 거래량 감소')
DARVAS_CAVEAT = ('옆걸음 장에서는 윗변 돌파와 손절이 반복된다. 셋업북의 핵심은 진입보다 관리이며, '
                 '새 박스가 생길 때마다 손절을 그 아랫변으로 올리는 것은 사람이 해야 한다.')


def detect_darvas(bars):
    """다바스 박스. 고점 3일 유지로 윗변을, 저점 3일 유지로 아랫변을 확정한다."""
    if bars.n < 60:
        return _none('darvas')
    if bars.close < bars.high52 * 0.85:
        return _none('darvas')
    # 윗변은 어제까지 형성된 고점이다. 오늘 고가를 쓰면 '3거래일 유지'가 늘 0이 된다.
    top_index = max(range(bars.n - 60, bars.n - 1), key=lambda i: bars.highs[i])
    top = bars.highs[top_index]
    held = bars.n - 1 - top_index
    # 셋업북: 신고가 근처의 강한 종목에서만 박스를 그린다. 윗변은 고점 3일 유지로 확정된다.
    if held < 3 or held > 50 or top < bars.high52 * 0.95:
        return _none('darvas')
    after = range(top_index + 1, bars.n)
    if not after:
        return _none('darvas')
    bottom_index = min(after, key=lambda i: bars.lows[i])
    bottom = bars.lows[bottom_index]
    if bars.n - 1 - bottom_index < 3 or bottom <= 0:
        return _none('darvas')
    width = (top - bottom) / top * 100
    # 폭이 넓으면 아랫변까지 거리가 멀어 손절 폭이 커진다(셋업북 실패 조건).
    if width > 15 or bars.close < bottom:
        return _none('darvas')
    ratio, dry = bars.dry_in(top_index)
    if not dry:
        return _none('darvas')
    pivot = top
    confirmed = _confirm(bars, pivot)
    status, distance = _stage(bars, pivot, confirmed)
    label = {'BREAKOUT': '박스 윗변 돌파', 'READY': '박스 윗변 근접', 'WATCH': '박스 형성'}[status]
    return _result(
        'darvas', status, label, pivot, bottom, distance, held,
        {'boxWidthPct': _r(width, 1), 'boxDays': held, 'boxTopDate': bars.dates[top_index],
         'boxBottomDate': bars.dates[bottom_index], 'boxVolumeRatio': _r(ratio),
         'high52Ratio': _r(bars.close / bars.high52 * 100, 1),
         'breakoutVolumeRatio': _r(bars.volume_ratio())},
        DARVAS_CRITERION, DARVAS_CAVEAT)


# ── 상승 삼각형 ─────────────────────────────────────────────────────────────
ASC_CRITERION = ('200일선 위 · 윗변 고점 2개 이상이 서로 2% 이내(수평) · 저점 2개 이상 상승 · '
                 '거래량 감소 · 윗변 위 종가 + 거래량 30일 평균 초과')
ASC_CAVEAT = ('셋업북 중요도 ★2다. 불코우스키 자료에서 위로 돌파하는 비율이 64%뿐이고, 위로 넘어도 '
              '5~10% 오르다 무너지는 경우가 많아 그는 이 패턴을 더 이상 쓰지 않는다고 적었다.')


def detect_asc(bars):
    """상승 삼각형. 수평 윗변과 상승 아랫변의 접점 수만 센다."""
    # 셋업북: 200일선 위에 있는 종목에서만 쓴다. 200거래일 이력이 없으면 판정하지 않는다.
    if bars.n < 200 or not bars.ma200 or bars.close < bars.ma200:
        return _none('asc')
    for length in (80, 60, 45, 30):
        if bars.n <= length + 5:
            continue
        start = bars.n - length
        events = swings(bars.highs, bars.lows, 2, start)
        peaks = [i for kind, i in events if kind == 'P']
        troughs = [i for kind, i in events if kind == 'T']
        if len(peaks) < 2 or len(troughs) < 2:
            continue
        resistance = _mean([bars.highs[i] for i in peaks])
        flat = all(abs(bars.highs[i] / resistance - 1) <= 0.02 for i in peaks)
        rising = all(bars.lows[troughs[k]] > bars.lows[troughs[k - 1]] for k in range(1, len(troughs)))
        ratio, dry = bars.dry_in(start)
        if not (flat and rising and dry):
            continue
        pivot = max(bars.highs[i] for i in peaks)
        confirmed = _confirm(bars, pivot)
        status, distance = _stage(bars, pivot, confirmed)
        label = {'BREAKOUT': '윗변 돌파', 'READY': '윗변 근접', 'WATCH': '삼각형 수축'}[status]
        return _result(
            'asc', status, label, pivot, bars.lows[troughs[-1]], distance, length,
            {'touchesTop': len(peaks), 'touchesBottom': len(troughs),
             'resistance': _r(resistance), 'volumeRatio': _r(ratio),
             'breakoutVolumeRatio': _r(bars.volume_ratio())},
            ASC_CRITERION, ASC_CAVEAT)
    return _none('asc')


# ── 3C 치트 진입 ────────────────────────────────────────────────────────────
CHEAT_CRITERION = ('컵 형성 중(깊이 12~50%, 왼쪽 고점이 52주 고가 90%↑, 그 앞 상승 30%↑) · '
                   '반등이 컵 아래쪽 1/3~중간 높이에서 멈춤 · 멈춘 구간 5거래일↑·폭 8% 이내·'
                   '거래량 건조 · 그 구간 고점 돌파')
CHEAT_CAVEAT = ('컵이 완성되지 않은 자리라 일반 돌파보다 실패가 잦다. 위쪽 옛 고점의 매물을 아직 '
                '소화하지 않았으므로 셋업북대로 비중을 작게 시작하고, 약한 장에서는 쓰지 않는다.')


def detect_cheat(bars):
    """3C 치트. 컵 오른쪽을 올라오다 중간에 멈춘 작은 베이스를 찾는다."""
    if bars.n < 60:
        return _none('cheat')
    peaks = [i for kind, i in bars.swings if kind == 'P']
    for left in peaks:
        if bars.n - left < 30:
            continue
        lows = bars.lows[left:]
        bottom = left + lows.index(min(lows))
        peak, trough = bars.highs[left], bars.lows[bottom]
        if peak <= 0 or trough <= 0 or bars.n - 1 - bottom < 10:
            continue
        depth = (peak - trough) / peak * 100
        if not 12 <= depth <= 50:
            continue
        # 셋업북: 2단계 종목이 '컵'을 만들고 있어야 한다. 컵 왼쪽 고점은 신고가권의 실제 고점이고,
        # 그 앞에는 30% 이상의 상승이 있어야 한다. 이 두 조건이 없으면 단순한 흔들림이다.
        advance = bars.advance_before(left)
        if peak < bars.high52 * 0.90 or advance is None or advance < 30:
            continue
        pause_len = bars.n - 1 - bottom
        for length in range(min(pause_len, 30), 4, -1):
            start = bars.n - length
            if start <= bottom:
                continue
            high, low = bars.base(start)
            if not high or high <= 0:
                continue
            width = (high - low) / high * 100
            if width > 8:
                continue
            # 셋업북: 컵 아래쪽 1/3 ~ 중간 높이에서 멈춰야 한다.
            level = (high - trough) / (peak - trough) if peak > trough else None
            if level is None or not 0.25 <= level <= 0.60:
                continue
            ratio, dry = bars.dry_in(start)
            if not dry:
                continue
            pivot = high
            confirmed = _confirm(bars, pivot)
            status, distance = _stage(bars, pivot, confirmed)
            label = {'BREAKOUT': '치트 구간 돌파', 'READY': '치트 구간 고점 근접',
                     'WATCH': '치트 구간 형성'}[status]
            return _result(
                'cheat', status, label, pivot, low, distance, length,
                {'cupDepthPct': _r(depth, 1), 'cupLevelPct': _r(level * 100, 1),
                 'pauseDays': length, 'pauseWidthPct': _r(width, 1),
                 'pauseVolumeRatio': _r(ratio), 'cupBottomDate': bars.dates[bottom],
                 'priorAdvancePct': _r(advance, 1),
                 'breakoutVolumeRatio': _r(bars.volume_ratio())},
                CHEAT_CRITERION, CHEAT_CAVEAT)
    return _none('cheat')


DETECTORS = (detect_cup, detect_flat, detect_dbl, detect_htf, detect_pp,
             detect_darvas, detect_asc, detect_cheat)


# ── VCP·추세 템플릿 (수집 단계가 계산해 둔 값을 그대로 쓴다) ────────────────
VCP_CAVEAT = ('수집 단계의 detect_vcp 결과를 그대로 쓴다. 국지 고저점 기반 정량 후보이므로 '
              '베이스 길이와 저항선은 차트에서 최종 확인해야 한다.')


def vcp_setup(vcp, bars):
    """stock['vcp'] 를 다른 셋업과 같은 모양으로 옮긴다. 기준을 다시 계산하지 않는다."""
    if not isinstance(vcp, dict) or vcp.get('status') not in LIVE:
        return _none('vcp')
    pivot = _f(vcp.get('pivot'))
    # 피벗이 없으면 넘어야 할 가격이 없다는 뜻이므로 화면에 올리지 않는다.
    if not pivot or pivot <= 0:
        return _none('vcp')
    contractions = vcp.get('contractions') or []
    stop = _f(contractions[-1]['trough']) if contractions and isinstance(contractions[-1], dict) else None
    status = vcp['status']
    return _result(
        'vcp', status, vcp.get('label') or NAMES['vcp'], pivot, stop,
        _f(vcp.get('distanceToPivotPct')), None,
        {'contractions': len(contractions),
         'depths': [c.get('depthPct') for c in contractions if isinstance(c, dict)],
         'shrinking': vcp.get('shrinking'), 'volumeDryRatio': vcp.get('volumeDryRatio'),
         'range10Pct': vcp.get('range10Pct'),
         'breakoutVolumeRatio': vcp.get('breakoutVolumeRatio')},
        VCP_CRITERION, VCP_CAVEAT)


def stage_gate(checks, core):
    """셋업북 ①-2: 2단계 상승 추세 종목만 본다. 셋업이 아니라 전제 조건이다."""
    if not isinstance(checks, dict) or not checks:
        return None
    passed = sum(1 for v in checks.values() if v)
    return {'pass': bool(core), 'passed': passed, 'total': len(checks),
            'failed': [k for k, v in checks.items() if not v]}


def detect_patterns(rows, vcp=None, stage_checks=None, stage_core=None):
    """한 종목의 셋업 판정. 진행 중인 셋업만 담아 돌려준다(없으면 None)."""
    if not isinstance(rows, list) or len(rows) < MIN_ROWS:
        return None
    bars = Bars(rows)
    setups = [vcp_setup(vcp, bars)]
    for detector in DETECTORS:
        try:
            setups.append(detector(bars))
        except (ValueError, ZeroDivisionError, IndexError, KeyError, TypeError):
            setups.append(_none(detector.__name__.replace('detect_', '')))
    live = [s for s in setups if s.get('status') in LIVE]
    gate = stage_gate(stage_checks, stage_core)
    if not live:
        return {'asOf': bars.dates[-1], 'setups': [], 'stage': gate} if gate else None
    live.sort(key=lambda s: (RANK[s['status']], s['importance'],
                             -(s['distanceToPivotPct'] if s['distanceToPivotPct'] is not None else 99)),
              reverse=True)
    best = live[0]
    return {
        'asOf': bars.dates[-1],
        'status': best['status'],
        'best': best['key'],
        'count': len(live),
        'breakoutCount': sum(1 for s in live if s['status'] == 'BREAKOUT'),
        'setups': live,
        'stage': gate,
    }


# ── 고정 설명과 압축 ────────────────────────────────────────────────────────
STAGE_CRITERION = ('주가>150·200일선 · 150일선>200일선 · 200일선 상승 · 50일선>150·200일선 · '
                   '주가>50일선 · 52주 저점 대비 +30% · 52주 고점 25% 이내')
STAGE_CAVEAT = ('셋업이 아니라 전제 조건이다(셋업북 ①-2). 수집 단계의 stage2Checks 를 그대로 '
                '쓴다. 3단계 천장은 2단계와 모양이 비슷하므로 거래량을 동반한 큰 하락일이 '
                '잦아지면 차트에서 천장을 의심해야 한다.')
# detect_vcp 가 내보내는 문장과 같아야 한다(tests/test_patterns.py 가 일치를 검사한다).
VCP_CRITERION = ('2개 이상 조정깊이 연속 축소(각 ≤이전의 88%, 마지막 ≤15%) + '
                 '최근 10일 거래량 ≤50일의 80% + 10일 가격폭 ≤12%')

CRITERIA = {
    'stage': STAGE_CRITERION, 'vcp': VCP_CRITERION, 'cup': CUP_CRITERION,
    'flat': FLAT_CRITERION, 'dbl': DBL_CRITERION, 'htf': HTF_CRITERION, 'pp': PP_CRITERION,
    'darvas': DARVAS_CRITERION, 'asc': ASC_CRITERION, 'cheat': CHEAT_CRITERION,
}
CAVEATS = {
    'stage': STAGE_CAVEAT, 'vcp': VCP_CAVEAT, 'cup': CUP_CAVEAT,
    'flat': FLAT_CAVEAT, 'dbl': DBL_CAVEAT, 'htf': HTF_CAVEAT, 'pp': PP_CAVEAT,
    'darvas': DARVAS_CAVEAT, 'asc': ASC_CAVEAT, 'cheat': CHEAT_CAVEAT,
}
# 종목마다 반복하면 스냅샷이 커진다. 화면은 meta() 를 한 번 읽어 쓴다.
STATIC_KEYS = ('name', 'master', 'importance', 'criterion', 'caveat')


# 셋업북(추세추종 셋업북 2판)의 '한 줄 논리'와 '모양 설명'. 화면의 패턴 설명에 쓴다.
LOGIC = {
    'stage': '이미 수요가 공급을 이기고 있는 종목만 고른다',
    'vcp': '팔 사람이 단계마다 줄어든 흔적을 읽는다',
    'cup': '둥근 바닥으로 겁먹은 매도자를, 핸들로 본전 매도자를 정리한다',
    'flat': '크게 오른 뒤에도 보유자가 팔지 않는다는 증거',
    'dbl': '두 번째 바닥에서 손절 물량을 털어내고 올라온다',
    'htf': '두 배 오른 뒤에도 차익 매물이 안 나오는 극단적 수요',
    'pp': '매도 압력을 압도하는 하루치 매수 거래량에서 기관을 본다',
    'darvas': '균형이 사는 쪽으로 깨진 순간만 타고, 박스를 따라 손절을 올린다',
    'cheat': '컵 중간의 작은 베이스에서 짧은 손절로 먼저 들어간다',
    'asc': '같은 가격의 매도 물량을 점점 높은 가격의 매수가 받아낸다',
}
DESCRIPTIONS = {
    'stage': ('모든 셋업에 앞서 거치는 필터입니다. 주가는 바닥(1단계) → 상승(2단계) → 천장(3단계) → '
              '하락(4단계)을 돕니다. 추세추종은 2단계에서만 사고, 3단계 신호가 나오면 줄이고, 4단계에서는 '
              '갖고 있지 않습니다. 주가가 150일선과 200일선 위에 있고 그 선들이 위로 기울어 있다는 것은 지난 '
              '반년~일 년 동안 산 사람 대부분이 이익 상태라 급하게 팔 이유가 없다는 뜻입니다.'),
    'vcp': ('앞선 상승 뒤 쉬는 구간에서 하락폭이 점점 줄어드는 모양입니다. 예를 들어 −30% → −16% → −8%처럼 '
            '흔들림이 작아지고, 마지막에는 거래량이 말라붙습니다. 같은 자리까지 올라와도 내놓는 물량이 단계마다 '
            '줄었다는 뜻이라 팔 사람이 거의 다 팔았다고 봅니다. 마지막 수축의 폭이 좁아 손절을 3~5%에 둘 수 '
            '있는 것이 이 셋업의 실질적 힘입니다.'),
    'cup': ('둥근 U자 모양 컵이 만들어진 뒤 오른쪽 위에서 짧게 쉬는 손잡이(핸들)가 붙은 모양입니다. 몇 주에 '
            '걸쳐 천천히 바닥을 다지는 동안 왼쪽 고점에서 물린 사람 중 겁먹은 쪽이 팔고 나가고, 컵 오른쪽이 '
            '옛 고점 근처까지 오면 끝까지 버틴 사람들이 "본전이다" 하고 팝니다. 핸들은 이 마지막 물량을 받아내는 '
            '구간이고, 핸들 고점을 거래량과 함께 넘으면 머리 위 매도자가 거의 없는 상태가 됩니다.'),
    'flat': ('큰 상승 뒤에 주가가 거의 옆으로만 움직이며 쉬는 모양입니다. 20~30% 오른 직후라 누구나 이익을 '
             '실현할 수 있는데도 5주 넘게 고점과 저점 차이가 15% 안에서 머문다는 것은, 팔 수 있는 사람들이 '
             '팔지 않기로 했다는 뜻입니다. 이미 한 번 돌파에 성공한 종목이 쉬는 자리라 첫 돌파보다 믿을 만한 '
             '경우가 많고, 폭이 좁아 손절선도 가깝습니다.'),
    'dbl': ('W 모양입니다. 첫 바닥 바로 아래에는 손절 주문이 쌓이는데, 두 번째 하락이 첫 바닥을 살짝 깨면 그 '
            '물량이 한꺼번에 쏟아집니다. 주가가 그 물량을 다 받아내고 다시 올라왔다면 가장 약한 손들이 가장 '
            '나쁜 가격에 팔고 나간 것입니다. 그래서 두 번째 바닥이 첫 바닥을 살짝 밑도는 언더컷을 오히려 좋게 '
            '보고, 가운데 봉우리를 종가로 넘는 날 패턴이 확정됩니다.'),
    'htf': ('2달 안에 주가가 거의 두 배 오른 뒤 3~5주 동안 20~25% 이내로만 쉬는 모양입니다. 보유자 전원이 '
            '큰 이익 상태인데도 차익 매물이 거의 나오지 않는다는 것은, 팔려는 사람보다 더 높은 가격에 사려는 '
            '사람이 훨씬 많다는 뜻입니다. 가장 강한 종목에서만 나오며 아주 드뭅니다. 변동성이 커서 실패할 때 '
            '낙폭도 큽니다.'),
    'pp': ('베이스 고점을 넘기 전에도, 이동평균선 근처에서 거래량이 강하게 터지며 오르는 날을 기관 매수의 '
           '흔적으로 보고 미리 삽니다. 그날 상승 거래량이 직전 열흘 동안 어떤 하락일 거래량보다 크다면, 최근 '
           '팔았던 사람들 전체보다 더 많은 돈이 하루에 들어왔다는 뜻입니다. 정식 돌파보다 일찍 사는 대신 신호가 '
           '더 자주 틀립니다.'),
    'darvas': ('주가가 일정한 상자(박스) 안에서 움직이다 윗변을 뚫으면 사고, 새 상자가 만들어질 때마다 손절선을 '
               '그 아랫변으로 올립니다. 윗변은 "이 가격이면 판다"는 사람들이 서 있는 자리이고, 그것이 뚫렸다는 '
               '것은 그 물량이 다 소화됐다는 뜻입니다. 진입보다 관리가 핵심이라, 추세가 이어지는 동안은 자동으로 '
               '보유하고 꺾이는 순간 자동으로 팔게 됩니다.'),
    'cheat': ('컵앤핸들이 다 완성되기 전에, 컵 오른쪽을 올라오다 중간쯤에서 잠깐 옆으로 쉬는 작은 구간에서 먼저 '
              '사는 방법입니다. 그 높이의 매물이 정리된 작은 베이스를 넘는 순간 다음 매물대까지는 비어 있습니다. '
              '정식 핸들 돌파보다 10~20% 낮은 가격에 손절 5% 안팎으로 들어가지만, 컵이 완성되지 않은 자리라 '
              '실패가 잦아 비중을 작게 시작합니다.'),
    'asc': ('고점은 같은 가격에서 계속 막히고 저점은 점점 높아지는 삼각형입니다. 수평 윗변의 매도 물량을 점점 '
            '높은 가격의 매수자가 받아내고 있다는 해석입니다. 다만 논리와 통계가 어긋나는 대표적인 패턴으로, '
            '불코우스키 자료에서 위로 돌파한 비율이 64%에 그쳤고 위로 넘어도 5~10% 오르다 무너지는 경우가 '
            '많았습니다.'),
}


def meta():
    """셋업별 고정 설명. 스냅샷에 한 번만 싣는다."""
    return {key: {'name': NAMES[key], 'master': MASTERS[key], 'importance': IMPORTANCE[key],
                  'logic': LOGIC[key], 'description': DESCRIPTIONS[key],
                  'criterion': CRITERIA[key], 'caveat': CAVEATS[key]}
            for key in NAMES}


def compact(result):
    """스냅샷에 실을 모양. 고정 설명을 떼고 진행 중인 셋업만 남긴다."""
    if not result or not result.get('setups'):
        return None
    trimmed = []
    for setup in result['setups']:
        trimmed.append({k: v for k, v in setup.items() if k not in STATIC_KEYS})
    return {**{k: v for k, v in result.items() if k != 'setups'}, 'setups': trimmed}
