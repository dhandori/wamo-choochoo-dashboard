import unittest
from unittest.mock import patch, Mock
import wamo_kis as kis


# Sanitized real response from the registered key, 2026-09-07 23:17 KST.
NVDA = {'last':'230.3600','perx':'29.12','epsx':'7.91','pbrx':'24.29','curr':'USD',
        'e_icod':'반도체 및 반도체장비','h52p':'236.2647','l52p':'163.8607'}


class KISUSTests(unittest.TestCase):
    def test_us_currency_sector_and_decimals_from_real_response(self):
        stock={'ticker':'NVDA','exchange':'NASDAQ','score':90,'sector':'반도체'}
        with patch.dict('os.environ',{'KIS_APP_KEY':'test','KIS_APP_SEC':'test'}), \
             patch.object(kis.KIS,'authenticate'), patch.object(kis.KIS,'overseas_quote',return_value=(NVDA,'NAS')), \
             patch.object(kis,'read_cache',return_value={}), patch.object(kis,'write_json'):
            meta=kis.enrich_us([stock])
        self.assertEqual(meta['quoteCount'],1)
        self.assertEqual(meta['industryCount'],1)
        self.assertEqual(stock['kisQuote']['currency'],'USD')
        self.assertEqual(stock['kisQuote']['price'],230.36)
        self.assertEqual(stock['kisQuote']['industry'],'반도체 및 반도체장비')
        self.assertEqual(stock['sector'],'반도체')
        self.assertFalse(meta['forwardMetricsConnected'])

    def test_wrong_exchange_empty_quote_is_resolved(self):
        api=kis.KIS()
        with patch.object(api,'get',side_effect=[{'output':{'last':'0'}},{'output':NVDA}]) as call:
            data,exchange=api.overseas_quote('NVDA','NYSE')
        self.assertEqual(exchange,'NAS')
        self.assertEqual(call.call_count,2)
        self.assertEqual(data['curr'],'USD')

    def test_us_auth_failure_does_not_try_other_exchanges(self):
        api=kis.KIS()
        with patch.object(api,'get',side_effect=RuntimeError('HTTP_403_DENIED')) as call:
            with self.assertRaises(RuntimeError): api.overseas_quote('NVDA','NASDAQ')
        self.assertEqual(call.call_count,1)

    def test_unknown_currency_does_not_become_dollars(self):
        stock={'ticker':'NVDA','exchange':'NASDAQ'}
        with patch.dict('os.environ',{'KIS_APP_KEY':'test','KIS_APP_SEC':'test'}), \
             patch.object(kis.KIS,'authenticate'), patch.object(kis.KIS,'overseas_quote',return_value=(dict(NVDA,curr=''),'NAS')), \
             patch.object(kis,'read_cache',return_value={}), patch.object(kis,'write_json'):
            meta=kis.enrich_us([stock])
        self.assertEqual(meta['errorCount'],1)
        self.assertNotIn('price',stock['kisQuote'])
