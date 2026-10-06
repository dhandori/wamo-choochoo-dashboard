import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import wamo_update_business_dart as core


def chart(last_day):
    last = datetime.fromisoformat(last_day).replace(hour=13, tzinfo=timezone.utc)
    stamps = [int((last - timedelta(days=60-i)).timestamp()) for i in range(61)]
    return json.dumps({'chart': {'result': [{'timestamp': stamps, 'indicators': {
        'quote': [{'close': [100]*61, 'high': [101]*61, 'low': [99]*61,
                   'volume': [10000]*61}], 'adjclose': [{'adjclose': [100]*61}]}}]}}).encode()


class USHistoryTests(unittest.TestCase):
    def test_stale_success_response_uses_second_provider(self):
        urls = []
        def response(req, **kwargs):
            urls.append(req.full_url)
            return io.BytesIO(chart('2026-10-02' if 'query1.' in req.full_url else '2026-10-05'))
        with patch.object(core.urllib.request, 'urlopen', side_effect=response), patch.object(core.time, 'sleep'):
            rows, host = core.fetch_yahoo_history('AAPL', expected_date='2026-10-05')
        self.assertEqual(rows[-1]['date'], '2026-10-05')
        self.assertEqual(host, 'query2.finance.yahoo.com')
        self.assertEqual(len(urls), 2)

    def test_both_stale_providers_are_rejected_with_dates(self):
        with patch.object(core.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(chart('2026-10-02'))), patch.object(core.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '2026-10-05.*2026-10-02'):
                core.fetch_yahoo_history('AAPL', expected_date='2026-10-05')

    def test_new_intraday_bar_is_not_mislabeled_as_previous_close(self):
        with patch.object(core.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(chart('2026-10-06'))), patch.object(core.time, 'sleep'):
            rows, _ = core.fetch_yahoo_history('AAPL', expected_date='2026-10-05')
        self.assertEqual(rows[-1]['date'], '2026-10-05')

