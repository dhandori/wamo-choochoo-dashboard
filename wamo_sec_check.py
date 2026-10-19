"""SEC EDGAR 직접연결 점검. 연락처(SEC_USER_AGENT) 값 자체는 출력하지 않는다."""
import os
import re
import sys

import wamo_update_us_sec as us

ua = (os.getenv('SEC_USER_AGENT') or '').strip()
print('SEC_USER_AGENT 설정됨:', bool(ua), flush=True)
print('이름+이메일 형식:', bool(re.search(r'\S+\s+\S+@\S+\.\S+', ua)), flush=True)
ok = True
for label, url in [('티커 목록', 'https://www.sec.gov/files/company_tickers.json'),
                   ('애플 재무자료', 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json')]:
    try:
        data = us._sec_get(url)
        size = len(data) if isinstance(data, dict) else 0
        print(f'{label}: 성공 · 항목 {size}개', flush=True)
    except Exception as exc:
        ok = False
        print(f'{label}: 실패 · {str(exc)[-160:]}', flush=True)
sys.exit(0 if ok else 1)
