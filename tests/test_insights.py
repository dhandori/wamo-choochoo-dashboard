import copy
import unittest
from datetime import datetime, timedelta, timezone
from wamo_insights import build_insights, naver_url

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
DAY = '2026-10-13'
def stock(symbol='AAA', close=90, count=252, industry='Chips'):
    start = datetime.fromisoformat(DAY) - timedelta(days=count-1)
    rows = [{'date': (start+timedelta(days=i)).date().isoformat(), 'close': 80., 'high': 100., 'low': 70., 'volume': 100.} for i in range(count)]
    rows[-1].update(close=close, high=max(100., close), volume=200.)
    return {'ticker':symbol, 'stock_code':symbol,'name':symbol,'industry':industry,'date':DAY,'history':rows,'dataSource':'verified'}
def payload(*stocks):
    return {'meta':{'asOf':DAY,'source':'verified'},'stocks':list(stocks)}
FUNNEL_KR = {'listed': 4148, 'companyUniverse': 2601, 'marketCapPass': 298, 'liquidityPass': 213,
             'marketCapThresholdKRW': 1_000_000_000_000, 'liquidityThresholdKRW': 10_000_000_000}


class UniverseRuleTests(unittest.TestCase):
    """스크리닝 대상 기준을 화면이 읽을 수 있게 스냅샷에 싣는다.

    임계값을 화면 코드에 적으면 수집 기준이 바뀔 때 숫자가 어긋난다.
    """

    def build(self, funnel, market='KR'):
        data = payload(stock())
        if funnel is not None:
            data['meta']['universeFunnel'] = funnel
        return build_insights({market: data}, NOW)['markets'][market]['coverage']['universe']

    def test_thresholds_and_steps_are_carried(self):
        universe = self.build(FUNNEL_KR)
        self.assertEqual(universe['currency'], 'KRW')
        self.assertEqual(universe['marketCap'], 1_000_000_000_000)
        self.assertEqual(universe['liquidity'], 10_000_000_000)
        self.assertEqual(universe['liquidityDays'], 50)
        self.assertEqual([x['label'] for x in universe['steps']],
                         ['상장', '기업·리츠', '시총 통과', '거래대금 통과'])
        self.assertEqual([x['count'] for x in universe['steps']], [4148, 2601, 298, 213])

    def test_dollar_thresholds_keep_their_currency(self):
        universe = self.build({'listed': 2560, 'liquidityPass': 881,
                               'marketCapThresholdUSD': 1_000_000_000,
                               'liquidityThresholdUSD': 10_000_000}, market='US')
        self.assertEqual(universe['currency'], 'USD')
        self.assertEqual(universe['marketCap'], 1_000_000_000)

    def test_missing_funnel_gives_nothing(self):
        self.assertIsNone(self.build(None))
        for bad in ({}, 'funnel', [1, 2], {'marketCapEnforced': True}):
            self.assertIsNone(self.build(bad))

    def test_bad_numbers_are_dropped_not_rendered(self):
        universe = self.build({'listed': 'many', 'companyUniverse': 0, 'liquidityPass': 213,
                               'marketCapThresholdKRW': float('nan'),
                               'liquidityThresholdKRW': -5})
        self.assertIsNone(universe['marketCap'])
        self.assertIsNone(universe['liquidity'])
        self.assertEqual([x['count'] for x in universe['steps']], [213])

    def test_snapshot_stays_json_safe(self):
        import json
        snapshot = build_insights({'KR': {'meta': {'asOf': DAY, 'source': 'verified',
                                                   'universeFunnel': FUNNEL_KR},
                                          'stocks': [stock()]}}, NOW)
        json.dumps(snapshot, ensure_ascii=False, allow_nan=False)


class InsightsTests(unittest.TestCase):
    def build(self,*stocks,previous=None, now=NOW):
        return build_insights({'US':payload(*stocks)},now,previous)['markets']['US']
    def test_market_cap_uses_market_currency_without_conversion(self):
        s=stock('005930');s.update(market_cap_krw=400_000_000_000_000,market_cap_usd=12_000_000_000)
        result=build_insights({'KR':payload(s),'US':payload(s)},NOW)
        self.assertEqual(result['markets']['KR']['stocks'][0]['marketCap'],400_000_000_000_000)
        self.assertEqual(result['markets']['US']['stocks'][0]['marketCap'],12_000_000_000)
        s['market_cap_usd']='12000000000'
        self.assertEqual(self.build(s)['stocks'][0]['marketCap'],12_000_000_000)

    def test_missing_or_invalid_market_cap_does_not_drop_prices(self):
        for value in (None,0,-1,True,float('nan'),float('inf'),'not available'):
            s=stock();s.update(market_cap_usd=value,market_cap_krw=999_000_000)
            result=self.build(s)
            self.assertEqual(result['coverage']['valid'],1)
            self.assertIsNone(result['stocks'][0]['marketCap'])
    def test_naver_projection_verified_exchange_mapping(self):
        cases = [('KR','005930',None,'domestic/stock/005930'), ('KR','0126Z0',None,'domestic/stock/0126Z0'), ('US','MSFT','NASDAQ','worldstock/stock/MSFT.O'), ('US','PSX','NYSE','worldstock/stock/PSX'), ('US','BRK-B','NYSE','worldstock/stock/BRKb'), ('US','BRK.A','NYSE','worldstock/stock/BRKa'), ('US','IMO','NYSE AMERICAN','worldstock/stock/IMO'), ('US','EQX','AMEX','worldstock/stock/EQX'), ('US','CBOE','NYSE AMERICAN','worldstock/stock/CBOE.K')]
        for market, symbol, exchange, path in cases:
            self.assertEqual(naver_url(market,symbol,exchange),f'https://m.stock.naver.com/{path}/total')
        for symbol, exchange in [('MSFT',None),('ABC','UNKNOWN'),('FOO-B','NYSE'),('BRK-B','NASDAQ'),('TEST','AMEX')]:
            self.assertIsNone(naver_url('US',symbol,exchange))
        s=stock('MSFT'); s['exchange']='NASDAQ'
        projected=self.build(s)['stocks'][0]
        self.assertEqual(projected['naverUrl'],'https://m.stock.naver.com/worldstock/stock/MSFT.O/total')
        self.assertEqual(projected['exchange'],'NASDAQ')
        self.assertIn('legacy=1',projected['detailUrl'])

    def test_boundary_and_breakout_are_distinct(self):
        stocks=self.build(stock(),stock('NEW',101))['stocks']
        self.assertEqual(stocks[0]['highDistancePct'],10)
        self.assertFalse(stocks[0]['intradayHigh52'])
        self.assertTrue(stocks[1]['intradayHigh52'])
        self.assertTrue(stocks[1]['closeHigh52'])
        self.assertEqual(stocks[0]['volumeRatio'],2)
        self.assertEqual(stocks[0]['return5d'],12.5)
    def test_60_day_high_uses_the_last_60_sessions_only(self):
        # 252일 전체 고가는 100이지만 최근 60거래일 고가는 85 → 종가 90은 60일 신고가, 52주 신고가는 아님.
        s=stock(close=90)
        for row in s['history'][-60:-1]:row.update(high=85.,close=80.)
        s['history'][-1].update(high=90.,close=90.)
        out=self.build(s)['stocks'][0]
        self.assertFalse(out['intradayHigh52'])
        self.assertTrue(out['intradayHigh60'])
        self.assertTrue(out['closeHigh60'])
        self.assertEqual(out['high60'],90)
        self.assertEqual(out['high60DistancePct'],0)
        short=self.build(stock(count=40))['stocks'][0]
        self.assertIsNone(short['intradayHigh60'])
        self.assertIsNone(short['high60'])
    def test_short_history_is_visible_but_ineligible(self):
        m=self.build(stock(count=40))
        self.assertEqual(m['coverage']['eligible52w'],0)
        self.assertIsNone(m['stocks'][0]['high52'])
    def test_60_day_return_requires_61_closes_and_uses_trading_observations(self):
        s=stock(close=90,count=61)
        start=datetime(2026,7,21)
        sessions=[start+timedelta(days=i) for i in range(85) if (start+timedelta(days=i)).weekday()<5]
        for row,session in zip(s['history'],sessions):row['date']=session.date().isoformat()
        s['history'][0]['close']=75
        self.assertEqual(self.build(s)['stocks'][0].get('return60d'),20)
        short=self.build(stock(count=60))['stocks'][0]
        self.assertIn('return60d',short)
        self.assertIsNone(short['return60d'])
    def test_long_returns_use_exact_120_and_200_session_boundaries(self):
        for days in (120,200):
            with self.subTest(days=days):
                s=stock(close=90,count=days+1)
                s['history'][0]['close']=75
                self.assertEqual(self.build(s)['stocks'][0].get(f'return{days}d'),20)
                short=self.build(stock(count=days))['stocks'][0]
                self.assertIn(f'return{days}d',short)
                self.assertIsNone(short[f'return{days}d'])
    def test_stale_row_and_corrupt_histories_excluded(self):
        stale=stock('OLD'); stale['date']='2026-10-12'
        broken=stock('BAD'); broken['history'][-2]['date']='garbage'
        dup=stock('DUP'); dup['history'][-2]['date']=DAY
        invalid=stock('NEG'); invalid['history'][0]['volume']=-1
        m=self.build(stock(), stale,broken,dup,invalid)
        self.assertEqual(m['status'],'PARTIAL')
        self.assertEqual(m['coverage']['excluded'],4)
        self.assertEqual([s['id'] for s in m['stocks']],['US:AAA'])
    def test_industry_requires_three_members_and_two_advancing(self):
        m=self.build(stock('A'),stock('B'),stock('C'),stock('LONE',industry='Other'))
        groups={g['name']:g for g in m['industries']}
        self.assertTrue(groups['Chips']['confirmed'])
        self.assertFalse(groups['Other']['confirmed'])
        self.assertEqual(groups['Chips']['members'],['US:A','US:B','US:C'])
    def test_translated_labels_do_not_split_the_same_us_industry(self):
        a=stock('A',industry='Oil & Gas Production');a['industryEn']='Oil & Gas Production'
        b=stock('B',industry='석유·가스 (Oil & Gas Production)');b['industryEn']='Oil & Gas Production'
        result=self.build(a,b)
        self.assertEqual(len(result['industries']),1)
        self.assertEqual(result['industries'][0]['memberCount'],2)
        self.assertEqual(result['stocks'][1]['industrySource'],'industryEn')
    def test_downside_mover_away_from_highs(self):
        m=self.build(stock(close=72))
        self.assertIn('US:AAA',[s['id'] for s in m['movers']])
        self.assertIn('DOWN',m['stocks'][0]['moverReasons'])
    def test_prior_market_survives_invalid_replacement(self):
        previous=build_insights({'US':payload(stock())},NOW)
        m=build_insights({'US':{}},NOW+timedelta(days=2),previous)['markets']['US']
        self.assertEqual(m['asOf'],DAY)
        self.assertEqual(m['status'],'STALE')
        self.assertEqual(len(m['stocks']),1)
    def test_holiday_weekend_does_not_expire_prices(self):
        friday=stock(); friday['date']='2026-10-09'
        for row in friday['history']: row['date']=(datetime.fromisoformat(row['date'])-timedelta(days=4)).date().isoformat()
        p=payload(friday); p['meta']['asOf']='2026-10-09'
        m=build_insights({'US':p},datetime(2026,10,11,12,tzinfo=timezone.utc))['markets']['US']
        self.assertEqual(m['status'],'CURRENT')
    def test_official_catalysts_only_with_authentic_date(self):
        s=stock(); s['catalysts']=[{'date':'20261012','report':'Filing','url':'https://www.sec.gov/Archives/a'},{'date':DAY,'title':'Fake','url':'https://sec.gov.evil.test/a'},{'title':'No date','url':'https://dart.fss.or.kr/a'}]
        self.assertEqual(self.build(s)['stocks'][0]['catalysts'],[{'date':'2026-10-12','title':'Filing','url':'https://www.sec.gov/Archives/a'}])
    def test_truncated_replacement_retains_prior_and_deduplicates_warnings(self):
        previous=build_insights({'US':payload(stock('A'),stock('B'),stock('C'))},NOW)
        first=build_insights({'US':payload(stock('A'))},NOW,previous)
        second=build_insights({'US':payload(stock('A'))},NOW,first)
        market=second['markets']['US']
        self.assertEqual(len(market['stocks']),3)
        self.assertEqual(market['status'],'PARTIAL')
        self.assertEqual(len(market['warnings']),len(set(market['warnings'])))
    def test_normal_universe_change_accepted(self):
        previous=build_insights({'US':payload(*(stock('S'+str(i)) for i in range(10)))},NOW)
        result=build_insights({'US':payload(*(stock('S'+str(i)) for i in range(9)))},NOW,previous)
        self.assertEqual(result['markets']['US']['coverage']['valid'],9)
        self.assertEqual(result['markets']['US']['status'],'CURRENT')
    def test_malformed_optional_catalyst_does_not_abort_prices(self):
        s=stock();s['catalysts']=[{'date':DAY,'title':'Broken','url':'https://[broken'}]
        result=self.build(s)
        self.assertEqual(result['coverage']['valid'],1)
        self.assertEqual(result['stocks'][0]['catalysts'],[])
    def test_sparse_replacement_retains_prior(self):
        previous=build_insights({'US':payload(stock('A'),stock('B'),stock('C'))},NOW)
        bad=stock('BAD');bad['history']=[]
        m=build_insights({'US':payload(stock('A'),bad,bad)},NOW,previous)['markets']['US']
        self.assertEqual(len(m['stocks']),3)
        self.assertEqual(m['status'],'PARTIAL')
    def test_kr_alphanumeric_identity_and_stage2(self):
        s=stock('0126Z0');s['stage2Core']=True;s['trendTemplate']=False
        m=build_insights({'KR':payload(s)},NOW)['markets']['KR']
        self.assertEqual(m['stocks'][0]['id'],'KR:0126Z0')
        self.assertTrue(m['stocks'][0]['stage2'])
    def test_weak_industry_and_unknown_industry_never_confirm(self):
        m=self.build(stock('A',72),stock('B',72),stock('C',72))
        self.assertFalse(m['industries'][0]['confirmed'])
        m=self.build(stock('A',industry='산업 미분류'),stock('B',industry='산업 미분류'),stock('C',industry='산업 미분류'))
        self.assertFalse(m['industries'][0]['confirmed'])
    def test_malformed_market_does_not_block_other_market(self):
        result=build_insights({'US':['bad'], 'KR':payload(stock('005930'))},NOW)
        self.assertEqual(result['markets']['US']['status'],'UNAVAILABLE')
        self.assertEqual(result['markets']['KR']['coverage']['valid'],1)
    def test_independent_markets_and_sector_fallback(self):
        # 999990 is not in the WAMO classification table, so the broad sector is kept.
        kr=stock('999990'); kr.pop('industry');kr['sector']='Electronics'
        m=build_insights({'KR':payload(kr),'US':{}},NOW)['markets']
        self.assertEqual(m['KR']['stocks'][0]['id'],'KR:999990')
        self.assertEqual(m['KR']['stocks'][0]['industrySource'],'sector (broad)')
        self.assertEqual(m['US']['status'],'UNAVAILABLE')
    def test_wamo_classification_table_overrides_broad_sector(self):
        # 에코프로(086520) used to land in '메모리 반도체'; the product-level table puts it with cathode makers.
        kr=stock('086520',industry='메모리 반도체')
        m=build_insights({'KR':payload(kr)},NOW)['markets']['KR']
        self.assertEqual(m['stocks'][0]['industry'],'양극재·전구체')
        self.assertEqual(m['stocks'][0]['industrySource'],'WAMO 분류표')
        self.assertEqual(m['stocks'][0]['industryGroup'],'2차전지')
        self.assertEqual(m['industries'][0]['name'],'양극재·전구체')
        self.assertIn('medianReturn20d',m['industries'][0])
        us=build_insights({'US':payload(stock('AAPL',industry='Chips'))},NOW)['markets']['US']
        self.assertEqual(us['stocks'][0]['industrySource'],'industry')
if __name__=='__main__': unittest.main()
