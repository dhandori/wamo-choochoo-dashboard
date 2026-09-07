"""Read-only integration check using existing dashboard candidates."""
import json
from pathlib import Path
from wamo_update_business_dart import extract_old_payload
from wamo_kis import us_master_records, enrich_us

master = us_master_records()
print('MASTER',len(master),{s:master.get(s) for s in ('DELL','NVDA','JPM')},flush=True)
payload = extract_old_payload(Path('us.html').read_text())
result = enrich_us(payload['stocks'])
print('US_KIS_RESULT',json.dumps(result,ensure_ascii=False),flush=True)
assert result['quoteCount'] == result['targetCount'] == 20, 'US candidate quote check failed'
