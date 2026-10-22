"""미국 지수 거래량: 저녁 재계산과 '언제 확정되나' 기록."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import wamo_run
from wamo_ftd import confirmed_latest
from wamo_trend import volume_schedule


class ConfirmedLatest(unittest.TestCase):
    def test_revised_latest_is_confirmed(self):
        rows = [{'date': '2026-10-21', 'volume': 5.3e9}]
        self.assertAlmostEqual(confirmed_latest(rows, {'2026-10-21': 2.89e9})['scale'], 1.8339, 3)

    def test_unrevised_or_adjusted_or_naver_is_not(self):
        self.assertIsNone(confirmed_latest([{'date': 'd', 'volume': 2.89e9}], {'d': 2.89e9}))
        self.assertIsNone(confirmed_latest([{'date': 'd', 'volume': 5.3e9, 'volumeRaw': 2.89e9}], {'d': 2.89e9}))
        self.assertIsNone(confirmed_latest([{'date': 'd', 'volume': 5.3e9, 'source': 'naver'}], {'d': 2.89e9}))


class Schedule(unittest.TestCase):
    def test_claim_needs_three_first_checks_mostly_confirmed(self):
        c = lambda ok: {'slot': 'first', 'confirmed': ok}
        self.assertFalse(volume_schedule([c(True), c(True)])['verified'])
        self.assertTrue(volume_schedule([c(True), c(True), c(True), c(True), c(False)])['verified'])
        self.assertFalse(volume_schedule([c(True), c(False), c(False)])['verified'])
        self.assertIsNone(volume_schedule([]))


class ConfirmRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(wamo_run, 'ROOT', self.root); self.patch.start()
        mock.patch.object(wamo_run, 'refresh_insights', lambda: []).start()
        (self.root / 'wamo_ftd_history.json').write_text('{}')
        self.write_block({'prelim': {'date': '2026-10-21', 'scale': 1.84}})

    def tearDown(self):
        mock.patch.stopall(); self.tmp.cleanup()

    def write_block(self, dist):
        (self.root / 'wamo_trend.json').write_text(json.dumps({'markets': {'US': {
            'asOf': '2026-10-21', 'weather': {'distribution': dist}, 'liquidity': {'groups': []}}}}))

    def test_records_confirmation_and_skips_once_final(self):
        now = datetime(2026, 10, 22, 9, 10, tzinfo=timezone.utc)
        builder = mock.Mock(side_effect=lambda m: self.write_block({'confirmed': {'date': '2026-10-21', 'scale': 1.84}}) or ['wamo_trend.json'])
        wamo_run.confirm_volume('first', builder=builder, now=now)
        hist = json.loads((self.root / 'wamo_ftd_history.json').read_text())
        self.assertEqual(hist['_confirmChecks']['US'][-1]['confirmed'], True)
        self.assertEqual(hist['_confirmChecks']['US'][-1]['at'], '2026-10-22T18:10+09:00')
        block = json.loads((self.root / 'wamo_trend.json').read_text())['markets']['US']
        self.assertEqual(block['volumeSchedule']['confirmedAtFirst'], 1)
        wamo_run.confirm_volume('second', builder=builder, now=now)      # 21:10 run: already final → no rebuild
        self.assertEqual(builder.call_count, 1)

    def test_still_preliminary_is_recorded_as_a_miss(self):
        builder = mock.Mock(return_value=['wamo_trend.json'])          # rebuild left the prelim flag in place
        wamo_run.confirm_volume('first', builder=builder)
        hist = json.loads((self.root / 'wamo_ftd_history.json').read_text())
        self.assertFalse(hist['_confirmChecks']['US'][-1]['confirmed'])


if __name__ == '__main__':
    unittest.main()
