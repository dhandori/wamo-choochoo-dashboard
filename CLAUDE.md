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
- 모듈 캐시 버전 `?v=` 는 같은 파일을 부르는 모든 곳이 같은 값이어야 한다. `help.js` 는 다섯 파일이
  부른다 — 하나라도 다르면 두 벌 올라와 팝업이 갈라진다. 올릴 때는 전부 함께 올린다.

## 생성 파일을 손으로 고쳐야 할 때(충돌 등)

손으로 합치지 말고 다시 만든다:
`git checkout --theirs radar.html wamo_insights.json && python3 wamo_insights.py && python3 wamo_publish_insights.py`

## 정비 숙제 (2026-10-28 검수)

- 브라우저 검사(`tests/radar_browser.py`)가 그날 자료를 읽어 시장 상황·수집 실패에 따라 결과가 바뀐다 → 고정 스냅샷으로 전환.
- `?v=` 버전 문자열(23곳)을 `wamo_publish_insights.py` 가 커밋 해시로 일괄 치환하도록.
- 자료 커밋을 `data` 브랜치로 분리해 저장소 성장(2.5개월 157MB, `us.html` 27MB 하루 수십 번 재커밋)을 멈춤.
