import unittest
from datetime import datetime, timezone
from wamo_universe_recovery import load_universe

NOW = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)

def payload():
    return {'meta': {'market':'US','asOf':'2026-10-13'}, 'stocks': [
        {'ticker':'MSFT','name':'Microsoft','exchange':'NASDAQ','market_cap_usd':1e12,
         'market_cap_krw':1e12,'industry':'Software','close':123,'date':'2026-10-13','history':[{'close':123}]}]}

class UniverseRecoveryTests(unittest.TestCase):
    def test_source_failure_reuses_identity_only_and_prices_must_be_fetched(self):
        def unavailable(): raise RuntimeError('HTTP 503')
        rows, excluded, report = load_universe(unavailable,payload(),'US',NOW)
        self.assertEqual(rows[0]['ticker'],'MSFT')
        self.assertNotIn('history',rows[0]); self.assertNotIn('close',rows[0]); self.assertNotIn('date',rows[0])
        self.assertEqual(report['status'],'CACHED_UNIVERSE')
        self.assertEqual(report['asOf'],'2026-10-13')
        self.assertEqual(excluded,0)
    def test_success_never_uses_previous_identities(self):
        rows,excluded,report=load_universe(lambda:([{'ticker':'AAPL'}],12),payload(),'US',NOW)
        self.assertEqual(rows,[{'ticker':'AAPL'}]); self.assertEqual(excluded,12)
        self.assertEqual(report['status'],'LIVE')
    def test_old_or_wrong_market_cache_is_rejected(self):
        for meta in ({'market':'US','asOf':'2026-08-01'},{'market':'KR','asOf':'2026-10-13'},
                     {'market':'US','asOf':'2026-10-15'}):
            p=payload();p['meta']=meta
            with self.assertRaises(RuntimeError): load_universe(lambda:1/0,p,'US',NOW)
    def test_cache_age_is_not_reset_by_new_price_date(self):
        p=payload();p['meta']['universeRecovery']={'status':'CACHED_UNIVERSE','asOf':'2026-08-01'}
        with self.assertRaises(RuntimeError):load_universe(lambda:1/0,p,'US',NOW)
    def test_duplicate_or_invalid_identity_is_rejected(self):
        p=payload();p['stocks']*=2
        with self.assertRaises(RuntimeError):load_universe(lambda:1/0,p,'US',NOW)
    def test_korea_preserves_exchange_suffix(self):
        p={'meta':{'market':'KR','asOf':'2026-10-13'},'stocks':[{'ticker':'005930.KS','stock_code':'005930','name':'삼성전자','krx_market':'KOSPI','market_cap_krw':1e12}]}
        rows,_,_=load_universe(lambda:1/0,p,'KR',NOW)
        self.assertEqual(rows[0]['krx_market'],'KOSPI')
        p['stocks'][0]['ticker']='005930.KQ'
        with self.assertRaises(RuntimeError):load_universe(lambda:1/0,p,'KR',NOW)
