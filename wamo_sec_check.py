"""SEC EDGAR 직접연결 점검. 연락처(SEC_USER_AGENT) 값 자체는 출력하지 않는다."""
import gzip
import os
import re
import sys
import urllib.error
import urllib.request


def note(level, text):
    # GitHub annotations show up in the run summary and the checks API.
    print(f'::{level}::{text}', flush=True)


ua = (os.getenv('SEC_USER_AGENT') or '').strip()
note('notice', f"SEC_USER_AGENT 설정됨={bool(ua)} · 이름+이메일 형식={bool(re.search(r'\\S+\\s+\\S+@\\S+\\.\\S+', ua))}")
try:
    ip = urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode()
    note('notice', f'실행 서버 IP 앞부분 {".".join(ip.split(".")[:2])}.x.x')
except Exception:
    pass

URL = 'https://www.sec.gov/files/company_tickers.json'
VARIANTS = {
    '현재 코드 방식': {'User-Agent': ua, 'Referer': 'https://www.sec.gov/', 'Accept': 'application/json,text/plain,*/*', 'Accept-Language': 'en-US,en;q=0.9'},
    'SEC 권장 최소 헤더': {'User-Agent': ua, 'Accept-Encoding': 'gzip, deflate', 'Host': 'www.sec.gov'},
    '연락처만': {'User-Agent': ua},
}
ok = False
for label, headers in VARIANTS.items():
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, headers=headers), timeout=20) as r:
            body = r.read()
            if r.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            note('notice', f'{label}: 성공 HTTP {r.status} · {len(body)} bytes')
            ok = True
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
        text = re.sub(r'<[^>]+>', ' ', raw.decode('utf-8', 'replace'))
        text = re.sub(r'\s+', ' ', text).strip()[:220]
        note('error', f'{label}: HTTP {exc.code} · {text}')
    except Exception as exc:
        note('error', f'{label}: {type(exc).__name__} {str(exc)[:120]}')
sys.exit(0 if ok else 1)
