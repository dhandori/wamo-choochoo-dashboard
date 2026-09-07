# 한국투자 데이터 연결 범위

2026-09-07 실제 GitHub Actions 인증·조회 확인. 주문·잔고 API를 호출하지 않는다.

- 기존 추세조건·점수 순 상위 20종목. `inquire-price`의 `stck_prpr`, `per`, `eps`, `pbr`만 시세 보조정보에 사용한다. 기존 가격/RS/점수와 DART 실제 실적은 유지한다.
- 응답 완료 뒤 최소 2초 간격. 일시적 네트워크/HTTP 429·5xx/EGW00201만 최대 3회 요청한다. 인증 거부는 재시도하지 않는다. 연속 시세 실패 5회 또는 240초 초과 시 남은 종목을 미조회로 표시한다.
- 시세 실패 시 3일 이내 이전값은 기존 확인시각과 CACHED 상태로만 표시한다. 성공률에 포함하지 않는다. 누적 오류 3건만으로 전체 루프를 중단하지 않는다.
- `estimate-perform`의 output2/3 DATA1..5와 output4 결산기간만 별도 저장한다. 3일 캐시, 조회일별 최대 100일 보관. 조회일은 제공사의 추정치 작성일이 아니다.
- 실제 삼성전자 응답에 2023.12 DATA가 21310으로 들어오지만 시세 EPS 필드와 단위가 같다고 볼 근거는 없다. 공식 예제도 DATA1..5의 행 이름/단위를 정의하지 않는다. 예상 EPS·Forward PER·리비전·컨센서스는 아직 계산하지 않는다.
- 후속 연결에 필요한 근거: output2/3 각 행의 지표명·배율·회계기준, 연간/분기 구분, 추정치 작성일, 단일 증권사 추정/시장 컨센서스 구분. E 표시와 날짜만으로 이들을 추론하지 않는다.
- 비밀키와 토큰은 프로세스 메모리에만 둔다. 오류 로그에는 HTTP 상태와 검증된 오류코드만 남긴다. 응답 메시지나 인증 응답 원문을 공개하지 않는다.

공식 원본:
- https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/inquire_price
- https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/estimate_perform

읽기 전용 실응답 검사: https://github.com/dhandori/wamo-choochoo-dashboard/actions/runs/34128442941
