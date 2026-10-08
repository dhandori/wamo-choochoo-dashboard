import copy
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import wamo_update_us_sec as us


def history(end='2026-10-07', count=260):
    day = date.fromisoformat(end)
    return [dict(date=(day-timedelta(days=count-i-1)).isoformat(),
                 close=100., high=101., low=99., volume=1000000.) for i in range(count)]


class PriceSourceTests(unittest.TestCase):
    def sources(self):
        # A missing independent source is the production failure being reproduced.
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('wamo_price_sources'),
                             'US daily histories must have independent fallback sources')
        import wamo_price_sources
        return wamo_price_sources

    def test_stale_yahoo_switches_to_independent_history(self):
        p = self.sources()
        router = p.PriceRouter({
            'Yahoo Finance': lambda m, d: p.PriceHistory(history('2026-10-06'), 'Yahoo Finance', 'yahoo', True),
            'Nasdaq': lambda m, d: p.PriceHistory(history(), 'Nasdaq', 'nasdaq', False),
        })
        result = router.fetch({'ticker': 'AAPL'}, '2026-10-07')
        self.assertEqual(result.source, 'Nasdaq')
        self.assertEqual(result.rows[-1]['date'], '2026-10-07')
        self.assertEqual(router.summary()['sources']['Yahoo Finance']['failures'], 1)

    def test_all_stale_sources_never_become_live(self):
        p = self.sources()
        router = p.PriceRouter({'Nasdaq': lambda m, d: p.PriceHistory(history('2026-10-06'), 'Nasdaq', 'nasdaq', False)})
        with self.assertRaisesRegex(RuntimeError, '2026-10-07'):
            router.fetch({'ticker': 'AAPL'}, '2026-10-07')

    def test_invalid_ohlcv_and_duplicate_dates_are_rejected(self):
        p = self.sources()
        for mutate in (lambda r: r[-1].update(close=float('nan')),
                       lambda r: r[-1].update(high=98),
                       lambda r: r[-1].update(volume=-1),
                       lambda r: r.append(dict(r[-1]))):
            rows = history()
            mutate(rows)
            with self.subTest(rows=rows[-1]), self.assertRaises(ValueError):
                p.validate_history(rows, '2026-10-07')

    def test_next_intraday_bar_is_removed(self):
        p = self.sources()
        rows = p.validate_history(history('2026-10-08'), '2026-10-07')
        self.assertEqual(rows[-1]['date'], '2026-10-07')

    def test_nasdaq_checks_identity_and_complete_response(self):
        p = self.sources()
        fixture = json.loads((Path(__file__).parent/'fixtures/nasdaq_history.json').read_text())
        parsed = p.parse_nasdaq(fixture, 'AAPL', '2026-10-07')
        self.assertEqual(parsed[-1]['close'], 336.67)
        for altered in ('identity', 'truncated'):
            bad = copy.deepcopy(fixture)
            if altered == 'identity': bad['data']['symbol'] = 'MSFT'
            else: bad['data']['totalRecords'] += 1
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                p.parse_nasdaq(bad, 'AAPL', '2026-10-07')

    def test_circuit_stops_outage_fanout_and_probes_after_cooldown(self):
        p = self.sources()
        clock = [0.]
        calls = []
        def primary(meta, expected):
            calls.append(meta['ticker'])
            if clock[0] < 61: raise RuntimeError('provider offline')
            return p.PriceHistory(history(), 'Yahoo', 'yahoo', True)
        router = p.PriceRouter({'Yahoo': primary, 'Nasdaq': lambda m, d: p.PriceHistory(history(), 'Nasdaq', 'nasdaq', False)}, failure_limit=2, cooldown=60, clock=lambda:clock[0])
        for i in range(10): self.assertEqual(router.fetch({'ticker':str(i)}, '2026-10-07').source, 'Nasdaq')
        self.assertEqual(len(calls), 2)
        clock[0] = 61
        self.assertEqual(router.fetch({'ticker':'RECOVER'}, '2026-10-07').source, 'Yahoo')

    def test_partial_history_does_not_claim_all_time_high(self):
        p = self.sources()
        meta = dict(ticker='AAPL', stock_code='AAPL', name='Apple', sector='Technology')
        stock = us.stock_from_history(meta, p.PriceHistory(history(), 'Nasdaq', 'api.nasdaq.com', False))
        self.assertEqual(stock['dataStatus'], 'LIVE')
        self.assertAlmostEqual(stock['high52Ratio'], 99.00990099)
        self.assertIsNone(stock['historicalHighRatio'])
        self.assertEqual(stock['historyScope'], 'AVAILABLE_WINDOW')

    def test_naver_identity_mismatch_cannot_supply_another_security(self):
        p = self.sources()
        with patch.object(p, 'get_json', return_value={'symbolCode':'MSFT', 'reutersCode':'AAPL.O', 'stockExchangeType':{'nationCode':'USA'}}):
            with self.assertRaises(RuntimeError):
                p.fetch_naver_us({'ticker':'AAPL', 'displayTicker':'AAPL', 'exchange':'NASDAQ'}, '2026-10-07')

    def test_google_html_reads_regular_close_not_after_hours(self):
        p = self.sources()
        html = '<h1>Apple Inc</h1><div class="N6SYTe"><span>$336.67</span></div><div>$337.99 After hours</div><div>Closed: Oct 7, 4:00:01 PM GMT-4 · USD</div>'
        value = p.parse_google_close(html, '2026-10-07')
        self.assertEqual(value['close'], 336.67)
        with self.assertRaises(ValueError): p.parse_google_close(html, '2026-10-06')

    def test_daily_collection_has_bounded_history_and_no_ath(self):
        p = self.sources()
        requests = []
        def yahoo(symbol, years=None, expected_date=None):
            requests.append(years)
            return history(), 'yahoo'
        result = p.us_router(yahoo).fetch({'ticker':'AAPL'}, '2026-10-07')
        self.assertEqual(requests, [3])
        self.assertFalse(result.full_history)

    def test_52_week_filter_does_not_admit_ath_only_or_short_listing(self):
        import wamo_update_business_dart as core
        self.assertTrue(hasattr(core, 'is_52week_high_zone'))
        self.assertFalse(core.is_52week_high_zone(dict(high52Ratio=80, historicalHighRatio=99, high52WindowDays=252)))
        self.assertFalse(core.is_52week_high_zone(dict(high52Ratio=99, high52WindowDays=60)))
        self.assertTrue(core.is_52week_high_zone(dict(high52Ratio=93, high52WindowDays=252)))

