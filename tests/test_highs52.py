"""wamo_highs52: 52주 신고가 공통 계산의 시험. 네트워크 대신 가짜 응답(fetch)을 쓴다.

노션 기록과 같은 기준인지(대상 종목 범위·신고가 판정)와, 실제로 틀렸던 사례(ETF 섞임, 거래정지 종목이 같은 가격으로
계속 찍혀 신고가로 잡힘, 'BRK/B' 표기)를 지킨다.
"""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import wamo_highs52 as h

DAY = date(2026, 10, 30)


def bars(days=260, base=100.0, last_high=None, last_vol=1000, flat=False):
    """DAY 로 끝나는 평일 일봉. last_high 를 주면 마지막 날 고가를 그 값으로 둔다."""
    out, d, k = [], DAY, 0
    while len(out) < days:
        if d.weekday() < 5:
            p = base if flat else base + (k % 7) - 3
            out.append((d, p + 1, p - 1, p, 1000))
            k += 1
        d -= timedelta(days=1)
    out.reverse()
    if last_high is not None:
        d0, _, lo, c, _ = out[-1]
        out[-1] = (d0, last_high, last_high - 2, last_high - 0.5, last_vol)
    elif last_vol != 1000:
        d0, hi, lo, c, _ = out[-1]
        out[-1] = (d0, hi, lo, c, last_vol)
    return out


def chart(rows):
    ts = [int(datetime(d.year, d.month, d.day, 6, tzinfo=timezone.utc).timestamp()) for d, *_ in rows]
    return {'chart': {'result': [{'timestamp': ts, 'meta': {'gmtoffset': 0}, 'indicators': {'quote': [{
        'high': [r[1] for r in rows], 'low': [r[2] for r in rows], 'close': [r[3] for r in rows],
        'volume': [r[4] for r in rows]}]}}]}}


class JudgeTests(unittest.TestCase):
    def test_touching_the_one_year_high_counts(self):
        rows = bars(last_high=104.0 * 0.99995)   # 1년 최고가 104 의 0.9999 배 이상이면 신고가(노션 기록과 같은 허용치)
        hi, lo, close, chg = h.judge({d: (a, b, c) for d, a, b, c, _ in rows}, DAY)
        self.assertTrue(hi)
        self.assertFalse(lo)

    def test_below_the_high_does_not_count(self):
        rows = bars(last_high=103.5)
        self.assertFalse(h.judge({d: (a, b, c) for d, a, b, c, _ in rows}, DAY)[0])

    def test_short_history_or_missing_day_is_not_judged(self):
        self.assertIsNone(h.judge({d: (a, b, c) for d, a, b, c, _ in bars(days=10)}, DAY))
        self.assertIsNone(h.judge({d: (a, b, c) for d, a, b, c, _ in bars()[:-1]}, DAY))

    def test_halted_day_is_dropped(self):
        """거래정지 종목은 같은 가격이 계속 찍힌다. 거래량 0인 날은 봉으로 보지 않는다(2026-10-30 삼부토건)."""
        rows = bars(flat=True, last_vol=0)
        got = h.yahoo_bars('001470.KS', DAY, fetch=lambda url, **k: chart(rows))
        self.assertNotIn(DAY, got)
        self.assertIsNone(h.judge(got, DAY))


class UniverseTests(unittest.TestCase):
    def naver(self, pages):
        def fetch(url, **kw):
            n = int(url.split('page=')[1].split('&')[0])
            market = 'KOSPI' if '/KOSPI?' in url else 'KOSDAQ'
            return {'stocks': pages.get((market, n), [])}
        return fetch

    def stock(self, code, name, cap, kind='stock', stop='TRADING'):
        return {'itemCode': code, 'stockName': name, 'marketValueRaw': str(int(cap)), 'stockEndType': kind,
                'tradeStopType': {'name': stop}}

    def test_korea_keeps_common_stocks_above_the_cap_only(self):
        page = [self.stock('005930', '삼성전자', 9e14), self.stock('069500', 'KODEX 200', 2e13, kind='etf'),
                self.stock('005935', '삼성전자우', 5e13), self.stock('001470', '삼부토건', 2e11, stop='SUSPENDED'),
                self.stock('123450', '한화플러스스팩', 2e11), self.stock('000660', 'SK하이닉스', 5e14),
                self.stock('999990', '작은회사', 5e10)]
        got = h.universe_kr(fetch=self.naver({('KOSPI', 1): page}))
        self.assertEqual([x['code'] for x in got], ['005930', '000660'])
        self.assertEqual(got[0]['yahoo'], '005930.KS')

    def test_us_dedupes_share_classes_and_reads_slash_tickers(self):
        rows = [{'symbol': s, 'name': n, 'marketCap': c} for s, n, c in (
            ('GOOGL', 'Alphabet Inc. Class A Common Stock', '3e12'), ('GOOG', 'Alphabet Inc. Class C Capital Stock', '3e12'),
            ('BRK/B', 'Berkshire Hathaway Inc. Class B', '1e12'), ('BRK/A', 'Berkshire Hathaway Inc. Class A', '1e12'),
            ('SPY', 'SPDR S&P 500 ETF Trust', '6e11'), ('ABC.WS', 'ABC Corp Warrant', '3e9'), ('TINY', 'Tiny Co', '1e9'))]
        rows += [{'symbol': f'X{i}', 'name': f'Filler {i}', 'marketCap': '1'} for i in range(1000)]
        got = h.universe_us(fetch=lambda url, **k: {'data': {'rows': rows}})
        codes = sorted(x['code'] for x in got)
        self.assertEqual(codes, ['BRK.B', 'GOOG'])
        self.assertEqual(next(x for x in got if x['code'] == 'BRK.B')['yahoo'], 'BRK-B')


class RunTests(unittest.TestCase):
    def test_result_keeps_the_other_market_and_compares_with_notion(self):
        good = chart(bars(last_high=110.0))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'h.json'
            out.write_text(json.dumps({'markets': {'KR': {'asOf': '2026-10-30', 'highCount': 3}}}), encoding='utf-8')
            universe = [{'code': 'AAA', 'yahoo': 'AAA', 'name': 'A', 'cap': 5e9}]
            orig = h.universe_us
            h.universe_us = lambda fetch=None: universe
            try:
                r = h.run('US', DAY, out, fetch=lambda url, **k: good)
            finally:
                h.universe_us = orig
            doc = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(doc['markets']['KR']['highCount'], 3)   # 다른 시장 결과는 그대로
            self.assertEqual(doc['markets']['US']['highCount'], 1)
            self.assertEqual(r['highs'][0]['code'], 'AAA')
            self.assertTrue(h.already_done('US', DAY, out))
        sh = h.notion_shadow('US', '2026-10-30', r, fetch=lambda: {'미국': {'2026-10-30': {'rows': [{'코드': 'AAA'}, {'코드': 'BBB'}]}}})
        self.assertEqual((sh['notion'], sh['ours'], sh['both'], sh['onlyNotion']), (2, 1, 1, ['BBB']))

    def test_too_many_failures_stop_the_run(self):
        def fail(url, **k):
            raise RuntimeError('boom')
        orig = h.universe_us
        h.universe_us = lambda fetch=None: [{'code': f'S{i}', 'yahoo': f'S{i}', 'name': 'x', 'cap': 5e9} for i in range(10)]
        try:
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError):
                h.run('US', DAY, Path(tmp) / 'h.json', fetch=fail)
        finally:
            h.universe_us = orig


if __name__ == '__main__':
    unittest.main()
