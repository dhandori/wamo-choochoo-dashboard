import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import wamo_update_business_dart as core

JST = 9 * 3600


def jp_chart(last_close, market_time_utc, regular_end_utc=None, price=3051.0):
    # 도쿄 일봉은 00:00 UTC(09:00 JST) 시각으로 온다. 마지막 줄이 2026-11-02.
    last = datetime(2026, 11, 2, tzinfo=timezone.utc)
    stamps = [int((last - timedelta(days=60 - i)).timestamp()) for i in range(61)]
    closes = [3000.0] * 60 + [last_close]
    adj = [3000.0] * 60 + [last_close]
    end = regular_end_utc or market_time_utc
    meta = {'regularMarketPrice': price, 'regularMarketTime': int(market_time_utc.timestamp()),
            'gmtoffset': JST,
            'currentTradingPeriod': {'regular': {'start': int(last.timestamp()), 'end': int(end.timestamp())}}}
    return json.dumps({'chart': {'result': [{'meta': meta, 'timestamp': stamps, 'indicators': {
        'quote': [{'open': [3000.0] * 61, 'close': closes, 'high': [3100.0] * 61, 'low': [2950.0] * 61,
                   'volume': [1000] * 61}], 'adjclose': [{'adjclose': adj}]}}]}}).encode()


CLOSE = datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc)  # 15:30 JST 마감


def fetch(body, expected='2026-11-02'):
    with patch.object(core.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(body)), \
            patch.object(core.time, 'sleep'):
        return core.fetch_yahoo_history('7203.T', expected_date=expected)


class YahooNullCloseTests(unittest.TestCase):
    def test_closed_session_null_close_uses_market_price(self):
        rows, _ = fetch(jp_chart(None, CLOSE))
        self.assertEqual(rows[-1]['date'], '2026-11-02')
        self.assertEqual(rows[-1]['close'], 3051.0)
        self.assertEqual(rows[-1]['high'], 3100.0)

    def test_intraday_price_is_not_used(self):
        with self.assertRaisesRegex(RuntimeError, '응답 가격일 2026-10-31|응답 가격일 2026-11-01'):
            fetch(jp_chart(None, CLOSE - timedelta(hours=2), regular_end_utc=CLOSE))

    def test_market_price_of_other_day_is_not_used(self):
        with self.assertRaises(RuntimeError):
            fetch(jp_chart(None, CLOSE - timedelta(days=1)))

    def test_real_close_is_kept(self):
        rows, _ = fetch(jp_chart(3070.0, CLOSE))
        self.assertEqual(rows[-1]['close'], 3070.0)

    def test_without_expected_date_nothing_is_filled(self):
        rows, _ = fetch(jp_chart(None, CLOSE), expected=None)
        self.assertNotEqual(rows[-1]['date'], '2026-11-02')


if __name__ == '__main__':
    unittest.main()
