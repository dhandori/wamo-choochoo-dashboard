import copy
import unittest
from datetime import datetime, timedelta, timezone
from wamo_insights import build_insights

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
DAY = '2026-10-13'
def stock(symbol='AAA', close=90, count=252, industry='Chips'):
    start = datetime.fromisoformat(DAY) - timedelta(days=count-1)
    rows = [{'date': (start+timedelta(days=i)).date().isoformat(), 'close': 80., 'high': 100., 'low': 70., 'volume': 100.} for i in range(count)]
    rows[-1].update(close=close, high=max(100., close), volume=200.)
    return {'ticker':symbol, 'stock_code':symbol,'name':symbol,'industry':industry,'date':DAY,'history':rows,'dataSource':'verified'}
def payload(*stocks):
    return {'meta':{'asOf':DAY,'source':'verified'},'stocks':list(stocks)}
class InsightsTests(unittest.TestCase):
    def build(self,*stocks,previous=None, now=NOW):
        return build_insights({'US':payload(*stocks)},now,previous)['markets']['US']
    def test_boundary_and_breakout_are_distinct(self):
        stocks=self.build(stock(),stock('NEW',101))['stocks']
        self.assertEqual(stocks[0]['highDistancePct'],10)
        self.assertFalse(stocks[0]['intradayHigh52'])
        self.assertTrue(stocks[1]['intradayHigh52'])
        self.assertTrue(stocks[1]['closeHigh52'])
        self.assertEqual(stocks[0]['volumeRatio'],2)
        self.assertEqual(stocks[0]['return5d'],12.5)
    def test_short_history_is_visible_but_ineligible(self):
        m=self.build(stock(count=40))
        self.assertEqual(m['coverage']['eligible52w'],0)
        self.assertIsNone(m['stocks'][0]['high52'])
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
        kr=stock('005930'); kr.pop('industry');kr['sector']='Electronics'
        m=build_insights({'KR':payload(kr),'US':{}},NOW)['markets']
        self.assertEqual(m['KR']['stocks'][0]['id'],'KR:005930')
        self.assertEqual(m['KR']['stocks'][0]['industrySource'],'sector (broad)')
        self.assertEqual(m['US']['status'],'UNAVAILABLE')
if __name__=='__main__': unittest.main()
