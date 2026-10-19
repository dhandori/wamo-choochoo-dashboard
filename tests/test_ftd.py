"""FTD must be a recovery signal, never an arbitrary high-volume up day."""
import copy
import unittest
from datetime import date, timedelta
from unittest.mock import patch
import importlib


def rows_for(prices):
    day = date(2026, 1, 2)
    rows = []
    for close in prices:
        while day.weekday() > 4:
            day += timedelta(days=1)
        rows.append(dict(date=day.isoformat(), close=close, high=close+0.3, low=close-0.3, volume=100))
        day += timedelta(days=1)
    return rows


def recovery():
    # A genuine correction, then four rally sessions. Declines within the
    # attempt still count; day 4 is exactly +1.7% on increased volume.
    prices = [100.0]*70 + [98, 96, 94, 92, 90, 91, 91.2, 91.1, 91.1*1.017]
    rows = rows_for(prices)
    rows[-1]['volume'] = 130
    return rows


class FTDTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('wamo_ftd'), 'FTD engine not implemented')
        self.ftd = importlib.import_module('wamo_ftd')

    def run_signal(self, rows, **kwargs):
        return self.ftd.analyze(rows, asof=rows[-1]['date'], **kwargs)

    def test_uptrend_big_gain_is_not_ftd(self):
        rows = rows_for([100+i*.1 for i in range(90)]+[112])
        rows[-1]['volume'] = 200
        result = self.run_signal(rows)
        self.assertEqual(result['phase'], 'OUTSIDE')
        self.assertFalse(any(e['type']=='FTD' for e in result['events']))

    def test_fourth_session_and_exact_threshold_confirm(self):
        result = self.run_signal(recovery())
        self.assertEqual(result['phase'], 'CONFIRMED')
        self.assertEqual(result['rally']['day'], 4)
        self.assertAlmostEqual(result['ftd']['changePct'], 1.7)
        self.assertEqual(result['ftd']['volumeRatio'], 1.3)
        self.assertEqual(result['ftd']['rallyDay'], 4)

    def test_third_session_big_gain_does_not_confirm(self):
        rows = recovery()[:-1]
        rows[-1].update(close=94, high=94.3, volume=200)
        result = self.run_signal(rows)
        self.assertEqual(result['phase'], 'RALLY')
        self.assertIsNone(result['ftd'])

    def test_day_seven_is_last_eligible_day(self):
        rows = recovery()
        prices = [r['close'] for r in rows[:-1]] + [91.2,91.3,91.2,93]
        extended = rows_for(prices); extended[-1]['volume']=130
        result=self.run_signal(extended)
        self.assertEqual(result['phase'],'CONFIRMED')
        self.assertEqual(result['ftd']['rallyDay'],7)

    def test_day_eight_gain_is_a_late_ftd(self):
        rows = recovery()
        prices = [r['close'] for r in rows[:-1]] + [91.2,91.3,91.2,91.3,93]
        extended = rows_for(prices); extended[-1]['volume']=130
        result=self.run_signal(extended)
        self.assertEqual(result['phase'],'CONFIRMED')
        self.assertEqual(result['ftd']['rallyDay'],8)
        self.assertTrue(result['ftd']['late'])
        self.assertFalse(result['ftd']['quality']['early'])

    def test_rally_past_day_seven_without_ftd_is_late_not_expired(self):
        rows = recovery()
        prices = [r['close'] for r in rows[:-1]] + [91.2,91.3,91.2,91.3,91.2]
        result=self.run_signal(rows_for(prices))
        self.assertEqual(result['phase'],'LATE')
        self.assertTrue(any(e['type']=='LATE_WINDOW' for e in result['events']))
        self.assertEqual(result['rally']['day'],8)
        self.assertIsNone(result['ftd'])

    def test_gain_below_one_point_seven_is_not_ftd(self):
        rows=recovery(); rows[-1]['close']=rows[-2]['close']*1.0169
        self.assertEqual(self.run_signal(rows)['phase'],'RALLY')

    def test_equal_volume_does_not_confirm(self):
        rows = recovery(); rows[-1]['volume'] = 100
        self.assertEqual(self.run_signal(rows)['phase'], 'RALLY')

    def test_intraday_bottom_break_resets_even_if_close_recovers(self):
        rows = recovery(); rows[-2].update(low=89.6, close=89.8)  # breaks the bottom, closes near the low
        result = self.run_signal(rows)
        self.assertEqual(result['phase'], 'RALLY')
        self.assertEqual(result['rally']['day'], 1)
        self.assertTrue(any(e['type']=='RALLY_FAILED' for e in result['events']))

    def test_dip_below_day_one_low_but_above_bottom_keeps_counting(self):
        # Day 1 low is 90.7, the correction bottom (prior session) is 89.7.
        rows = recovery(); rows[-2]['low'] = 90.6
        result = self.run_signal(rows)
        self.assertEqual(result['phase'], 'CONFIRMED')
        self.assertEqual(result['rally']['low'], 89.7)
        self.assertEqual(result['rally']['dayOneLow'], 90.7)

    def test_down_close_in_upper_half_after_new_low_is_day_one(self):
        rows = rows_for([100.0]*70 + [98, 96, 94, 92, 90])
        rows[-1].update(low=88.0, high=91.0, close=90.0)  # new low, closes 2/3 up the range
        result = self.run_signal(rows)
        self.assertEqual(result['phase'], 'RALLY')
        self.assertEqual(result['rally']['day'], 1)
        self.assertEqual(result['rally']['kind'], 'REVERSAL')
        rows[-1].update(close=89.4)  # lower half: not a Day 1
        self.assertEqual(self.run_signal(rows)['phase'], 'CORRECTION')

    def test_same_day_up_reversal_starts_new_day_one(self):
        rows = recovery(); rows[-2].update(low=89.6, close=91.3, high=91.6)
        result = self.run_signal(rows)
        self.assertEqual(result['phase'], 'RALLY')
        self.assertEqual(result['rally']['day'], 2)

    def test_failed_ftd_uses_intraday_low(self):
        rows = recovery(); day = rows_for([100]*len(rows)+[91])[-1]['date']
        rows.append(dict(date=day, close=90, high=92, low=89.6, volume=200))  # lower half: no new Day 1
        result = self.run_signal(rows)
        self.assertEqual(result['phase'], 'CORRECTION')
        self.assertIsNone(result['ftd'])
        self.assertTrue(any(e['type']=='FTD_FAILED' for e in result['events']))

    def test_ftd_low_close_is_warning_not_rally_invalidation(self):
        rows = recovery(); last=rows[-1]
        rows.append(dict(date=rows_for([100]*(len(rows)+1))[-1]['date'], close=last['low']-.1,
                         high=last['high'], low=last['low']-.2, volume=120))
        self.assertEqual(self.run_signal(rows)['phase'], 'PRESSURE')

    def test_three_distribution_days_after_ftd_warn(self):
        rows = recovery(); n=len(rows)
        prices=[r['close'] for r in rows]+[93.0,92.7,93.1,92.8,93.2,92.9]
        ext=rows_for(prices); ext[n-1]['volume']=130
        vol=[130,140,120,150,120,160]
        for k,v in enumerate(vol): ext[n+k]['volume']=v
        result=self.run_signal(ext)
        self.assertEqual(result['phase'],'PRESSURE')
        self.assertEqual(len(result['ftd']['postDistribution']),3)
        self.assertEqual(result['warnings'][-1]['code'],'DISTRIBUTION_CLUSTER')
        self.assertEqual(self.run_signal(ext[:-2])['phase'],'CONFIRMED')

    def test_ftd_quality_fields(self):
        result = self.run_signal(recovery())
        q=result['ftd']['quality']
        self.assertTrue(q['early']); self.assertFalse(q['strongGain']); self.assertTrue(q['aboveAvgVolume'])
        self.assertEqual(result['ftd']['qualityScore'],2)

    def test_missing_or_synthetic_data_cannot_confirm(self):
        rows = recovery()
        for field, value in [('low',None),('volume',0),('high',float('nan')),('close',True)]:
            bad=copy.deepcopy(rows); bad[-1][field]=value
            self.assertEqual(self.run_signal(bad)['status'], 'UNAVAILABLE')
        self.assertEqual(self.run_signal(rows,synthetic=True)['status'], 'UNAVAILABLE')
        self.assertEqual(self.run_signal(rows,expected='2026-12-01')['status'], 'STALE')

    def test_duplicate_or_missing_session_is_rejected(self):
        rows = recovery()
        self.assertEqual(self.run_signal(rows+[rows[-1]])['status'], 'UNAVAILABLE')
        dates=[r['date'] for r in rows]
        self.assertEqual(self.run_signal(rows[:-2]+rows[-1:],sessions=dates)['status'], 'UNAVAILABLE')

    def test_no_future_information_and_one_ftd_per_attempt(self):
        rows = recovery(); before=self.run_signal(rows[:-1]); result=self.run_signal(rows)
        self.assertEqual(before['phase'],'RALLY')
        nextdate=rows_for([100]*(len(rows)+1))[-1]['date']
        rows.append(dict(date=nextdate,close=95,high=95.2,low=94,volume=200))
        after=self.run_signal(rows)
        self.assertEqual(after['ftd'],result['ftd'])
        self.assertEqual(sum(e['type']=='FTD' for e in after['events']),1)

    def test_falling_ma50_and_five_percent_drawdown_qualifies(self):
        rows=rows_for([100]*70+[99]*10+[94])
        self.assertEqual(self.run_signal(rows)['phase'],'CORRECTION')

    def test_small_pullback_is_not_eligible(self):
        rows=rows_for([100]*70+[99,98,97,98,99,99,101])
        rows[-1]['volume']=200
        self.assertEqual(self.run_signal(rows)['phase'],'OUTSIDE')

    def test_recovered_high_without_ftd_exits_search(self):
        rows=recovery(); rows[-1].update(close=101,high=101.3,volume=100)
        self.assertEqual(self.run_signal(rows)['phase'],'OUTSIDE')

    def test_recovering_prior_peak_after_ftd_completes_the_turn(self):
        rows=recovery(); n=len(rows)
        prices=[r['close'] for r in rows]+[93,95,97,99,100.5,101,102]
        extended=rows_for(prices); extended[n-1]['volume']=130
        result=self.run_signal(extended)
        self.assertEqual(result['phase'],'OUTSIDE')
        self.assertIsNone(result['ftd'])
        self.assertIsNone(result['rally'])
        self.assertEqual(result['lastFtd']['date'],extended[n-1]['date'])
        self.assertEqual(result['lastFtd']['recoveredDate'],extended[n+4]['date'])
        self.assertEqual([e['type'] for e in result['events']][-2:],['FTD','UPTREND'])

    def test_ftd_day_itself_above_peak_stays_confirmed_that_day(self):
        rows=recovery(); rows[-1].update(close=101,high=101.3,volume=130)
        result=self.run_signal(rows)
        self.assertEqual(result['phase'],'CONFIRMED')

    def test_new_correction_replaces_old_confirmed_cycle(self):
        rows=recovery(); n=len(rows)
        # A new advance well above the old bottom, followed by a new decline.
        prices=[r['close'] for r in rows]+[100+i*.5 for i in range(70)]+[132,130,127,124,121]
        extended=rows_for(prices)
        extended[n-1]['volume']=130
        result=self.run_signal(extended)
        self.assertEqual(result['phase'],'CORRECTION')
        self.assertIsNone(result['ftd'])
        self.assertTrue(any(e['type']=='FTD' for e in result['events']))


if __name__=='__main__':unittest.main()

class IndexFTDInputTests(unittest.TestCase):
    def response(self, symbol='^GSPC'):
        from datetime import datetime, timezone
        quotes={k:[v]*120 for k,v in {'close':100,'high':102,'low':98,'volume':10000}.items()}
        return {'chart':{'result':[{'meta':{'symbol':symbol,'gmtoffset':0},
                'timestamp':[int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp())+86400*i for i in range(120)],
                'indicators':{'quote':[quotes]}}]}}

    def test_fetch_keeps_real_low_and_high(self):
        import io,json
        from wamo_trend import fetch_index
        data=self.response()
        rows=fetch_index('^GSPC',opener=lambda *a,**kw:io.BytesIO(json.dumps(data).encode()))
        self.assertEqual(rows[-1].get('low'),98)
        self.assertEqual(rows[-1].get('high'),102)

    def test_wrong_index_identity_rejected(self):
        import io,json
        from wamo_trend import fetch_index
        data=self.response('SPY')
        with self.assertRaises(RuntimeError):
            fetch_index('^GSPC',opener=lambda *a,**kw:io.BytesIO(json.dumps(data).encode()))

class DurableHistoryTests(unittest.TestCase):
    def test_ftd_does_not_disappear_when_provider_window_rolls(self):
        import wamo_ftd
        original=recovery()
        prices=[r['close'] for r in original]+[96+(i%20)*.05 for i in range(400)]
        full=rows_for(prices);full[len(original)-1]['volume']=130
        merge=getattr(wamo_ftd,'merge_history',None)
        self.assertIsNotNone(merge,'Durable FTD history missing')
        merged=merge(full[-290:],full[:-100])
        result=wamo_ftd.analyze(merged,asof=full[-1]['date'])
        self.assertEqual(result['phase'],'CONFIRMED')
        self.assertEqual(result['ftd']['date'],original[-1]['date'])

    def test_verified_old_observation_survives_new_provider_null(self):
        import wamo_ftd
        stored=recovery(); current=copy.deepcopy(stored)
        current[70]['volume']=0
        merged=wamo_ftd.merge_history(current,stored)
        self.assertEqual(merged[70]['volume'],stored[70]['volume'])
        current[-1]['volume']=0
        merged=wamo_ftd.merge_history(current,stored)
        self.assertEqual(merged[-1]['volume'],0)  # Never conceal a bad latest session.

    def test_duplicate_new_history_is_not_hidden_by_merge(self):
        import wamo_ftd
        rows=recovery(); merge=getattr(wamo_ftd,'merge_history',None)
        self.assertIsNotNone(merge,'Durable FTD history missing')
        self.assertEqual(wamo_ftd.analyze(merge(rows+[rows[-1]],rows),asof=rows[-1]['date'])['status'],'UNAVAILABLE')

class KoreanInputRepairTests(unittest.TestCase):
    def fixture(self):
        rows=recovery()
        payload={'code':'KOSPI','infoType':'index','stockExchangeType':'KRX','hasVolume':True,
                 'priceInfos':[{'localDate':r['date'].replace('-',''),'closePrice':r['close'],
                  'highPrice':r['high'],'lowPrice':r['low'],'accumulatedTradingVolume':r['volume']} for r in rows]}
        damaged=copy.deepcopy(rows);damaged[70]['volume']=0
        return rows,damaged,payload

    def test_zero_volume_is_repaired_from_matching_independent_source(self):
        import wamo_ftd
        repair=getattr(wamo_ftd,'repair_korean_history',None)
        self.assertIsNotNone(repair,'Verified Korean fallback missing')
        rows,damaged,payload=self.fixture()
        fixed,dates=repair(damaged,'^KS11',payload)
        self.assertEqual(fixed,rows)
        self.assertEqual(dates,[rows[70]['date']])

    def test_wrong_identity_or_volume_scale_is_never_spliced(self):
        import wamo_ftd
        repair=getattr(wamo_ftd,'repair_korean_history',None)
        self.assertIsNotNone(repair,'Verified Korean fallback missing')
        rows,damaged,payload=self.fixture();payload['code']='KOSDAQ'
        with self.assertRaises(ValueError):repair(damaged,'^KS11',payload)
        payload['code']='KOSPI';payload['priceInfos'][0]['accumulatedTradingVolume']*=1000
        with self.assertRaises(ValueError):repair(damaged,'^KS11',payload)

class FTDSessionTests(unittest.TestCase):
    def test_official_krx_closures_do_not_count_as_missing_prices(self):
        import wamo_ftd
        session_fn=getattr(wamo_ftd,'market_sessions',None)
        self.assertIsNotNone(session_fn,'Verified holiday overrides missing')
        dates=session_fn('KR','2026-06-01','2026-07-20')
        self.assertNotIn('2026-06-03',dates)
        self.assertNotIn('2026-07-17',dates)
        self.assertIn('2026-06-29',dates)
