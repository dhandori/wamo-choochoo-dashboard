"""Recover discovery outages using recent identities, never cached prices."""
from datetime import date, datetime, timezone
import math
import re

FIELDS = ('ticker','displayTicker','stock_code','name','market','krx_market','exchange',
          'market_cap_krw','market_cap_usd','sector','industry','industryEn','sectorEn',
          'krxSector','sectorSource','sectorPath','sectorConfidence','instrumentType')


def load_universe(primary, previous, market, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        result = primary()
        rows, excluded = result if isinstance(result, tuple) else (result, 0)
        if not isinstance(rows, list) or not rows:
            raise ValueError('empty universe')
        return rows, excluded, {'status':'LIVE','asOf':now.date().isoformat(),
                               'scope':'공급자 최신 종목 목록','count':len(rows)}
    except Exception as error:
        cause = type(error).__name__  # Never publish arbitrary response/credential bodies.
    meta = previous.get('meta') or {}
    recovered = meta.get('universeRecovery') or {}
    cached_date = recovered.get('asOf') or meta.get('asOf')
    try:
        age = (now.date() - date.fromisoformat(cached_date)).days
        if meta.get('market') != market or not 0 <= age <= 31:
            raise ValueError('cache market/date')
        rows, seen = [], set()
        for stock in previous.get('stocks') or []:
            ticker = stock.get('ticker')
            if not isinstance(ticker,str) or ticker in seen or not stock.get('name'):
                raise ValueError('identity')
            if market == 'KR':
                code, exchange = stock.get('stock_code'),stock.get('krx_market')
                suffix = {'KOSPI':'.KS','KOSDAQ':'.KQ'}.get(exchange)
                if not isinstance(code,str) or not re.fullmatch(r'[A-Z0-9]{6}',code) or not suffix or ticker != code+suffix:
                    raise ValueError('KR identity')
            elif not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,11}',ticker):
                raise ValueError('US identity')
            cap = stock.get('market_cap_usd' if market=='US' else 'market_cap_krw')
            if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not math.isfinite(cap) or cap<=0:
                raise ValueError('capitalization')
            rows.append({key:stock[key] for key in FIELDS if key in stock})
            seen.add(ticker)
        if not rows:
            raise ValueError('missing cached identities')
    except (TypeError,ValueError,AttributeError):
        raise RuntimeError('종목 목록 연결 실패 · 사용 가능한 최근 검증 목록 없음') from None
    warning = f'종목 목록 연결 지연 · {cached_date} 검증 종목 {len(rows)}개로 새 가격 조회 · 신규 편입 종목 제외'
    print(warning,flush=True)
    return rows, 0, {'status':'CACHED_UNIVERSE','asOf':cached_date,'count':len(rows),
                     'scope':'이전 정밀계산 종목군','warning':warning,'failureType':cause}
