# wamo-choochoo-dashboard — 작업 규칙

## 생성 파일은 PR에서 커밋하지 않는다

자동 갱신이 평일 하루 60회 넘게 자료 커밋을 올린다(2026-10-30부터 `data` 브랜치, 아래 '자료 분리' 참고). 아래 파일은
그때마다 다시 써지는 **생성 파일**이라, 작업 브랜치에서 함께 커밋하면 합치는 순간마다 충돌해 검사가 돌지 않는다
(2026-10-28 PR #35 가 이 이유로 세 번 다시 만들어졌다).

- `radar.html` ← `radar.template.html` + 자료 (`wamo_publish_insights.py`)
- `wamo_insights.json` (`wamo_insights.py`)
- `index.html` `us.html` (수집 단계가 자료를 안에 넣어 다시 씀)
- `wamo_*.json` 자료·상태 파일 전부

코드를 고친 뒤 로컬에서 `python3 wamo_publish_insights.py` 로 화면을 확인하는 것은 좋지만,
커밋 전에 되돌린다: `git checkout -- radar.html wamo_insights.json index.html us.html 'wamo_*.json'`.

검사와 배포에는 지장이 없다: CI(`ci.yml`)가 `wamo_publish_insights.py` 를 다시 돌려 검사하고,
병합 뒤에는 `radar-sync.yml` 이 `radar/**`·템플릿 변경을 보고 `data` 에 다시 만들어 올린다.
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
- 화면이 새 자료 칸을 읽게 바꿨다면 고정 자료도 다시 만들어 함께 커밋한다(고정 자료는 생성 파일 규칙의 예외).
  먼저 `python3 wamo_data_branch.py setup` 으로 최신 자료를 받은 뒤(위 '자료 분리'):
  `python3 wamo_publish_insights.py && python3 tools/make_radar_fixture.py`
- CI 는 같은 검사를 실제 자료로도 한 번 더 돌리되(`WAMO_RADAR_LIVE=1`) 결과를 막지 않는다.
- `tests/browser_smoke.py` · `tests/discovery_browser.py` 는 자료가 통째로 든 옛 화면(`index.html`·`us.html`,
  합쳐 36MB)을 띄우므로 고정 자료로 옮기지 않았다. 대신 자료 응답·시계는 검사가 통제하고, 특정 종목을 가정하는
  곳은 그날 자료에서 조건에 맞는 종목을 고른다. 새 검사를 쓸 때도 종목을 못 박지 않는다.

## 생성 파일을 손으로 고쳐야 할 때(충돌 등)

손으로 합치지 말고 다시 만든다:
`git checkout --theirs radar.html wamo_insights.json && python3 wamo_insights.py && python3 wamo_publish_insights.py`

## 자료 분리 (2026-10-30 사용자 결정으로 진행)

자동 갱신이 만든 자료 32개는 `main` 이 아니라 **`data` 브랜치**에 쌓는다(저장소가 하루 약 12MB 커지던 것을 멈추려는 것).
`main` 에는 코드만 올린다. 배포(`pages-deploy.yml`)는 `main` 코드에 `data` 자료를 더해(`--overlay`) 사이트를 만든다.
같은 경로가 둘 다 있으면 `data` 쪽이 이긴다. 자료를 못 받으면 배포가 멈추고 이전 사이트가 그대로 남는다.

- 자료를 쓰는 작업 6개(`main.yml` `asia.yml` `macro.yml` `radar-sync.yml` `resume-once.yml` `highs52.yml`)는 첫 단계에서
  `python3 wamo_data_branch.py setup` 으로 자료를 받고, 결과를 `data` 에 올린다(`wamo_data_branch.publish`).
  `main` 에 직접 올리는 길은 막혀 있다(`WAMO_ALLOW_MAIN_PUSH=1` 이 있어야만 열린다. 쓰지 않는다).
- 로컬에서 자료가 필요한 작업(화면 확인, 고정 자료 재생성)은 먼저 `GITHUB_REPOSITORY=dhandori/wamo-choochoo-dashboard
  GITHUB_TOKEN=... python3 wamo_data_branch.py setup` 으로 최신 자료를 받는다. `main` 에 남은 자료 사본은 점점 묵는다.
- `index.html` `us.html` 의 뼈대(HTML)를 고쳐야 하면 `data` 브랜치에서 고친다. `main` 의 사본은 배포에 쓰이지 않는다.
- `data` 브랜치 기록은 매일 01:55 KST 에 줄인다(`data-prune.yml` → `wamo_data_branch.py prune`, 2026-10-31 사용자 결정).
  오늘(KST) 기록은 전부, 어제부터 7일 전까지는 하루 마지막 1개씩 남기고 그보다 오래된 기록은 하나로 합친다.
  최신 자료 내용은 바뀌지 않으며, 정리 중에 올라온 갱신은 `publish` 의 재시도가 새 기록 위에 다시 얹는다.
  되돌릴 때는 남은 하루치 기록에서 파일을 꺼낸다: `git show origin/data~N:파일`.
- 기존 `main` 의 과거 기록(약 160MB)은 그대로다. 줄이려면 저장소 기록을 새로 써야 해서 하지 않았다.

### 되돌리기 (자료 분리를 취소해야 할 때)
코드만 되돌리면 `main` 의 자료가 옛 것이라 누적 파일(`wamo_*_history` `wamo_chart_*` `wamo_ftd_history` `wamo_signal_log` 등)이
며칠치 사라진다. 아래를 **한 커밋**에 함께 넣고, 자료 갱신 작업이 돌지 않는 틈에 합친다.
1. `git checkout origin/data -- $(git ls-tree -r --name-only origin/data)` 로 최신 자료를 `main` 에 복사한다.
2. 자료 분리 커밋들(`wamo_data_branch.py` 연결, 워크플로 5개, `wamo_run.py`)을 되돌린다.
3. `pages-deploy.yml` 의 `--on-duplicate overlay` 를 `src` 로 되돌린다(또는 `--overlay` 자체를 뺀다).

## 52주 신고가 공통 계산 (2026-10-31 사용자 결정)

52주 신고가는 노션 기록과 같은 기준 하나로 `wamo_highs52.py` 가 한 번만 계산한다(`highs52.yml`, 마감 직후 한국 15:52·뉴욕 16:08,
결과는 data 브랜치 `wamo_highs52.json`). 기준: 한국 KOSPI·KOSDAQ 보통주 시총 1,000억 원 이상, 미국 NYSE·NASDAQ·AMEX 보통주
시총 20억 달러 이상, 장중 고가 ≥ 1년 최고가 × 0.9999. 종목 목록은 네이버 시가총액 순(ETF·우선주·스팩·거래정지 제외)과
나스닥 스크리너, 가격은 야후 일봉(거래량 0인 날 제외).
지금은 노션 기록과 나란히 돌리는 단계다(`shadow` 칸에 같은 날 노션과의 겹침·차이). 10/28~10/30 비교: 한국 13/13·8/8·3/4,
미국 63/66·79/80·107/107. 화면·노션·보고서가 이 파일을 읽게 바꾸는 것은 며칠 더 비교한 뒤에 한다.

## 업종 분류 하나로 (2026-10-31 사용자 결정)

공통 업종 분류는 `wamo_taxonomy.py` 다(레이더·흐름판·노션 기록·Global High-Radar가 따로 쓰던 4가지를 하나로).
- 큰 업종: 노션 52주 신고가 기록의 21개(`BIG`, 노션 선택지와 같다). 한국식 테마 표 → 야후 업종(`taxonomy/yahoo_class.json`
  캐시) → WAMO 대분류를 21개로 옮긴 값 순서.
- 세부 산업: WAMO 산업 분류표(시장 흐름판 `config/baskets.json`)의 바구니 이름이 먼저. 겹쳐 들어간 바구니(ESS 등)는 `baskets` 에
  모두 남긴다('배터리 셀'은 늘 그 이름).
- 지금은 52주 신고가 공통 계산(`wamo_highs52.json` 의 `group`·`detail`·`baskets`·`byGroup`)에만 붙는다. 레이더 업종 탭·노션·흐름판이
  이 분류를 읽게 바꾸는 것은 52주 신고가 전환과 함께 한다. 표 원본(테마 표·야후 업종 표)은 이 파일이고, 노션 기록 쪽 `n52_class.py` 는 옛 사본이다.
