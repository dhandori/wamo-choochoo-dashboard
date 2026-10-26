import contextlib
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import wamo_alerts as alerts
import wamo_run as runner
import wamo_runtime as runtime
import wamo_trend as trend
import wamo_update_business_dart as core

UTC = timezone.utc


def sessions(n, end='2026-10-16'):
    out, d = [], date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def stock(symbol, closes, volumes=None, dates=None):
    dates = dates or sessions(len(closes))
    volumes = volumes or [1000] * len(closes)
    rows = [{'date': d, 'close': c, 'high': c * 1.01, 'low': c * 0.99, 'volume': v}
            for d, c, v in zip(dates, closes, volumes)]
    return {'id': f'US:{symbol}', 'symbol': symbol, 'ticker': symbol, 'name': symbol, 'industry': 'Test',
            'marketCap': 1e9, 'rows': rows, 'closes': [r['close'] for r in rows], 'rsPercentile': None, 'vcp': None}


class TrendMathTests(unittest.TestCase):
    def test_stage_two_and_four(self):
        up = [100 + i for i in range(260)]
        down = [400 - i for i in range(260)]
        self.assertEqual(trend.stage_at(up, 259), 2)
        self.assertEqual(trend.stage_at(down, 259), 4)
        self.assertIsNone(trend.stage_at(up, 100))

    def test_distribution_days_count_and_expire(self):
        dates = sessions(26)
        bench = [{'date': d, 'close': 100.0, 'volume': 100.0} for d in dates]
        bench[5] = {'date': dates[5], 'close': 99.0, 'volume': 150.0}     # distribution
        for k in range(6, 26):
            bench[k] = {'date': dates[k], 'close': 99.0, 'volume': 100.0}
        bench[20] = {'date': dates[20], 'close': 98.0, 'volume': 200.0}   # distribution
        out = trend.distribution_days(bench)
        self.assertEqual(out['count'], 2)
        bench[24] = {'date': dates[24], 'close': 104.0, 'volume': 90.0}   # +5% above day 5 and day 20
        bench[25] = {'date': dates[25], 'close': 103.0, 'volume': 80.0}
        self.assertEqual(trend.distribution_days(bench)['count'], 0)

    def test_distribution_close_position_sets_risk(self):
        from wamo_ftd import distribution_kind
        prev = {'close': 100.0, 'volume': 100.0, 'high': 101, 'low': 99}
        down = lambda close, high, low: {'close': close, 'high': high, 'low': low, 'volume': 150.0}
        self.assertEqual(distribution_kind(prev, down(98.0, 100.0, 97.9)), 'HEAVY')    # closed near the low
        self.assertEqual(distribution_kind(prev, down(99.0, 100.0, 97.0)), 'LIGHT')    # recovered off the low
        self.assertEqual(distribution_kind(prev, down(98.5, 100.0, 97.5)), 'NORMAL')   # middle
        # Up close on more volume but finished in the bottom third: late selling (disguised).
        self.assertEqual(distribution_kind(prev, down(100.3, 102.0, 100.0)), 'HIDDEN')
        self.assertIsNone(distribution_kind(prev, down(101.6, 102.0, 100.0)))          # strong close
        self.assertIsNone(distribution_kind(prev, down(100.1, 100.3, 100.0)))          # tiny range: noise
        self.assertIsNone(distribution_kind(prev, {**down(100.3, 102.0, 100.0), 'volume': 90.0}))  # lighter volume
        self.assertEqual(distribution_kind(prev, {'close': 99.0, 'volume': 150.0}), 'NORMAL')       # no range data

    def test_distribution_days_score_by_kind(self):
        dates = sessions(26)
        bench = [{'date': d, 'close': 100.0, 'high': 100.5, 'low': 99.5, 'volume': 100.0} for d in dates]
        bench[5] = {'date': dates[5], 'close': 99.0, 'high': 100.2, 'low': 98.9, 'volume': 150.0}    # heavy
        bench[10] = {'date': dates[10], 'close': 100.0, 'high': 100.5, 'low': 99.5, 'volume': 100.0}
        bench[11] = {'date': dates[11], 'close': 99.6, 'high': 100.0, 'low': 98.0, 'volume': 150.0}  # light
        bench[15] = {'date': dates[15], 'close': 100.0, 'high': 100.5, 'low': 99.5, 'volume': 100.0}
        bench[16] = {'date': dates[16], 'close': 100.2, 'high': 101.5, 'low': 100.0, 'volume': 150.0}  # hidden
        for k in (6, 12, 17):
            bench[k]['volume'] = 100.0
        out = trend.distribution_days(bench)
        self.assertEqual(out['count'], 3)
        self.assertEqual(out['kinds'], {'HEAVY': 1, 'NORMAL': 0, 'LIGHT': 1, 'HIDDEN': 1, 'STALL': 0})
        self.assertEqual(out['score'], 3.0)
        day = next(x for x in out['days'] if x['date'] == dates[16])
        self.assertEqual((day['kind'], day['closePos']), ('HIDDEN', 13))
        self.assertEqual((day['volume'], day['prevVolume'], day['volumeRatio']), (150.0, 100.0, 1.5))

    def test_stall_day_counts_and_strong_days_raise_the_stage(self):
        from wamo_ftd import distribution_kind, watch_reason
        bar = lambda c, v, hi=None, lo=None: {'close': c, 'high': hi or c * 1.005, 'low': lo or c * 0.995, 'volume': v}
        prev = bar(100, 100)
        stall = bar(100.05, 130, 100.3, 99.9)                                      # +0.05%, closes low in range
        self.assertEqual(distribution_kind(prev, stall), 'STALL')                  # near a high: counted
        self.assertIsNone(distribution_kind(prev, stall, advancing=False))         # in a decline: not a stall
        self.assertIn('고점 근처가 아니라서', watch_reason(prev, stall, advancing=False))
        small = bar(100.05, 103, 100.3, 99.9)                                      # only +3% volume
        self.assertIsNone(distribution_kind(prev, small))
        self.assertIn('5% 미만', watch_reason(prev, small))
        dip = bar(99.9, 130)                                                       # -0.1%: under the 0.2% rule
        self.assertIsNone(distribution_kind(prev, dip))
        self.assertIn('0.2% 미만', watch_reason(prev, dip))
        high_close = bar(100.05, 130, 100.1, 99.5)                                 # flat but closes near the top
        self.assertIsNone(distribution_kind(prev, high_close))
        self.assertIn('위쪽', watch_reason(prev, high_close))
        self.assertIsNone(distribution_kind(prev, bar(100.05, 90)))                 # less volume: nothing at all
        self.assertIsNone(watch_reason(prev, bar(100.05, 90)))
        self.assertIsNone(distribution_kind(prev, bar(100.5, 130, 100.6, 99.9)))   # a real up day
        # bottom 25% of the range = strong (HEAVY); 30% is an ordinary distribution day
        self.assertEqual(distribution_kind(prev, {'close': 99.2, 'high': 100, 'low': 99.0, 'volume': 130}), 'HEAVY')
        self.assertEqual(distribution_kind(prev, {'close': 99.3, 'high': 100, 'low': 99.0, 'volume': 130}), 'NORMAL')
        st = trend.distribution_stage
        self.assertEqual([st(n, 0)['key'] for n in (0, 2, 3, 4, 5, 6, 9)],
                         ['HEALTHY', 'HEALTHY', 'CAUTION', 'WARNING', 'WARNING', 'DANGER', 'DANGER'])
        self.assertEqual((st(3, 2)['key'], st(3, 2)['bumped']), ('WARNING', True))   # 3번 중 강한 날 2번 → 한 단계 위
        self.assertEqual(st(5, 3)['key'], 'DANGER')
        self.assertFalse(st(4, 1)['bumped'])
        self.assertEqual(st(7, 7)['key'], 'DANGER')

    def test_up_days_on_falling_volume_are_an_auxiliary_warning(self):
        dates = sessions(26)
        bench = [{'date': d, 'close': 100.0 + i, 'high': 101.0 + i, 'low': 99.5 + i, 'volume': 1000.0 - i * 10}
                 for i, d in enumerate(dates)]
        out = trend.distribution_days(bench)
        self.assertEqual(out['count'], 0)                       # never counted as distribution
        self.assertEqual((out['thinRally']['upDays5'], out['thinRally']['thinDays5']), (5, 5))
        self.assertTrue(out['thinRally']['warn'])

    def test_follow_through_day_wipes_earlier_distribution_days(self):
        dates = sessions(26)
        bench = [{'date': d, 'close': 100.0, 'high': 100.5, 'low': 99.5, 'volume': 100.0} for d in dates]
        for k in (5, 15):
            bench[k] = {'date': dates[k], 'close': 99.0, 'high': 100.2, 'low': 98.9, 'volume': 150.0}
            bench[k + 1]['volume'] = 100.0
        self.assertEqual(trend.distribution_days(bench)['count'], 2)
        out = trend.distribution_days(bench, reset_on=dates[10])
        self.assertEqual(out['count'], 1)                                          # only the day after the FTD remains
        gone = next(x for x in out['days'] if x['date'] == dates[5])
        self.assertEqual((gone['expired'], gone['expiredByFtd']), (True, dates[10]))

    def test_distribution_days_need_volume(self):
        bench = [{'date': d, 'close': 100.0, 'volume': None} for d in sessions(30)]
        self.assertEqual(trend.distribution_days(bench)['status'], 'UNAVAILABLE')

    def test_weather_levels(self):
        good_idx = {'aboveMa50': True, 'aboveMa200': True}
        dist = {'status': 'OK', 'count': 1}
        br = {'status': 'OK', 'above50Pct': 70}
        hl = {'status': 'OK', 'newHighs': 40, 'newLows': 2, 'denominator': 500}
        self.assertEqual(trend.weather(good_idx, dist, br, hl, False)['level'], 'GREEN')
        bad = trend.weather({'aboveMa50': False, 'aboveMa200': False}, {'status': 'OK', 'count': 7},
                            {'status': 'OK', 'above50Pct': 20}, {'status': 'OK', 'newHighs': 1, 'newLows': 50, 'denominator': 500}, False)
        self.assertEqual(bad['level'], 'RED')
        few_highs = trend.weather(good_idx, dist, br, {'status': 'OK', 'newHighs': 2, 'newLows': 0, 'denominator': 500}, False)
        self.assertEqual([f['score'] for f in few_highs['factors'] if f['key'] == 'highsLows'], [0])

    def test_index_pullback_is_neutral_and_secondary_index_counts(self):
        dist = {'status': 'OK', 'count': 1}
        br = {'status': 'OK', 'above50Pct': 50}
        hl = {'status': 'OK', 'newHighs': 0, 'newLows': 0, 'denominator': 500}
        def score(primary, secondary=None, d=dist):
            w = trend.weather(primary, d, br, hl, False, secondary)
            return {f['key']: f['score'] for f in w['factors']}, w
        up = {'aboveMa50': True, 'aboveMa200': True, 'label': 'S&P 500'}
        pullback = {'aboveMa50': False, 'aboveMa200': True, 'label': '나스닥'}
        broken = {'aboveMa50': False, 'aboveMa200': False, 'label': '나스닥'}
        self.assertEqual(score({**pullback, 'label': 'S&P 500'})[0]['index'], 0)
        self.assertEqual(score(up)[0]['index'], 1)
        self.assertEqual(score(up, pullback)[0]['index'], 0)
        s, w = score(up, broken)
        self.assertEqual(s['index'], -1)
        self.assertIn('나스닥 200일선 아래', next(f['text'] for f in w['factors'] if f['key'] == 'index'))
        heavy = {**up, 'distribution': {'status': 'OK', 'count': 7}}
        s, w = score(up, heavy)
        self.assertEqual(s['distribution'], -1)
        self.assertIn('S&P 500 1번', next(f['text'] for f in w['factors'] if f['key'] == 'distribution'))
        # Weather judges the risk score: 4 days that all closed near the low (4 x 1.5 = 6) is a warning.
        full = {**up, 'distribution': {'status': 'OK', 'count': 4, 'score': 6.0, 'kinds': {'HEAVY': 4}}}
        s, w = score(up, full)
        self.assertEqual(s['distribution'], -1)
        self.assertIn('꽉 찬 음봉 4', next(f['text'] for f in w['factors'] if f['key'] == 'distribution'))

    def test_moving_average_slope_direction_and_score(self):
        up = [100 + i * 0.3 for i in range(260)]
        down = [300 - i * 0.3 for i in range(260)]
        flat = [100.0] * 260
        self.assertEqual(trend.ma_slope(up, 50)[1], 'up')
        self.assertEqual(trend.ma_slope(down, 200)[1], 'down')
        self.assertEqual(trend.ma_slope(flat, 200), (0.0, 'flat'))
        dist = {'status': 'OK', 'count': 1}
        br = {'status': 'OK', 'above50Pct': 50}
        hl = {'status': 'OK', 'newHighs': 0, 'newLows': 0, 'denominator': 500}
        def slope_score(i, j=None):
            w = trend.weather(i, dist, br, hl, False, j)
            return next(f['score'] for f in w['factors'] if f['key'] == 'slope')
        rising = {**trend.index_trend([{'date': f'd{k}', 'close': c} for k, c in enumerate(up)]), 'label': 'A'}
        falling = {**trend.index_trend([{'date': f'd{k}', 'close': c} for k, c in enumerate(down)]), 'label': 'B'}
        self.assertEqual(slope_score(rising), 1)
        self.assertEqual(slope_score(falling), -1)
        self.assertEqual(slope_score(rising, falling), -1)

    def test_heavy_fifty_day_break_and_sell_warning(self):
        closes = [100 + i * 0.5 for i in range(259)] + [180.0]
        volumes = [1000] * 259 + [5000]
        s = stock('BRK', closes, volumes)
        self.assertTrue(trend.heavy_ma50_break_at(s['rows'], s['closes'], 259))
        bench = [{'date': r['date'], 'close': 100.0, 'volume': None} for r in s['rows']]
        filler = [stock(f'F{k}', [50 + (k % 7) + i * 0.1 for i in range(260)]) for k in range(5)]
        leaders, warnings = trend.analyze_stocks([s, *filler], bench)
        hit = next(w for w in warnings if w['symbol'] == 'BRK')
        self.assertEqual(hit['severity'], 'HIGH')
        self.assertIn('MA50_VOLUME', [r['code'] for r in hit['reasons']])

    def test_rs_line_leader_before_price_high(self):
        dates = sessions(260)
        # Stock flat 10% below its old high while the index falls: RS line at a new high.
        closes = [100.0] * 30 + [110.0] * 10 + [99.0] * 220
        bench_closes = [100.0] * 200 + [100.0 - k for k in range(60)]
        s = stock('LEAD', closes, dates=dates)
        bench = [{'date': d, 'close': c, 'volume': None} for d, c in zip(dates, bench_closes)]
        leaders, _ = trend.analyze_stocks([s], bench)
        self.assertEqual(len(leaders), 1)
        self.assertTrue(leaders[0]['rsLineHigh'])
        self.assertTrue(leaders[0]['leading'])

    def test_signal_log_fills_forward_returns_once(self):
        dates = sessions(260)
        closes = [100.0] * 239 + [101.0 + i for i in range(21)]
        s = stock('UP', closes, dates=dates)
        bench = [{'date': d, 'close': 100.0, 'volume': None} for d in dates]
        log = {}
        book = trend.update_signal_log(log, 'US', [s], bench, dates[-1])
        self.assertTrue(book['events'])
        first = book['events'][0]
        self.assertTrue(first['backfill'])
        self.assertIn('5', first['ret'])
        again = trend.update_signal_log(log, 'US', [s], bench, dates[-1])
        self.assertEqual(len(again['events']), len(book['events']))
        card = trend.scorecard(again)
        self.assertEqual(card['status'], 'OK')

    def test_earnings_cache_uses_few_calls_and_matches_universe(self):
        calls = []
        def fetcher(day):
            calls.append(day)
            return [{'symbol': 'AAA', 'time': 'time-after-hours', 'epsForecast': None}] if day == '2026-10-20' else []
        cache, n = trend.refresh_earnings_cache({}, '2026-10-16', days=7, fetcher=fetcher,
                                                now=datetime(2026, 10, 17, tzinfo=UTC))
        self.assertEqual(n, len(calls))
        self.assertTrue(all(date.fromisoformat(d).weekday() < 5 for d in calls))
        _, n2 = trend.refresh_earnings_cache(cache, '2026-10-16', days=7, fetcher=fetcher,
                                             now=datetime(2026, 10, 17, 6, tzinfo=UTC))
        self.assertEqual(n2, 0)  # fresh cache: no network
        s = stock('AAA', [100.0] * 260)
        s['stage'], s['highDistancePct'] = 2, 5.0
        up = trend.upcoming_earnings(cache, [s], '2026-10-16')
        self.assertEqual(up[0]['daysUntil'], 4)
        self.assertEqual(up[0]['time'], '장 마감 후')

    def test_build_uses_synthetic_benchmark_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dates = sessions(260)
            stocks = []
            for k in range(40):
                closes = [100 + k + i * (0.2 + k / 100) for i in range(260)]
                stocks.append({'ticker': f'T{k}', 'name': f'T{k}', 'date': dates[-1], 'dataStatus': 'LIVE',
                               'history': [{'date': d, 'close': c, 'high': c, 'low': c, 'volume': 1000}
                                           for d, c in zip(dates, closes)]})
            payload = {'meta': {'asOf': dates[-1]}, 'stocks': stocks}
            (root / 'us.html').write_text('<script>window.WAMO_DATA = ' + json.dumps(payload) + ';</script>')
            with contextlib.redirect_stdout(io.StringIO()):
                result, built = trend.build(root, markets=('US',), fetch_network=False)
            self.assertEqual(built, ['US'])
            block = result['markets']['US']
            self.assertTrue(block['index']['synthetic'])
            self.assertEqual(block['weather']['distribution']['status'], 'UNAVAILABLE')
            self.assertIn(block['weather']['level'], ('GREEN', 'YELLOW', 'RED', 'UNKNOWN'))
            self.assertTrue((root / 'wamo_signal_log.json').exists())


class PartialRerunTests(unittest.TestCase):
    slot = datetime(2026, 10, 16, 7, tzinfo=UTC)
    now = datetime(2026, 10, 16, 14, tzinfo=UTC)

    def select(self, entry, report=None, force=False, recovery=True):
        with patch.object(runner, 'latest_slot', return_value=self.slot), \
             patch.object(runner, 'expected_session', return_value='2026-10-16'):
            state = {'KR': entry}
            status = {'KR': report or {}}
            return [m for m, _ in runner.select_targets(state, self.now, 'KR', force, recovery, status=status)]

    def test_complete_close_is_not_collected_again(self):
        entry = {'slot': self.slot.isoformat(), 'success': True, 'missingCount': 0,
                 'lastAttemptAt': (self.now - timedelta(hours=5)).isoformat()}
        self.assertEqual(self.select(entry, force=True), [])

    def test_missing_stocks_rerun_manual_and_limited_automatic(self):
        entry = {'slot': self.slot.isoformat(), 'success': True, 'missingCount': 7,
                 'lastAttemptAt': (self.now - timedelta(hours=3)).isoformat()}
        self.assertEqual(self.select(entry, force=True), ['KR'])
        self.assertEqual(self.select(entry), ['KR'])
        self.assertEqual(self.select(entry, recovery=False), [])
        self.assertEqual(self.select({**entry, 'lastAttemptAt': (self.now - timedelta(minutes=30)).isoformat()}), [])
        self.assertEqual(self.select({**entry, 'partialAttempts': 2}), [])
        self.assertEqual(self.select({**entry, 'partialAttempts': 2}, force=True), ['KR'])

    def test_older_status_files_supply_missing_count(self):
        entry = {'slot': self.slot.isoformat(), 'success': True,
                 'lastAttemptAt': (self.now - timedelta(hours=3)).isoformat()}
        report = {'asOf': '2026-10-16', 'count': 216,
                  'freshness': {'expectedSession': '2026-10-16', 'currentCount': 209}}
        self.assertEqual(runner.missing_count(entry, report), 7)
        self.assertEqual(self.select(entry, report, force=True), ['KR'])

    def run_partial(self, new_current, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('<body>old verified</body>')
            (root / 'us.html').write_text('<body>us</body>')
            state = {'KR': {'slot': self.slot.isoformat(), 'success': True, 'missingCount': 7,
                            'lastAttemptAt': (self.now - timedelta(hours=3)).isoformat()}}
            status = {'KR': {'status': 'WARNING', 'asOf': '2026-10-16', 'warnings': ['당일 신규수집 제외 7종목']}}
            (root / 'wamo_refresh_state.json').write_text(json.dumps(state))
            (root / 'wamo_refresh_status.json').write_text(json.dumps(status))

            def script(name, *args, **kwargs):
                if name == 'wamo_update_business_dart.py':
                    if fail:
                        raise runner.subprocess.CalledProcessError(1, name)
                    (root / 'index.html').write_text('<body>new data</body>')
            payload = {'meta': {'asOf': '2026-10-16'}, 'stocks': [{'date': '2026-10-16'}] * 216}
            fresh = {'warnings': [], 'currentCount': new_current}
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'STATE', root / 'wamo_refresh_state.json'), \
                 patch.object(runner, 'STATUS', root / 'wamo_refresh_status.json'), \
                 patch.object(runner, 'run_script', side_effect=script), patch.object(runner, 'publish'), \
                 patch.object(runner, 'build_trend', return_value=[]), patch.object(runner, 'refresh_insights', return_value=[]), \
                 patch.object(core, 'extract_old_payload', return_value=payload), \
                 patch.object(runtime, 'validate_freshness', return_value=fresh), \
                 patch.object(sys, 'argv', ['wamo_run.py', '--market', 'KR']), patch.dict('os.environ', {}, clear=True), \
                 patch.object(runner, 'select_targets', return_value=[('KR', self.slot)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                runner.main()
            return (json.loads((root / 'wamo_refresh_state.json').read_text())['KR'],
                    json.loads((root / 'wamo_refresh_status.json').read_text())['KR'],
                    (root / 'index.html').read_text())

    def test_improved_rerun_is_published(self):
        entry, report, html = self.run_partial(214)
        self.assertEqual(entry['missingCount'], 2)
        self.assertEqual(entry['partialAttempts'], 1)
        self.assertIn('new data', html)
        self.assertIn('7 → 2', report['partialRerun'])

    def test_worse_or_failed_rerun_keeps_verified_close(self):
        for kwargs in ({'new_current': 200}, {'new_current': 216, 'fail': True}):
            with self.subTest(**kwargs):
                entry, report, html = self.run_partial(**kwargs)
                self.assertTrue(entry['success'])
                self.assertEqual(entry['missingCount'], 7)
                self.assertEqual(entry['partialAttempts'], 1)
                self.assertEqual(report['status'], 'WARNING')
                self.assertIn('old verified', html)


class AlertTests(unittest.TestCase):
    block = {'asOf': '2026-10-16',
             'weather': {'level': 'YELLOW', 'label': '조심', 'advice': '섞여 있어요.',
                         'factors': [{'text': '지수가 50일선 아래예요.'}]},
             'rsLeaders': {'items': [{'symbol': 'AAA', 'name': 'Alpha', 'leading': True, 'highDistancePct': 8.0, 'rs': 95}]},
             'sellWarnings': {'items': [{'symbol': 'BBB', 'name': 'Beta', 'severity': 'HIGH',
                                         'reasons': [{'label': '50일선 대량 이탈'}]}]},
             'earnings': {'upcoming': [{'symbol': 'AAA', 'name': 'Alpha', 'daysUntil': 2, 'date': '2026-10-19',
                                        'time': '장 마감 후', 'inTrend': True}]}}

    def test_compose_includes_watchlist_and_sections(self):
        text = alerts.compose('US', self.block, {'US': ['aaa', 'BBB']})
        for part in ('조심', '관심 종목', 'Alpha', '50일선 대량 이탈', 'D-2', 'tab=trend'):
            self.assertIn(part, text)

    def test_no_secrets_no_send_and_daily_dedupe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'wamo_trend.json').write_text(json.dumps({'markets': {'US': self.block}}))
            sent = []
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(alerts.send_for_markets(root, ['US'], sender=lambda *a: sent.append(a), env={}), [])
                env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
                self.assertEqual(alerts.send_for_markets(root, ['US'], sender=lambda *a: sent.append(a), env=env), [alerts.STATE])
                self.assertEqual(alerts.send_for_markets(root, ['US'], sender=lambda *a: sent.append(a), env=env), [])
            self.assertEqual(len(sent), 1)


if __name__ == '__main__':
    unittest.main()


class StoredIndexFallback(unittest.TestCase):
    """Yahoo can serve an index without the latest bar after the close; use the verified stored day."""

    def test_stored_history_used_only_when_it_ends_on_the_session_with_a_full_bar(self):
        import json, tempfile
        from pathlib import Path
        dates = sessions(260)
        rows = [{'date': d, 'close': 100.0 + i * 0.1, 'high': 101.0 + i * 0.1, 'low': 99.0 + i * 0.1, 'volume': 1000.0} for i, d in enumerate(dates)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'wamo_ftd_history.json').write_text(json.dumps({'^KS11': rows}))
            self.assertEqual(trend.stored_index(root, '^KS11', dates[-1])[-1]['date'], dates[-1])
            self.assertIsNone(trend.stored_index(root, '^KS11', '2099-01-01'))   # stored data is a day behind
            rows[-1]['volume'] = None
            (root / 'wamo_ftd_history.json').write_text(json.dumps({'^KS11': rows}))
            self.assertIsNone(trend.stored_index(root, '^KS11', dates[-1]))       # incomplete bar is not used



class IndexSafeguards(unittest.TestCase):
    """Late index bars: Naver fills Korea after a cross-check; a fake index never replaces a real one."""

    def naver(self, rows, code='KOSPI', scale=1.0):
        infos = [{'localDate': r['date'].replace('-', ''), 'closePrice': r['close'] * scale, 'highPrice': r['high'] * scale,
                  'lowPrice': r['low'] * scale, 'openPrice': r['close'] * scale, 'accumulatedTradingVolume': r['volume']} for r in rows]
        return {'code': code, 'infoType': 'index', 'stockExchangeType': 'KRX', 'hasVolume': True, 'priceInfos': infos}

    def rows(self, n=60):
        return [{'date': d, 'close': 2500.0 + i, 'high': 2510.0 + i, 'low': 2490.0 + i, 'volume': 300000.0 + i}
                for i, d in enumerate(sessions(n))]

    def test_naver_adds_the_missing_session_only_after_matching_history(self):
        import wamo_ftd
        full = self.rows()
        yahoo = full[:-1]                                     # Yahoo has not posted the last session yet
        rows, added = wamo_ftd.extend_korean_latest(yahoo, '^KS11', self.naver(full), full[-1]['date'])
        self.assertEqual(added, full[-1]['date'])
        self.assertEqual(rows[-1]['close'], full[-1]['close'])
        with self.assertRaises(ValueError):                   # a source on a different scale is refused
            wamo_ftd.extend_korean_latest(yahoo, '^KS11', self.naver(full, scale=1.01), full[-1]['date'])
        with self.assertRaises(ValueError):                   # wrong index identity is refused
            wamo_ftd.extend_korean_latest(yahoo, '^KS11', self.naver(full, code='KOSDAQ'), full[-1]['date'])
        same, none = wamo_ftd.extend_korean_latest(full, '^KS11', self.naver(full), full[-1]['date'])
        self.assertIsNone(none)                               # nothing to add when Yahoo already has it

    def test_real_index_block_is_kept_instead_of_a_fake_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dates = sessions(260)
            stocks = []
            for k in range(40):
                closes = [100 + k + i * (0.2 + k / 100) for i in range(260)]
                stocks.append({'ticker': f'T{k}', 'name': f'T{k}', 'date': dates[-1], 'dataStatus': 'LIVE',
                               'history': [{'date': d, 'close': c, 'high': c, 'low': c, 'volume': 1000} for d, c in zip(dates, closes)]})
            (root / 'us.html').write_text('<script>window.WAMO_DATA = ' + json.dumps({'meta': {'asOf': dates[-1]}, 'stocks': stocks}) + ';</script>')
            real = {'asOf': dates[-2], 'index': {'synthetic': False, 'label': 'S&P 500'}, 'weather': {'label': '조심'}}
            (root / 'wamo_trend.json').write_text(json.dumps({'markets': {'US': real}}))
            with contextlib.redirect_stdout(io.StringIO()):
                result, built = trend.build(root, markets=('US',), fetch_network=False)
            kept = result['markets']['US']
            self.assertEqual(kept['asOf'], dates[-2])         # previous verified verdict stays
            self.assertFalse(kept['index']['synthetic'])
            self.assertEqual(kept['kept']['reason'], '지수 자료 지연')
            self.assertFalse((root / 'wamo_chart_US.json').exists())   # nothing built on the fake index


class FtdAlertTest(unittest.TestCase):
    def block(self, ftd_date, last_dates):
        series = [{'date': d} for d in last_dates]
        ev = {'type': 'FTD', 'date': ftd_date, 'close': 100, 'low': 98.5, 'changePct': 2.3,
              'volumeRatio': 1.2, 'rallyDay': 5, 'late': False, 'volumeVsAvg': 1.1}
        idx = {'status': 'OK', 'phase': 'CONFIRMED', 'symbol': '^KS11', 'label': '코스피',
               'series': series, 'events': [{'type': 'RALLY', 'date': '2026-10-23'}, ev]}
        return {'asOf': last_dates[-1], 'weather': {}, 'ftd': {'indexes': [idx]}}

    def test_fresh_ftd_sent_once_and_old_ftd_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': 'c'}
            sent = []
            send = lambda *a: sent.append(a[2])
            block = self.block('2026-10-29', ['2026-10-27', '2026-10-28', '2026-10-29'])
            (root / 'wamo_trend.json').write_text(json.dumps({'markets': {'KR': block}}))
            with contextlib.redirect_stdout(io.StringIO()):
                alerts.send_for_markets(root, ['KR'], sender=send, env=env)
                alerts.send_for_markets(root, ['KR'], sender=send, env=env)
            ftd_msgs = [m for m in sent if 'FTD 발생' in m]
            self.assertEqual(len(ftd_msgs), 1)
            self.assertIn('코스피', ftd_msgs[0])
            self.assertIn('tab=ftd', ftd_msgs[0])
            self.assertIn('신호 품질 3/3', ftd_msgs[0])
            old = alerts.fresh_ftds('KR', self.block('2026-04-15', ['2026-10-27', '2026-10-28', '2026-10-29']))
            self.assertEqual(old, [])

    def test_daily_message_shows_ftd_phase(self):
        block = self.block('2026-04-15', ['2026-10-26'])
        block['ftd']['indexes'][0].update(phase='RALLY', rally={'day': 2})
        self.assertIn('FTD 상태 · 코스피: 반등 시도 중 (Day 2)', alerts.compose('KR', block, {}))
