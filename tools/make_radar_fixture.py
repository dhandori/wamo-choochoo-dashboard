"""레이더 브라우저 검사용 고정 자료를 만든다.

tests/radar_browser.py 는 기본으로 이 자료를 읽는다. 그날 시장 상황이나 수집 실패에 따라 검사 결과가
바뀌지 않게 하기 위해서다(2026-10-28 일본 갱신 실패로 main 의 검사가 깨진 일이 있었다).

다시 만들 때: 화면이 새 자료 칸을 읽게 바꿨다면, 그 코드로 자료를 다시 만든 뒤 이 스크립트를 돌려
tests/fixtures/radar/ 를 함께 커밋한다.

    python3 wamo_publish_insights.py && python3 tools/make_radar_fixture.py
"""
from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'radar'
# 레이더 화면이 읽는 자료: 박아 넣는 스냅샷 + 화면이 fetch 하는 파일. 일본·대만 차트는 검사에서 종목 상세를
# 열지 않아 넣지 않는다(합쳐 2MB). 검사가 그 상세를 열게 되면 여기에 더한다.
FILES = ['wamo_insights.json', 'wamo_trend.json', 'wamo_asia_status.json', 'wamo_refresh_status.json',
         'wamo_sp500_movers.json', 'wamo_chart_KR.json', 'wamo_chart_US.json']


def main():
    FIXTURE.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(ROOT / name, FIXTURE / name)
    snapshot = json.loads((FIXTURE / 'wamo_insights.json').read_text(encoding='utf-8'))
    print('고정 자료:', snapshot['generatedAt'], ' / '.join(
        f"{m} {v['status']} {v['asOf']} ({len(v['stocks'])})" for m, v in snapshot['markets'].items()))


if __name__ == '__main__':
    main()
