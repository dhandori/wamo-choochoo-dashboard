import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import requests
import wamo_kis as kis


class KISTests(unittest.TestCase):
    def test_rate_limit_retries_and_succeeds(self):
        api = kis.KIS()
        api.token = 'memory-only'
        limited = Mock(status_code=500)
        limited.json.return_value = {'rt_cd': '1', 'msg_cd': 'EGW00201', 'msg1':'never publish response messages'}
        ok = Mock(status_code=200)
        ok.json.return_value = {'rt_cd':'0', 'output':{'stck_prpr':'100'}}
        api.session.get = Mock(side_effect=[limited, ok])
        with patch.object(kis.time, 'sleep'):
            self.assertEqual(api.quote('005930')['stck_prpr'], '100')
        self.assertEqual(api.session.get.call_count, 2)

    def test_non_transient_error_is_not_retried_or_leaked(self):
        api = kis.KIS()
        api.token = 'memory-only'
        response = Mock(status_code=403)
        response.json.return_value = {'rt_cd':'1', 'msg_cd':'DENIED', 'msg1':'sensitive text'}
        api.session.get = Mock(return_value=response)
        with patch.object(kis.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '^HTTP_403_DENIED$'):
            api.quote('005930')
        self.assertEqual(api.session.get.call_count, 1)

    def test_real_estimate_response_is_not_guessed_as_forward_eps(self):
        sample = json.loads((Path(__file__).parent / 'fixtures/kis_estimate_sample.json').read_text())
        out = kis.public_estimate(sample, '2026-09-07T22:38:00+09:00')
        self.assertEqual(out['status'], 'RESPONSE_AVAILABLE_UNMAPPED')
        self.assertEqual(out['periods'][-1], '2027.12E')
        # Real response carries scaled data fields: never silently interpret as won.
        self.assertEqual(out['output3'][1]['data1'], 21310)
        self.assertFalse(out['forwardMetricsConnected'])
        self.assertNotIn('eps', out)
        self.assertEqual(kis.public_estimate({'output1': {'sht_cd':'005930'}}, '')['status'], 'EMPTY')
        self.assertEqual(kis.public_estimate({'output2':[{'data1':'0'}], 'output4':[{'dt':'2026.12E'}]}, '')['status'], 'EMPTY')

    def test_cached_quote_retains_original_time_and_failure_count(self):
        stamp = kis.datetime.now(kis.KST).isoformat()
        old = {'005930': {'checkedAt':stamp, 'price':100, 'per':10}}
        stock = {'stock_code':'005930'}
        with patch.dict('os.environ',{'KIS_APP_KEY':'test','KIS_APP_SEC':'test'}), \
             patch.object(kis.KIS,'authenticate'), \
             patch.object(kis.KIS,'quote',side_effect=RuntimeError('NETWORK_TIMEOUT')), \
             patch.object(kis.KIS,'estimate',return_value={}), \
             patch.object(kis,'read_cache',side_effect=[old,{}]), patch.object(kis,'write_json'):
            meta=kis.enrich([stock])
        self.assertEqual(stock['kisQuote']['status'],'CACHED')
        self.assertEqual(stock['kisQuote']['checkedAt'],stamp)
        self.assertEqual(meta['quoteCount'],0)
        self.assertEqual(meta['errorCount'],1)
        self.assertEqual(meta['cachedCount'],1)

    def test_three_interspersed_errors_do_not_abort_other_candidates(self):
        stocks=[{'stock_code':f'{i:06d}'} for i in range(7)]
        responses=[RuntimeError('EMPTY_PRICE'), {'stck_prpr':'100','eps':'-2','per':'8'}, RuntimeError('EMPTY_PRICE'),
                   {'stck_prpr':'100'}, RuntimeError('EMPTY_PRICE'), {'stck_prpr':'100'}, {'stck_prpr':'100'}]
        with patch.dict('os.environ',{'KIS_APP_KEY':'test','KIS_APP_SEC':'test'}), \
             patch.object(kis.KIS,'authenticate'), patch.object(kis.KIS,'quote',side_effect=responses), \
             patch.object(kis.KIS,'estimate',return_value={}), patch.object(kis,'read_cache',return_value={}), \
             patch.object(kis,'write_json'):
            meta=kis.enrich(stocks)
        self.assertEqual(meta['quoteCount'],4)
        self.assertEqual(meta['skippedCount'],0)
        self.assertIsNone(stocks[1]['kisQuote']['per'])
