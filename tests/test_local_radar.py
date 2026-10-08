import unittest
import copy
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
import wamo_radar as radar
from tests.test_price_sources import history


class LocalRadarTests(unittest.TestCase):
    def test_bad_security_is_excluded_without_blocking_verified_peers(self):
        data=self.payload()
        data['stocks']=[dict(copy.deepcopy(data['stocks'][0]),ticker=f'T{i}') for i in range(10)]
        data['stocks'][0]['history'][100]['high']=90.
        with patch.object(radar,'expected_session',return_value='2026-10-07'):
            out=radar.local_edition(data,'US',datetime(2026,10,8,tzinfo=timezone.utc))
        self.assertEqual(out['status'],'PASS')
        self.assertEqual(len(out['signals']),9)
        self.assertEqual(out['coverage']['validatedCount'],9)
        self.assertEqual(out['coverage']['excludedCount'],1)
        self.assertEqual(out['exclusions'][0]['symbol'],'T0')
        data['stocks'][1]['history'][100]['high']=90.
        with patch.object(radar,'expected_session',return_value='2026-10-07'):
            with self.assertRaisesRegex(ValueError,'verified coverage'):
                radar.local_edition(data,'US',datetime(2026,10,8,tzinfo=timezone.utc))

    def test_failed_legacy_edition_retains_only_52_week_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            prior={'editions':{'US':{'status':'PASS','signals':[
                dict(market='US-NASDAQ',symbol='A',industry_path=['Tech'],flags={'close_63':True}),
                dict(market='US-NASDAQ',symbol='B',industry_path=['Tech'],flags={'close_252':True})],
                'industries':[dict(market='US',industry_path=['Tech'],current_signal_issuers=2,eligible_classified_issuers=10)]}}}
            (root/'wamo_radar.json').write_text(json.dumps(prior))
            for name in ('index.html','us.html'):(root/name).write_text('<body></body>')
            with patch.object(radar,'__file__',str(root/'wamo_radar.py')),patch.dict(os.environ,{},clear=True):
                with self.assertRaises(SystemExit):radar.main()
            result=json.loads((root/'wamo_radar.json').read_text())['editions']['US']
            self.assertEqual(result['status'],'FAILED')
            self.assertEqual([s['symbol'] for s in result['signals']],['B'])
            self.assertEqual(result['industries'][0]['current_signal_issuers'],1)

    def test_upstream_ath_only_signals_are_removed_from_52_week_counts(self):
        self.assertTrue(hasattr(radar, 'only_52week'))
        edition={'signals':[
            dict(market='US-NASDAQ',symbol='A',industry_path=['Tech'],flags={'close_ath':True,'close_252':False}),
            dict(market='US-NYSE',symbol='B',industry_path=['Tech'],flags={'intraday_252':True})],
            'industries':[dict(market='US',industry_path=['Tech'],current_signal_issuers=2,eligible_classified_issuers=10)]}
        out=radar.only_52week(edition)
        self.assertEqual([s['symbol'] for s in out['signals']],['B'])
        self.assertEqual(out['industries'][0]['current_signal_issuers'],1)

    def payload(self):
        rows = history()
        rows[-1].update(close=102., high=103., low=100.)
        return {'meta':{'asOf':'2026-10-07'}, 'stocks':[
            dict(ticker='AAPL',displayTicker='AAPL',name='Apple',exchange='NASDAQ',sector='Technology',
                 date='2026-10-07',dataStatus='LIVE',history=rows,close=102.,high52WindowDays=252)]}

    def test_current_dashboard_produces_52_week_candidates_without_upstream(self):
        self.assertTrue(hasattr(radar, 'local_edition'))
        with patch.object(radar, 'expected_session', return_value='2026-10-07'):
            out=radar.local_edition(self.payload(), 'US', datetime(2026,10,8,tzinfo=timezone.utc))
        self.assertEqual(out['status'],'PASS')
        self.assertEqual(out['signals'][0]['flags'],{'intraday_252':True,'close_252':True})
        self.assertEqual(out['signals'][0]['symbol'],'AAPL')
        self.assertEqual(out['industries'][0]['eligible_classified_issuers'],1)

    def test_class_share_signal_uses_catalog_symbol_for_technical_join(self):
        data=self.payload()
        data['stocks'][0].update(ticker='BRK-B',displayTicker='BRK.B')
        with patch.object(radar, 'expected_session', return_value='2026-10-07'):
            out=radar.local_edition(data,'US',datetime(2026,10,8,tzinfo=timezone.utc))
        self.assertEqual(out['signals'][0]['symbol'],'BRK-B')

    def test_stale_snapshot_and_cached_prices_cannot_supply_current_candidates(self):
        self.assertTrue(hasattr(radar, 'local_edition'))
        for mode in ('date','cached'):
            data=self.payload()
            if mode=='date':data['meta']['asOf']='2026-10-06'
            else:data['stocks'][0]['dataStatus']='CACHED'
            with patch.object(radar, 'expected_session', return_value='2026-10-07'):
                with self.assertRaises(ValueError):
                    radar.local_edition(data,'US',datetime(2026,10,8,tzinfo=timezone.utc))

    def test_short_history_and_near_high_are_not_false_breakouts(self):
        self.assertTrue(hasattr(radar, 'local_edition'))
        for mode in ('short','near'):
            data=self.payload()
            if mode=='short':
                data['stocks'][0]['history']=data['stocks'][0]['history'][-60:]
            else:
                data['stocks'][0]['history'][-1].update(close=99.,high=100.,low=98.)
                data['stocks'][0]['close']=99.
            with patch.object(radar, 'expected_session', return_value='2026-10-07'):
                out=radar.local_edition(data,'US',datetime(2026,10,8,tzinfo=timezone.utc))
            self.assertEqual(out['signals'],[])
