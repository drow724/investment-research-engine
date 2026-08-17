# 비트코인 자동매매 프로그램 현재 개발 현황

기준 버전: `0.13.0`
기준일: 2026-08-14

## 한 줄 요약

현재 프로그램은 **실제 돈으로 자동 주문하는 완성형 자동매매 프로그램이 아니라**, 비트코인과
주요 가상자산의 데이터를 수집하고 전략·머신러닝 모델을 검증하며 가상 포트폴리오의 목표
비중을 계산하는 **Research/Paper Trading Engine** 단계다.

실거래 주문은 의도적으로 차단되어 있다. 따라서 지금 실행해도 Upbit 계좌의 자산을 매수하거나
매도하지 않는다.

## 현재 어디까지 완성되었나

| 영역 | 상태 | 사용자가 할 수 있는 일 |
|---|---|---|
| 애플리케이션/API 실행 | 완료 | 웹 문서에서 API를 조회하고 실행할 수 있다. |
| Upbit 공개 시세 수집 | 완료 | KRW 마켓 목록과 일봉 OHLCV를 내려받아 저장할 수 있다. |
| 비트코인 장기 리서치 | 완료 | 가격·수급 관련 가설과 미래 수익률 관계를 분석할 수 있다. |
| 모멘텀 백테스트 | 완료 | BTC·ETH·SOL 등의 과거 전략 성과를 수수료·슬리피지 포함으로 계산한다. |
| Point-in-time 안전장치 | 완료 | 당시 알 수 없던 미래 봉과 미래 종목 정보를 사용하는 오류를 방지한다. |
| 머신러닝 학습 | 연구용 완료 | Ridge와 Gradient Boosting을 walk-forward 방식으로 비교·저장한다. |
| 머신러닝 예측 | 승인 연결 완료 | 검증 기준과 사람의 승인을 통과한 ACTIVE 모델로 기대수익률 순위를 계산한다. |
| Paper 포트폴리오 | 완료 | 가상 현금·보유수량을 SQLite에 저장하고 조회할 수 있다. |
| ML Paper 작업 | Dry-run 완료 | 주문 없이 목표 종목과 목표 비중, 리스크 승인 결과를 계산한다. |
| 전략 실험 수명주기 | 완료 | 새 전략을 Candidate로 검증하고 사람의 승인 후 Champion으로 승격한다. |
| 자동 스케줄 실행 | 기본 구현 | Python Scheduler가 heartbeat, 종목 Snapshot, 시세 수집을 실행한다. |
| 15분봉 중빈도 연구 | 기반 완료 | 15분봉 수집, 4시간 국면, 1시간 리밸런싱 백테스트가 가능하다. |
| 동적 종목 자동선정 | 완료 | KRW 거래 가능 종목 중 데이터·유동성·점수를 통과한 종목을 새로 편입할 수 있다. |
| 자동 Paper 주문 | 안전 모드 구현 | 기본 비활성/Dry-run이며, 명시적으로 설정하면 로컬 Paper 원장만 체결한다. |
| Upbit 실계좌 주문 | 미구현/차단 | API Key 연결, 실제 주문 전송 및 체결 정산이 없다. |

## 프로그램이 현재 수행하는 흐름

```text
Upbit 공개 데이터 수집
        ↓
과거 시점 기준 데이터와 종목 유니버스 구성
        ↓
기술적 특성 계산
        ↓
모멘텀 백테스트 또는 머신러닝 학습
        ↓
Walk-forward / Out-of-sample 검증
        ↓
기존 Champion과 Candidate 비교
        ↓
사람이 승인한 경우에만 새 전략 버전 활성화
        ↓
동적 Universe Paper dry-run으로 목표 비중과 리스크 결과 계산
```

여기서 마지막 단계도 실제 주문은 아니다. 예를 들어 결과가 `BTC 50%, ETH 50%`라고 나와도
프로그램은 Upbit에 매수 요청을 보내지 않는다. Paper 실행을 켜도 로컬 SQLite 잔고만 바뀐다.

### 동적 종목 편입과 리밸런싱

현재 보유 종목 안에서만 비중을 조절하지 않는다. 저장된 최신 KRW 마켓 Snapshot 전체를
출발점으로 삼아 경고 종목을 제외하고, 최소 7일의 완성된 15분봉이 있는 종목을 평가한다.
최근 거래대금 상위 20개 후보에서 1시간·4시간·24시간 모멘텀과 변동성을 조합한 점수가
왕복 비용 허들을 넘는 최대 3개 종목을 선택한다.

선택에서 빠진 기존 Paper 보유 종목은 매도 계획, 새로 선택된 종목은 매수 계획이 생성된다.
최소 주문금액, 종목별 최대 40%, 총 투자비중 90%, 주문 중복 방지 규칙이 적용된다.

현재 로컬 개발 환경은 `.env`를 통해 `paper-main` 자동 Paper 체결이 활성화되어 있다. 앱을
시작하면 포트폴리오가 없을 때 가상 현금 1,000,000 KRW로 한 번만 생성한다. 이후 재시작하거나
초기 현금 설정을 바꿔도 기존 잔고와 포지션은 초기화하지 않는다.

Dashboard의 `Paper 리밸런싱·체결 이력`에는 최근 100건의 매수·매도, 수량, 체결가,
거래금액, 편도 수수료와 매도 실현손익이 표시된다. 이 값은 로컬 SQLite Paper 원장의 실제
기록이며 화면을 새로 열어도 유지된다.

자동 평가는 각 15분봉 수집 직후인 매시 02·17·32·47분에 실행된다. KRW 마켓 편도 수수료
0.05%와 추정 슬리피지 0.05%를 양방향으로 계산해 총 0.20%를 비용 허들로 사용한다. 1시간,
4시간, 24시간 모멘텀을 결합한 점수가 이 허들을 넘지 않으면 거래하지 않는다. 빠른 평가가
수익을 보장하지 않으며, 실거래 전 충분한 Paper 관찰과 별도 검증이 필요하다.

`dynamic-intraday-v2`부터는 신규 진입과 기존 보유의 기준을 분리한다. 신규 종목은 비용을
포함한 0.5% 진입 허들을 두 번 연속 통과해야 한다. 보유 종목은 점수가 -0.2% 아래로
내려가거나 유동성 후보 순위 8위 밖으로 밀릴 때까지 유지하여 15분 단위 순위 변동에 따른
교체를 줄인다.

UTC 하루 기준 회전율 6배, 수수료 0.5%, 확정손실 2% 중 하나를 소진하면 신규 매수는
중단하지만 위험 축소를 위한 매도는 계속 허용한다. 전량 매도는 계산 수량이 아니라 실제
보유수량을 사용하며 `1E-18` 미만 먼지 포지션은 제거한다.

모든 판단은 전략 버전, 종목별 점수, 선정·유지 이유, 주문, 위험 제한과 함께
`paper_rebalance_decision`에 저장된다. Dashboard의 `전략 판단 감사 로그` 또는 다음 API에서
확인할 수 있다.

```text
GET /api/v1/crypto/paper/portfolios/{portfolioId}/rebalance-decisions
```

## 구현된 핵심 기능

### 1. 시장 데이터 수집

- Upbit 공개 API에서 KRW 마켓 목록 수집
- BTC/KRW, ETH/KRW, SOL/KRW 등 일봉 데이터 수집
- 원본 응답은 JSON, 정규화된 가격은 Parquet으로 저장
- 동일 데이터의 중복 저장을 줄이고 수정된 데이터는 구분
- 데이터가 언제 실제로 이용 가능했는지 `available_at`으로 관리

### 2. 백테스트

- 비트코인 시장 추세를 `RISK_ON`, `NEUTRAL`, `RISK_OFF`로 구분
- 여러 가상자산의 모멘텀을 비교해 상위 종목 선택
- 최대 보유 종목 수와 종목별 최대 비중 적용
- 위험 구간 또는 신호가 약할 때 현금 보유 가능
- 신호 계산 다음 시가에 체결되는 것으로 계산해 미래 정보 사용 방지
- 수수료와 슬리피지 반영
- CAGR, Sharpe, Sortino, 최대 낙폭, 변동성, 적중률, 회전율 등을 출력

### 3. 머신러닝 연구

모델 입력에는 다음과 같은 정보가 포함된다.

- 7일·30일·90일 모멘텀
- BTC 대비 상대 강도
- 30일 실현 변동성
- 30일·90일 낙폭
- 거래량 z-score와 평균 거래대금
- BTC 상관관계
- 비트코인 시장 국면

모델은 Ridge와 Histogram Gradient Boosting을 비교한다. 무작위 train/test 분할이 아니라
시간 순서를 지키는 purged walk-forward 검증을 사용한다. 미래 수익률은 평가용 정답으로만
사용되며 모델 입력에는 포함되지 않는다.

학습된 모델은 데이터 checksum, Feature 버전, 학습 기간, 검증 결과와 함께 저장된다.

학습 직후 모델은 `LATEST`일 뿐 운영 모델이 아니다. 선택된 모델의 walk-forward 검증 IC와
별도 test IC가 모두 양수이고, 사용자가 승인 API에 승인자 이름을 입력해야만 `ACTIVE`가 된다.
승인 시각, 승인자, 이전 ACTIVE 모델, 정책 버전과 점수는 별도 감사 파일로 누적된다.

```text
POST /api/v1/crypto/ml/models/{modelId}/activate
GET  /api/v1/crypto/ml/models/active
```

### 4. 전략 변경 승인 절차

전략 파라미터를 바꿨다고 즉시 운영 전략이 바뀌지 않는다.

```text
가설 작성
→ Experiment 생성
→ 새 Strategy Version 생성
→ 동일 데이터로 기존/신규 전략 백테스트
→ Candidate 평가
→ 사람의 명시적 승인
→ 새 Active Strategy로 승격
```

실패하거나 거절된 실험은 Active Strategy를 변경할 수 없다. 누가 승인했는지도 기록된다.

## 지금 직접 실행하는 방법

### 1. 애플리케이션 시작

프로젝트 디렉터리에서 다음 명령을 실행한다.

```bash
source .venv/bin/activate
uvicorn investment.interfaces.api.fastapi.main:app --reload
```

정상 실행 확인:

```text
http://127.0.0.1:8000/api/v1/health
```

웹 API 화면:

```text
http://127.0.0.1:8000/api/v1/docs
```

처음에는 터미널 명령을 직접 작성하기보다 `/api/v1/docs`의 `Try it out` 기능을 사용하는 것이
가장 간단하다.

### 2. 권장 사용 순서

1. `POST /api/v1/crypto/market/data/sync`로 가격 데이터를 수집한다.
2. `POST /api/v1/crypto/market/universe/snapshots`로 현재 종목 목록을 기록한다.
3. `POST /api/v1/crypto/backtests`로 기본 모멘텀 전략을 검증한다.
4. `POST /api/v1/crypto/ml/train`으로 연구 모델을 학습한다.
5. Research Lifecycle에서 Candidate를 검증하고 사람이 승인한다.
6. `POST /api/v1/crypto/ml/predict`로 승인 모델의 예측을 조회한다.
7. `POST /api/v1/crypto/paper/portfolios/dynamic-rebalance`를 `execute=false`로 실행해 전체
   Universe 기반 편입·제외 계획을 확인한다.

## 사용 시 알아야 할 중요한 제한

### Python 내부 스케줄러가 추가되었다

Python은 Spring 요청 없이 계속 실행되며 설정된 작업을 스스로 호출한다. 현재 heartbeat,
Upbit 종목 Snapshot, 일봉과 유동성 상위 20개 종목의 15분봉 증분 시세 수집이 등록되어 있다.
Paper 포트폴리오 ID를 환경변수로 지정하면 15분 단위 동적 리밸런싱도 등록된다. ML 재학습과
Experiment 실행은 다음 단계에서 Scheduler job으로 추가할 예정이다.

```bash
# 기존 paper-main 포트폴리오를 15분마다 평가하되 주문은 만들지 않는 안전 모드
export INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID=paper-main

# 로컬 Paper 원장 체결까지 원할 때만 추가 (Upbit 실주문은 여전히 불가능)
export INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE=true
python -m investment
```

이 프로젝트의 로컬 `.env`에는 위 포트폴리오 ID와 `EXECUTE=true`가 이미 설정되어 있으므로
별도 `export` 없이 `python -m investment`만 실행하면 된다. `.env`는 Git에 커밋되지 않는다.

```text
Daily: 데이터 수집 → 검증 → Feature 계산 → 예측 → Paper dry-run
Weekly: 재학습 → Challenger 평가 → Drift 확인
Monthly: 전체 walk-forward 검증 → 성과 저하 검토
```

Spring은 실행 주체가 아니라 heartbeat와 작업 이벤트를 저장하고 Dashboard에 보여주는 역할이다.
Spring 상태 수신 서버가 중단되어도 Python Research Job의 성공·실패 결과는 바뀌지 않는다.

### 학습한 최신 모델이 자동으로 운영 모델이 되지 않는다

`train`은 모델 파일을 만들 뿐이다. 기본 예측은 검증 정책을 통과하고 승인 API에서 사람이
명시적으로 승인한 `ACTIVE` 모델만 사용한다. 새 모델을 학습해도 기존 ACTIVE 모델은 자동으로
교체되지 않는다.

### 전략 Champion과 실제 주문 실행은 아직 연결되지 않았다

전략 버전의 생성·검증·승격은 구현됐지만, 자동 실행기가 Active Strategy를 읽어 실제 Paper
주문으로 변환하는 end-to-end 작업은 아직 없다.

### 과거 유니버스 데이터가 부족할 수 있다

종목 유니버스 Snapshot은 프로그램을 운영한 이후부터 쌓인다. 첫 Snapshot보다 과거인 시점은
당시 상장 종목을 확정할 수 없다. `STATIC_EXPLICIT` 모드로 연구할 수는 있지만 생존편향 위험이
있으며 결과에 해당 경고가 기록된다.

### 수익을 보장하지 않는다

백테스트와 머신러닝 점수가 좋더라도 미래 수익을 의미하지 않는다. 거래소 장애, 유동성,
호가 간격, 급격한 가격 변화, 세금 및 실제 체결 차이도 현재 연구 결과에 완전히 반영되지 않는다.

## 실거래까지 남은 작업

최소한 다음 단계가 완료되어야 실제 자금을 연결할 수 있다.

1. Model Candidate 평가·수동 승격과 `ACTIVE` 모델 연결
2. Active Strategy/Model과 동적 Paper 실행기의 승인 연결
3. Upbit 주문 단위·호가 단위·최소 주문금액 처리
4. 미체결·부분 체결·취소·재시도 상태 머신
5. 거래소 잔고와 내부 원장의 정기 대사
6. 일일 손실 한도, 최대 낙폭 중단, kill switch
7. API Key 암호화 및 권한 분리
8. 감사 로그와 운영 알림
9. 충분한 기간의 shadow/paper trading
10. 별도 승인 후 제한된 금액으로 live adapter 활성화

## 현재 단계에 대한 현실적인 평가

현재는 **연구·백테스트·모델 검증 기반은 상당 부분 완성됐고, 주문 자동화와 운영 안전장치는 아직
구현 전인 상태**다. 따라서 “어떤 전략을 쓸 것인가”를 재현 가능하게 실험하기에는 적합하지만,
“켜 두면 알아서 실제 매매하는 프로그램”으로 사용해서는 안 된다.

다음 개발 목표는 `Model Experiment → Candidate → Human Approval → ACTIVE Model` 연결과,
승인된 Strategy/Model만 사용하는 완전한 Paper Trading 실행 루프다.
# 7일 동결 Paper 관찰

현재 `.env`에는 `paper-v2.1-forward-observation-20260814`가 활성화되어 있다. 애플리케이션을
평소처럼 실행하면 실험은 최초 시작 시각부터 168시간 동안 자동으로 유지된다. 전략 설정
해시는 시작 시 고정되며 변경이 발견되면 관찰이 무효화된다.

```bash
python -m investment observation start
python -m investment observation status
python -m investment observation evaluate
python -m investment observation report
```

72시간에는 `status` 또는 아래 health API로 누락 주기, outcome backlog, 데이터 누락,
런타임 실패, 중복/lock skip만 확인한다. 이 체크포인트에서 전략 파라미터를 변경하지 않는다.

```text
GET /api/v1/experiments/current
GET /api/v1/experiments/{experiment_id}/health
GET /api/v1/experiments/{experiment_id}/metrics
GET /api/v1/experiments/{experiment_id}/decisions
GET /api/v1/experiments/{experiment_id}/report
```

168시간 이후 `report`가 생성하는 JSON은 사람의 연구 검토 자료이며 실거래 전환이나 전략
승인을 자동으로 수행하지 않는다. 마지막 결정들의 24시간 outcome은 관찰 종료 후 최대
24시간 동안 추가로 성숙할 수 있다.
