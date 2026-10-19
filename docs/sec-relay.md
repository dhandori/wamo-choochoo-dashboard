# SEC 중계 (사용자 컴퓨터 → 대시보드)

SEC는 GitHub Actions 공용 서버 주소를 "Request Rate Threshold Exceeded"로 막는다
(연락처 `SEC_USER_AGENT`를 넣어도 같음, 2026-10-19 확인). 같은 시각 사용자 컴퓨터에서는
정상 접속된다. 그래서 주 1회 Claude 앱 브라우저가 SEC 자료를 받아 숫자만 넘긴다.

1. `python wamo_sec_relay.py targets` — 미국 갱신과 같은 우선순위로 SEC 신규 대상 80종목.
2. Claude 앱 브라우저(data.sec.gov 탭)에서 `tools/sec_relay.js`를 실행하고
   `SEC_RELAY.run(종목목록)` → 회사당 약 10KB 결과. 80종목에 약 1분.
3. `python wamo_sec_relay.py apply relay.json` — `wamo_us_sec_cache.json`에 `secStatus=LIVE`,
   `secFetchedAt=오늘`로 저장. 정규 미국 갱신이 7일 동안 "SEC 캐시"로 사용한다.

`tools/sec_relay.js`의 계산은 `wamo_update_us_sec.py`를 그대로 옮긴 것이며 반올림은 파이썬이 한다.
`tests/test_sec_relay.py`가 실제 회사 자료(FLNC)와 경계 사례에서 두 경로 결과가 완전히 같음을 확인한다.

예약: 매주 일요일 20:00 KST, 컴퓨터와 Claude 앱이 켜져 있어야 한다. 꺼져 있으면 그 주는 건너뛰고
보관 자료가 7일 지나면 기존처럼 Nasdaq 대체 자료를 쓴다.
