# wamo-choochoo-dashboard — 작업 규칙

## 생성 파일은 PR에서 커밋하지 않는다

자동 갱신이 평일 하루 60회 넘게 main에 자료 커밋을 올린다. 아래 파일은 그때마다 다시 써지는
**생성 파일**이라, 작업 브랜치에서 함께 커밋하면 합치는 순간마다 충돌해 검사가 돌지 않는다
(2026-10-28 PR #35 가 이 이유로 세 번 다시 만들어졌다).

- `radar.html` ← `radar.template.html` + 자료 (`wamo_publish_insights.py`)
- `wamo_insights.json` (`wamo_insights.py`)
- `index.html` `us.html` (수집 단계가 자료를 안에 넣어 다시 씀)
- `wamo_*.json` 자료·상태 파일 전부

코드를 고친 뒤 로컬에서 `python3 wamo_publish_insights.py` 로 화면을 확인하는 것은 좋지만,
커밋 전에 되돌린다: `git checkout -- radar.html wamo_insights.json index.html us.html 'wamo_*.json'`.

검사와 배포에는 지장이 없다: CI(`ci.yml`)가 `wamo_publish_insights.py` 를 다시 돌려 검사하고,
병합 뒤에는 `radar-sync.yml` 이 `radar/**`·템플릿 변경을 보고 main 에 다시 만들어 올린다.
새 자료 칸을 추가했다면 화면 코드는 그 칸이 아직 없는 자료(병합 직후 1~2분)도 견뎌야 한다.

## 합치기 전

- 작업 브랜치에 main 을 **merge** 한다(rebase 하지 않는다). 생성 파일을 안 건드렸다면 충돌이 없다.
- CI 는 PR 에서만 돈다. 충돌(`mergeable_state: dirty`) 상태면 CI 가 아예 돌지 않는다.
- 모듈 캐시 버전 `?v=` 는 손으로 쓰지 않는다. `radar/` 모듈끼리는 `'./help.js'` 처럼 버전 없이 부르고,
  `wamo_publish_insights.version_assets` 가 `radar.html` 을 만들 때 파일 내용 해시로 CSS·스크립트 주소에
  `?v=` 를 붙이고 import map 을 넣는다. 파일이 바뀔 때만 그 파일의 값이 바뀐다
  (`tests/test_insights_publish.py` 가 손으로 쓴 `?v=` 를 막는다).

## 브라우저 검사의 자료

- `tests/radar_browser.py` 는 `tests/fixtures/radar/` 의 고정 자료로 돈다. 브라우저 시계도 그 자료가 만들어진
  시각에 고정한다. 그래서 그날 시장 상황·수집 실패로 결과가 바뀌지 않는다.
- 화면이 새 자료 칸을 읽게 바꿨다면 고정 자료도 다시 만들어 함께 커밋한다(고정 자료는 생성 파일 규칙의 예외):
  `python3 wamo_publish_insights.py && python3 tools/make_radar_fixture.py`
- CI 는 같은 검사를 실제 자료로도 한 번 더 돌리되(`WAMO_RADAR_LIVE=1`) 결과를 막지 않는다.
- `tests/browser_smoke.py` · `tests/discovery_browser.py` 는 자료가 통째로 든 옛 화면(`index.html`·`us.html`,
  합쳐 36MB)을 띄우므로 고정 자료로 옮기지 않았다. 대신 자료 응답·시계는 검사가 통제하고, 특정 종목을 가정하는
  곳은 그날 자료에서 조건에 맞는 종목을 고른다. 새 검사를 쓸 때도 종목을 못 박지 않는다.

## 생성 파일을 손으로 고쳐야 할 때(충돌 등)

손으로 합치지 말고 다시 만든다:
`git checkout --theirs radar.html wamo_insights.json && python3 wamo_insights.py && python3 wamo_publish_insights.py`

## 정비 숙제 (2026-10-28 검수)

- 자료 커밋을 `data` 브랜치로 분리해 저장소 성장(2.5개월 157MB, `us.html` 27MB 하루 수십 번 재커밋)을 멈춤.
  **저장소가 500MB를 넘으면 한다**(2026-10-29 사용자 결정). 그 전에는 하지 않는다: 지금은 사이트·검사에
  지장이 없고(사이트는 현재 파일만 배포), 하려면 Pages 배포 방식 변경(사용자가 설정에서 직접), 자료를 쓰는
  자동 작업 5개 수정, 다른 저장소(산업강도판·텔레그램 봇)가 읽는 주소 영향이 따른다.
  크기 확인: GitHub 저장소 정보의 `size`(KB 단위, 2026-10-28 기준 160,362).
  이 저장소에서 작업할 때 `size` 가 500,000 을 넘었으면 사용자에게 먼저 알린다.
