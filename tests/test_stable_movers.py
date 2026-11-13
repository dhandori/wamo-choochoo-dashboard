"""옛 '상승률 TOP 30' 화면을 레이더 특징주 탭으로 옮긴 연결(stableRank) 검사."""
import unittest

from wamo_insights import attach_stable_movers


def snapshot():
    return {'markets': {
        'KR': {'asOf': '2026-11-13', 'stocks': [{'symbol': '319660', 'ticker': '319660.KQ'}, {'symbol': '005930', 'ticker': '005930.KS'}]},
        'US': {'asOf': '2026-11-12', 'stocks': [{'symbol': 'AMD', 'ticker': 'AMD', 'stableRank': 9}]},
    }}


CACHE = {'korea': [{'ticker': '000000'}, {'ticker': '319660'}], 'us': [{'ticker': 'AMD'}],
         'meta': {'korea': {'asOf': '2026-11-13', 'candidateCount': 118, 'financialFailCount': 14, 'universe': '시총 1조원 이상'},
                  'us': {'asOf': '2026-11-11'}}}


class StableMoversTests(unittest.TestCase):
    def test_같은_기준일이면_순위와_기준_설명을_붙인다(self):
        result = attach_stable_movers(snapshot(), CACHE)
        kr = result['markets']['KR']
        self.assertEqual(kr['stocks'][0]['stableRank'], 2)       # 순위는 원래 목록의 자리 그대로
        self.assertNotIn('stableRank', kr['stocks'][1])
        self.assertEqual(kr['stableMovers']['count'], 1)
        self.assertEqual(kr['stableMovers']['candidateCount'], 118)

    def test_기준일이_다르면_옛_순위도_지운다(self):
        us = attach_stable_movers(snapshot(), CACHE)['markets']['US']
        self.assertNotIn('stableRank', us['stocks'][0])
        self.assertNotIn('stableMovers', us)

    def test_자료가_없거나_깨져도_그대로_둔다(self):
        for cache in (None, {}, {'korea': 'x', 'meta': None}):
            result = attach_stable_movers(snapshot(), cache)
            self.assertNotIn('stableMovers', result['markets']['KR'])


if __name__ == '__main__':
    unittest.main()
