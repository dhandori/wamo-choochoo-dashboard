import copy
from datetime import date, timedelta
import unittest
from unittest.mock import patch
import wamo_update_business_dart as kr
import wamo_update_us_sec as us


class PrimaryBusinessTests(unittest.TestCase):
    def test_cross_company_market_mentions_do_not_override_primary_industry(self):
        for name, sector in [('삼양식품','기타 식품 제조업'), ('삼아알미늄','1차 비철금속 제조업'),
                             ('카카오뱅크','은행 및 저축기관'), ('기아','자동차 제조업'),
                             ('하이브','오디오물 출판업'), ('삼천당제약','의약품 제조업')]:
            with self.subTest(name=name):
                text = f'당사는 {sector}을 영위합니다. 삼성전자의 DRAM NAND 반도체 업황은 시장에 영향을 미칩니다.'
                self.assertNotIn('반도체', kr._detail_sector_from_business(name, sector, text)[0])
                self.assertNotIn('메모리 반도체 생산', kr._profile_from_dart_report(name, sector, text)['summary'])

    def test_reviewed_supplier_profile_is_not_memory_manufacturer(self):
        profile = {'summary':'티씨케이는 반도체 공정용 SiC·흑연 소재·부품 업체입니다.',
                   'products':'SiC 링 부품', 'drivers':'DRAM NAND HBM 투자', 'customers':'메모리 반도체 제조사'}
        self.assertEqual(kr._fixed_profile_detail_sector('티씨케이','기타 비금속 제조업',profile)[0], '반도체 소재·부품')

    def test_genuine_memory_businesses(self):
        for name in ('삼성전자','SK하이닉스'):
            self.assertEqual(kr._detail_sector_from_business(name, '', '당사는 DRAM NAND 메모리 반도체를 생산합니다.')[0], '메모리 반도체')

    def test_legacy_template_migration_preserves_price_and_provenance(self):
        stock = {'name':'삼양식품','stock_code':'003230','krxSector':'기타 식품 제조업',
                 'sector':'메모리 반도체','detailSector':'메모리 반도체','sectorTags':['메모리 반도체'],
                 'businessProfile':{'summary':'삼양식품는 DRAM·NAND 등 메모리 반도체 생산·판매가 핵심입니다.',
                                    'products':'DRAM·NAND와 HBM 등 메모리 반도체'},
                 'businessModelUrl':'https://dart.fss.or.kr/original','businessModelReportDate':'20260312',
                 'close':12345,'history':[{'close':12000}],'dataSource':'KRX','ret7':3.5}
        old=copy.deepcopy(stock)
        self.assertTrue(kr.repair_business_classification(stock))
        self.assertEqual(stock['sector'],'식품')
        self.assertNotIn('DRAM',stock['businessProfile']['summary'])
        self.assertEqual(stock['businessModelDataStatus'],'DEGRADED')
        for key in ('close','history','dataSource','ret7','businessModelUrl','businessModelReportDate'):
            self.assertEqual(stock[key],old[key])
        again=copy.deepcopy(stock)
        kr.repair_business_classification(stock)
        self.assertEqual(stock,again)


class NasdaqCacheTests(unittest.TestCase):
    def run_enrich(self, prior, fetch, fast=False):
        rows=[{'ticker':'TEST','close':123,'ret7':4}]
        with patch.object(us,'_load_cache',return_value={'TEST':copy.deepcopy(prior)}), \
             patch.object(us,'_save_cache'), patch.object(us,'sec_company_map',return_value={}), \
             patch.object(us,'nasdaq_enrich_one',side_effect=fetch) as request, \
             patch.dict('os.environ',{'WAMO_PRICE_ONLY':'1' if fast else '0'}):
            us.enrich_sec(rows)
        return rows[0], request.call_count

    def test_expired_nasdaq_profile_refreshes(self):
        old={'secStatus':'NASDAQ_FALLBACK','profileFetchedAt':(date.today()-timedelta(days=45)).isoformat(), 'businessModelEasy':'old'}
        new={'secStatus':'NASDAQ_FALLBACK','profileFetchedAt':date.today().isoformat(),'businessModelEasy':'new'}
        row,calls=self.run_enrich(old,lambda stock:new)
        self.assertEqual(calls,1)
        self.assertEqual(row['businessModelEasy'],'new')
        self.assertEqual(row['close'],123)

    def test_failed_refresh_keeps_old_profile_date_and_marks_stale(self):
        old={'secStatus':'NASDAQ_FALLBACK','profileFetchedAt':(date.today()-timedelta(days=45)).isoformat(), 'businessModelEasy':'old', 'profileSource':'Nasdaq'}
        row,calls=self.run_enrich(old,RuntimeError('offline'))
        self.assertEqual(calls,1)
        self.assertEqual(row['profileFetchedAt'],old['profileFetchedAt'])
        self.assertEqual(row['businessModelEasy'],'old')
        self.assertTrue(row['profileIsStale'])
        self.assertEqual(row['profileAgeDays'],45)
        self.assertEqual(row['close'],123)

    def test_fresh_nasdaq_profile_uses_cache(self):
        old={'secStatus':'NASDAQ_FALLBACK','profileFetchedAt':date.today().isoformat(),'businessModelEasy':'old'}
        row,calls=self.run_enrich(old,RuntimeError('must not fetch'))
        self.assertEqual(calls,0)
        self.assertEqual(row['profileAgeDays'],0)
        self.assertFalse(row['profileIsStale'])
        self.assertEqual(row['profileStatus'],'CACHED')

    def test_price_only_does_not_fetch(self):
        old={'secStatus':'NASDAQ_FALLBACK','profileFetchedAt':'2020-01-01','businessModelEasy':'old'}
        row,calls=self.run_enrich(old,RuntimeError('must not fetch'),fast=True)
        self.assertEqual(calls,0)
        self.assertEqual(row['businessModelEasy'],'old')


class ProfileReuseTests(unittest.TestCase):
    def test_intraday_does_not_reanimate_bad_cached_template(self):
        old={'ticker':'003230.KS','name':'삼양식품','krxSector':'기타 식품 제조업',
             'sector':'메모리 반도체','detailSector':'메모리 반도체',
             'businessProfile':{'summary':'삼양식품는 DRAM·NAND 등 메모리 반도체 생산·판매가 핵심입니다.'}}
        rows=[{'ticker':'003230.KS','name':'삼양식품','close':123,'sector':'식품'}]
        with patch.dict('os.environ',{'WAMO_PRICE_ONLY':'1'}):
            kr.profile_enrich(rows,{'003230.KS':old})
        self.assertEqual(rows[0]['sector'],'식품')
        self.assertEqual(rows[0]['businessModelDataStatus'],'DEGRADED')
        self.assertEqual(rows[0]['close'],123)

    def test_migration_handles_unknown_companies_and_is_idempotent(self):
        import wamo_repair_profiles as repair
        stock={'name':'처음보는회사','stock_code':'999999','krxSector':'의약품 제조업',
               'sector':'메모리 반도체','businessProfile':{'summary':'처음보는회사는 DRAM·NAND 등 메모리 반도체 생산·판매가 핵심입니다.'},
               'close':321,'dataSource':'KRX'}
        payload={'stocks':[stock]}; cache={'999999':copy.deepcopy(stock)}
        result=repair.migrate(payload,cache,{})
        self.assertEqual(result['reviewRequired'],['처음보는회사'])
        self.assertEqual(stock['sector'],'제약·바이오')
        self.assertEqual(stock['close'],321)
        second=repair.migrate(payload,cache,{})
        self.assertEqual(second['changedStocks'],[])
        self.assertEqual(second['changedCacheEntries'],[])

    def test_sec_and_nasdaq_failure_preserve_previous_nasdaq_profile(self):
        old={'secStatus':'NASDAQ_FALLBACK','profileFetchedAt':'2020-01-01','businessModelEasy':'original'}
        rows=[{'ticker':'TEST','close':999}]
        with patch.object(us,'_load_cache',return_value={'TEST':old}), patch.object(us,'_save_cache'), \
             patch.object(us,'sec_company_map',return_value={'TEST':{'cik':'123'}}), \
             patch.object(us,'sec_enrich_one',side_effect=RuntimeError('SEC down')), \
             patch.object(us,'nasdaq_enrich_one',side_effect=RuntimeError('Nasdaq down')), \
             patch.dict('os.environ',{'WAMO_PRICE_ONLY':'0'}):
            us.enrich_sec(rows)
        self.assertEqual(rows[0]['businessModelEasy'],'original')
        self.assertEqual(rows[0]['profileFetchedAt'],'2020-01-01')
        self.assertEqual(rows[0]['profileStatus'],'STALE')
        self.assertEqual(rows[0]['close'],999)

    def test_offline_derived_rebuild_excludes_uncertain_businesses(self):
        import wamo_repair_profiles as repair
        rows=[]
        for i in range(3):
            rows.append({'name':f'장비{i}','ticker':str(i),'sector':'반도체 장비',
                         'sectorConfidence':'HIGH','close':110,'ma60':100,'ma20':100,
                         'high52Ratio':95,'rsPercentile':90,'volumeRatio':2,
                         'trendTemplate':True,'ret7':10,'ret30':20,'date':'2026-10-16',
                         'krx_market':'KOSPI','historyTradingDays':260})
        rows.append(dict(rows[0],ticker='unknown',name='검증중',sectorConfidence='LOW'))
        benchmarks={'KOSPI':{'ret7':1,'ret30':2,'source':'original snapshot'}}
        payload={'stocks':rows,'meta':{'sectorFlowMeta':{'benchmarks':copy.deepcopy(benchmarks)}}}
        with patch.object(kr,'http_bytes',side_effect=AssertionError('network forbidden')):
            repair.rebuild_derived(payload)
        self.assertEqual(payload['sectors'][0]['memberCount'],4)
        self.assertEqual(payload['sectors'][0]['flowEligibleCount'],3)
        self.assertEqual(rows[-1]['sectorAction']['status'],'NONE')
        self.assertEqual(payload['meta']['sectorFlowMeta']['benchmarks'],benchmarks)
        self.assertEqual(rows[0]['close'],110)


class ReviewRegressionTests(unittest.TestCase):
    def migrated(self):
        stock={'ticker':'003230.KS','stock_code':'003230','name':'삼양식품',
               'krxSector':'기타 식품 제조업','sector':'메모리 반도체',
               'businessModelSource':'OpenDART 정기보고서 직접추출',
               'businessProfile':{'summary':'삼양식품는 DRAM·NAND 등 메모리 반도체 생산·판매가 핵심입니다.'}}
        kr.repair_business_classification(stock)
        return stock

    def test_same_sentence_peer_clause_is_not_primary_business(self):
        for text in ('당사는 식품을 제조·판매하며 삼성전자의 DRAM NAND 반도체 장비 투자 동향을 참고합니다.',
                     '당사는 식품을 제조·판매하고 고객사의 반도체 장비 수요 동향을 참고합니다.',
                     '당사는 삼성전자의 DRAM NAND 반도체 장비 투자 동향을 참고하며 식품을 판매합니다.'):
            with self.subTest(text=text):
                detail,tags,confidence=kr._detail_sector_from_business('삼양식품','기타 식품 제조업',text)
                self.assertNotIn('반도체',detail)
                profile=kr._profile_from_dart_report('삼양식품','기타 식품 제조업',text)
                self.assertNotIn('DRAM',profile['summary'])

    def test_already_migrated_intraday_retains_quarantine_provenance(self):
        old=self.migrated(); rows=[{'ticker':old['ticker'],'name':old['name'],'close':123,'sector':'식품'}]
        with patch.dict('os.environ',{'WAMO_PRICE_ONLY':'1'}):
            kr.profile_enrich(rows,{old['ticker']:old})
        for key in ('classificationReviewRequired','businessModelPreviousSource','businessModelDataStatus'):
            self.assertEqual(rows[0].get(key),old[key])

    def test_already_migrated_dart_outage_retains_quarantine_provenance(self):
        old=self.migrated(); old['updatedAt']='2020-01-01'
        rows=[{'ticker':old['ticker'],'stock_code':'003230','name':old['name'],'close':123,'sector':'기타 식품 제조업'}]
        with patch.dict('os.environ',{'WAMO_PRICE_ONLY':'0'}),patch.object(kr,'DART_KEY','test'), \
             patch.object(kr,'_load_profile_cache',return_value={'003230':copy.deepcopy(old)}), \
             patch.object(kr,'_load_fixed_business_db_from_index',return_value={}), \
             patch.object(kr,'dart_corp_map',side_effect=RuntimeError('offline')),patch.object(kr,'_save_profile_cache'):
            kr.profile_enrich(rows,{old['ticker']:old})
        for key in ('classificationReviewRequired','businessModelPreviousSource','businessModelDataStatus'):
            self.assertEqual(rows[0].get(key),old[key])

    def test_fixed_replacement_distinguishes_description_source_from_historical_filing(self):
        stock={'name':'검수회사','stock_code':'999999','krxSector':'식품 제조업',
               'businessProfile':{'summary':'기존 설명'},'businessModelSource':'OpenDART old',
               'businessModelUrl':'https://dart.fss.or.kr/old','businessModelReportDate':'20200101'}
        kr.repair_business_classification(stock,{'999999':{'summary':'검수회사는 식품을 제조합니다.'}})
        self.assertEqual(stock['businessModelSource'],'WAMO 검수 고정 DB')
        self.assertEqual(stock['businessModelPreviousSource'],'OpenDART old')
        self.assertEqual(stock['businessModelPreviousUrl'],'https://dart.fss.or.kr/old')
        self.assertEqual(stock['businessModelPreviousReportDate'],'20200101')
        self.assertNotIn('businessModelUrl',stock)
        self.assertNotIn('businessModelReportDate',stock)

    def test_failed_qa_refuses_migration_write(self):
        import sys
        import tempfile
        from pathlib import Path
        import wamo_repair_profiles as repair
        with tempfile.TemporaryDirectory() as folder:
            index=Path(folder)/'index.html'; cache=Path(folder)/'cache.json'
            original='window.WAMO_DATA = {"meta":{},"sectors":[],"stocks":[{"name":"test"}]};'
            index.write_text(original);cache.write_text('{}')
            with patch.object(sys,'argv',['repair','--index',str(index),'--cache',str(cache),'--write']), \
                 patch.object(repair,'migrate',return_value={}),patch.object(repair,'rebuild_derived'), \
                 patch.object(kr,'validate_payload_integrity',return_value={'status':'FAIL'}):
                with self.assertRaises(RuntimeError):
                    repair.main()
            self.assertEqual(index.read_text(),original)
            self.assertEqual(cache.read_text(),'{}')
