"""Regression cases for cross-view definitions and complete trend payloads."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import wamo_trend as trend
from tests.test_trend import stock

class TrendReliabilityTests(unittest.TestCase):
    def test_display_rs_uses_same_short_term_percentile_as_main_detail(self):
        s = stock('A', [100 + i for i in range(260)])
        s['rsPercentile'] = 83.6
        bench = [{'date': r['date'], 'close': 100, 'volume': 100} for r in s['rows']]
        leaders, _ = trend.analyze_stocks([s], bench)
        self.assertTrue(leaders)
        self.assertEqual(leaders[0]['rs'], 83.6)

    def test_zero_rs_is_preserved_and_not_replaced_by_other_formula(self):
        s = stock('A', [100 + i for i in range(260)])
        raw = {'meta': {'asOf': s['rows'][-1]['date']}, 'stocks': [
            {'ticker': 'A', 'history': s['rows'], 'rsPercentile': 0, 'oneilRsPercentile': 90}]}
        _, universe = trend.build_universe(raw, 'US')
        self.assertEqual(universe[0]['rsPercentile'], 0)

    def test_weakening_alignment_does_not_claim_rising_ma200_stopped(self):
        s = stock('A', [100 + i for i in range(259)] + [320])
        bench = [{'date': r['date'], 'close': 100, 'volume': 100} for r in s['rows']]
        self.assertGreater(trend.sma(s['closes'], 200, 260), trend.sma(s['closes'], 200, 240))
        _, warnings = trend.analyze_stocks([s], bench)
        reason = next(r for x in warnings for r in x['reasons'] if r['code'] == 'STAGE3')
        self.assertNotIn('상승이 멈추', reason['detail'])
        self.assertIn('50일선', reason['detail'])

    def test_earnings_include_same_day_and_use_calendar_date(self):
        universe = [stock('A', [100] * 260)]
        cache = {'days': {d: [{'symbol':'A','time':'time-after-hours'}] for d in ['2026-10-18','2026-10-19','2026-11-01','2026-11-02']}}
        rows = trend.upcoming_earnings(cache, universe, '2026-10-18')
        self.assertEqual([r['daysUntil'] for r in rows], [0, 1, 14])

    def test_all_signals_are_published_and_earnings_use_current_market_date(self):
        universe = [stock(str(i), [100 + j for j in range(260)]) for i in range(100)]
        bench = [{'date':r['date'], 'close':r['close'], 'volume':100} for r in universe[0]['rows']]
        leaders = [{'leading':True} for _ in range(100)]
        warnings = [{'severity':'HIGH'} for _ in range(100)]
        cache = {'days': {'2026-10-19':[{'symbol':str(i),'time':'time-after-hours'} for i in range(100)]}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root/'us.html').write_text('fixture')
            with patch.object(trend, 'extract_old_payload', return_value={}), patch.object(trend, 'build_universe', return_value=('2026-10-16', universe)), patch.object(trend, 'load_fixture', return_value=bench), patch.object(trend, 'analyze_stocks', return_value=(leaders,warnings)), patch.object(trend, 'update_signal_log', return_value={}):
                result, _, _ = trend.build_market(root, 'US', {}, cache, fetch_network=False, now=datetime(2026,10,18,12,tzinfo=timezone.utc))
        self.assertEqual(len(result['rsLeaders']['items']), result['rsLeaders']['count'])
        self.assertEqual(len(result['sellWarnings']['items']), result['sellWarnings']['count'])
        self.assertEqual(len(result['earnings']['upcoming']), result['earnings']['total'])
        self.assertEqual(result['earnings']['upcoming'][0]['daysUntil'], 1)

    def test_breadth_labels_its_screened_population(self):
        br = {'status':'OK', 'above50Pct':25, 'denominator50':100, 'above50Count':25}
        w = trend.weather({}, {}, br, {}, False)
        f = next(f for f in w['factors'] if f['key'] == 'breadth')
        self.assertIn('스크리닝', f['text'])
        self.assertIn('100', f['text'])

if __name__ == '__main__': unittest.main()
