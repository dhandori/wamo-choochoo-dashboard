"""Read-only Korean industry and US sector verification; public fields only."""
import json
from wamo_kis import KIS

api = KIS()
api.authenticate()
print('KIS authentication OK', flush=True)
for code in ['005930', '003490']:
    try:
        data = api.quote(code)
        print(json.dumps({'market':'KR','symbol':code,'industry':data.get('bstp_kor_isnm')},ensure_ascii=False),flush=True)
    except RuntimeError as exc:
        print(json.dumps({'market':'KR','symbol':code,'error':str(exc)}),flush=True)
for symbol, exchange in [('NVDA','NAS'),('AAPL','NAS'),('JPM','NYS')]:
    try:
        data = api.get('/uapi/overseas-price/v1/quotations/price-detail','HHDFS76200200',
                       {'AUTH':'','EXCD':exchange,'SYMB':symbol}).get('output') or {}
        allowed = ('e_icod','last','perx','epsx','pbrx','curr','h52p','l52p','etyp_nm')
        print(json.dumps({'market':'US','symbol':symbol,'exchange':exchange,
                          'data':{key:data.get(key) for key in allowed}},ensure_ascii=False),flush=True)
    except RuntimeError as exc:
        print(json.dumps({'market':'US','symbol':symbol,'error':str(exc)}),flush=True)
