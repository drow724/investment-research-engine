# Binance Mark Price 스트림 수집

정규 runtime은 `btcusdt@markPrice@1s`의 mark price, index price, funding rate,
다음 funding 시각을 `mark_price_observation`에 저장한다. 거래소 이벤트 시각과
로컬 수신 시각을 모두 보관하며 중복 이벤트는 최초 기록을 유지한다.

15분 파생 스냅샷 생성 시 두 시각 모두 조회 시점 이전이고 이벤트가 15초 이내이면
WebSocket 값을 사용한다. 없거나 오래되면 `/fapi/v1/premiumIndex`로 보충한다.
V3의 기존 `mark/index - 1` proxy 계산과 이후 V2.7의 시점 기반 신호 조회는 유지한다.
따라서 최종 주문 판단에 임의의 최신 값을 끼워 넣지 않는다.

정규 runtime에서는 `/futures/data/basis`를 호출하지 않는다. Official Basis는
`basis_rate=null`, `missing_fields`로 proxy와 구분되며, 과거 데이터를 덮어쓰지 않는다.
일일 Official Basis 진단 작업도 기본적으로 실행하지 않는다. 클라이언트의
`official_basis_enabled` 옵션은 기존 진단/테스트 호환용이며 runtime에서는 명시적으로
`False`를 전달한다. Official Basis를 요구하는 과거 V2 보조 신호는 결측일 수 있다.

OI·Long/Short ratio·taker ratio는 기존 스케줄의 REST 수집을 유지한다.
이번 변경은 다른 REST endpoint의 IP 제한까지 해제하지 않는다.

`INVESTMENT_RUNTIME_DERIVATIVES_WEBSOCKET_ENABLED=true`일 때 기존 수집기와 함께
시작한다. URL은 `INVESTMENT_BINANCE_MARK_PRICE_WEBSOCKET_URL`로 설정한다.
2026년 Binance 선물 Market stream 경로인 `/market/ws/`를 사용한다. 이전 `/ws/`
경로는 연결이 성립해도 Market 메시지를 전달하지 않을 수 있다.
스트림 상태 이름은 `binance_btcusdt_mark_price`이고 15초 무응답 시 재연결한다.
스냅샷 `source=binance-mark-price-websocket`은 가격/funding 입력의 출처를 뜻한다.
OI와 ratio까지 WebSocket으로 수집했다는 의미는 아니다.

1초마다 수신 시 하루 약 86,400행이 추가된다. 자동 삭제는 하지 않는다.
운영 DB는 Docker 실행 중 호스트에서 직접 열지 말고 API 또는 컨테이너 내부에서 확인한다.
새 코드는 Docker 이미지 재빌드/재시작 이후 적용된다.
