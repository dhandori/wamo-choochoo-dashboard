"""Bounded read-only schema check; only public market fields are logged."""
import json
import re
import time
from wamo_kis import KIS, BASE

api = KIS()
api.authenticate()
print('KIS authentication OK', flush=True)
for code in ['003490', '078930', '005930']:
    for name, path, tr, params in [
        ('quote', '/uapi/domestic-stock/v1/quotations/inquire-price', 'FHKST01010100', {'FID_COND_MRKT_DIV_CODE':'J', 'FID_INPUT_ISCD':code}),
        ('estimate', '/uapi/domestic-stock/v1/quotations/estimate-perform', 'HHKST668300C0', {'SHT_CD':code})]:
        for attempt in range(3):
            time.sleep(2)
            r = api.session.get(BASE + path, params=params, headers={'authorization':'Bearer '+api.token, 'appkey':api.key, 'appsecret':api.secret, 'tr_id':tr, 'custtype':'P'}, timeout=20)
            try:
                d = r.json()
            except ValueError:
                d = {}
            msg = str(d.get('msg_cd', ''))
            print(json.dumps({'code':code, 'endpoint':name, 'http':r.status_code, 'rt_cd':str(d.get('rt_cd')), 'msg_cd':msg if re.fullmatch('[A-Za-z0-9_-]{0,30}',msg) else 'REDACTED'}, ensure_ascii=False), flush=True)
            if r.status_code == 200 and str(d.get('rt_cd')) == '0':
                allowed = {'stck_prpr','per','eps','pbr','data1','data2','data3','data4','data5','dt'}
                clean = {}
                for key in ['output','output2','output3','output4']:
                    rows = d.get(key) or []
                    if isinstance(rows,dict): rows=[rows]
                    clean[key] = [{k:v for k,v in row.items() if k in allowed} for row in rows if isinstance(row,dict)]
                print(json.dumps(clean,ensure_ascii=False),flush=True)
                break
