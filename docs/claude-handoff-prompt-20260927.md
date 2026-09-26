# Claude Handoff Prompt — Investment Research / Crypto Paper-Trading Platform

아래 프롬프트 전체를 Claude에게 전달하라.

---

## Role

너는 이 프로젝트의 **Senior Quant Research Engineer + Trading System Architect +
Data Integrity Reviewer** 역할을 맡는다.

단순히 수익이 좋아 보이는 파라미터를 찾거나 V2.9를 즉시 V3로 교체하는 것이 목적이
아니다. 현재 코드·PostgreSQL·Parquet·실험 로그를 source of truth로 삼아 다음을 수행한다.

1. 기존 V2.9를 frozen control로 보존한다.
2. ranking, calibration, gate, portfolio, risk, execution, evaluation을 분리해서 본다.
3. 중복·overlap·look-ahead·multiple testing을 통제한다.
4. 재현 가능한 Alpha가 있는지 검증한다.
5. 데이터가 부족하면 부족하다고 명시한다.

이 문서의 설명이 코드와 다르면 **코드를 우선**하고 차이를 보고하라. 기존 설계를 보호할
의무는 없다. 반대로 architecture purity를 위해 정상 동작하는 시스템을 재작성해서도 안 된다.

## Repository and environment

- Repository: `/Users/songjaegeun/git/investment-research-engine`
- Python package: `src/investment`
- Python requirement: 3.12 이상
- Runtime: Docker Compose
- API/dashboard: `http://127.0.0.1:8000`
- PostgreSQL host port: `127.0.0.1:55432`
- PostgreSQL schema: `investment`
- 현재 날짜/시간대: 2026-09-27, Asia/Seoul
- 주요 라이브러리: FastAPI, APScheduler, Polars, pandas, NumPy, SciPy,
  scikit-learn, psycopg, DuckDB

먼저 반드시 다음을 읽어라.

- `AGENTS.md`
- `docs/v29-alpha-research-audit.md`
- `docs/adr/0040-postgresql-concurrent-research-store.md`
- `docs/postgresql-migration-ko.md`
- `src/investment/crypto/application/dynamic_paper_rebalance.py`
- `src/investment/crypto/observation/service.py`
- `src/investment/crypto/derivatives/service.py`
- `src/investment/crypto/derivatives/overlay.py`
- `src/investment/database/postgres_schema.sql`
- `src/investment/crypto/research/signal_registry.py`
- `src/investment/crypto/research/signal_evaluator.py`
- `src/investment/crypto/research/hypothesis_registry.py`
- `research/v29_signal_baseline.py`

## Critical safety rules

### SQLite and Docker Desktop

Compose가 실행 중일 때 macOS host에서 `data/**/*.sqlite3`를 절대 열거나 읽거나 복사하거나
무결성 검사하지 마라. read-only SQLite 조회도 금지다. Docker Desktop VM과 host bind mount가
SQLite WAL lock을 안전하게 공유하지 못해 실제 DB 손상 사례가 있었다.

금지 예:

- host `sqlite3 data/...`
- host Python `sqlite3.connect(...)`
- DuckDB로 SQLite attachment
- host에서 SQLite copy/vacuum/integrity check/migration

Compose 실행 중 상태 조회는 다음 중 하나로만 한다.

1. HTTP API
2. 실행 중인 container 내부
3. PostgreSQL

SQLite를 꼭 다뤄야 하면 먼저 서비스 writer를 중지해야 하지만, 현재 주 저장소는 PostgreSQL이므로
특별한 이유가 없으면 SQLite를 만지지 마라.

### Production/Paper behavior

명시적 승인 없이 다음을 변경하지 마라.

- V2.9 score와 rank
- entry/hold/exit gate
- portfolio state
- scheduler
- paper order/fill
- 현재 실행 중인 experiment identity/config hash
- historical observation/outcome

연구 코드는 trading runtime에서 import하지 않는 별도 경계에 둔다.

### Worktree

현재 worktree에는 PostgreSQL 전환과 연구 프레임워크 관련 미커밋 변경이 있다. 모두 기존 작업으로
간주하고 보존하라. `git reset --hard`, `git checkout --`, 임의 삭제를 사용하지 마라.

## Current runtime status — 2026-09-27 확인

- `investment-engine`: healthy
- `postgres`: healthy
- PostgreSQL 크기: 약 1,889 MB
- 실행 중 experiment 5개는 모두 `RUNNING`
- 각 experiment: 241 decision cohorts, 69,649 snapshot rows
- 최신 판단: 2026-09-26 16:35 UTC 부근
- 계획 종료: 2026-10-01 04:37 UTC 부근

Active experiments:

1. `paper-v2.7-accuracy-fill-v2-pg1-20260924`
2. `paper-v2.9-alpha-pg1-20260924-production-control`
3. `paper-v2.9-alpha-pg1-20260924-volatility-relaxed`
4. `paper-v2.9-alpha-pg1-20260924-pullback-window`
5. `paper-v2.9-alpha-pg1-20260924-concentration-relaxed`

이전 `a1-r2`/`r2-20260923` experiment는 PostgreSQL 전환 때문에 `INTERRUPTED` 되었고 새 pg1
identity로 재시작했다. 중단 전후 데이터를 무심코 하나의 독립 표본처럼 합치지 마라.

## Why PostgreSQL was introduced

과거 SQLite는 Docker writer와 host/다중 agent reader가 동시에 접근하면서 WAL/lock 문제가
반복되었고 실제 손상 파일이 발생했다. 원본 손상 DB는 `data/recovery` 등에 보존되어 있다.

현재 Paper, observation, derivatives 저장소 adapter와 분석 SQL은 PostgreSQL을 지원하며 pg1
experiment가 PostgreSQL에 쓰고 있다. PostgreSQL은 다중 reader/MCP/agent 분석을 위한 현재
source of truth다. `investment_readonly` group role도 schema에 정의되어 있다.

## Historical project evolution

아래는 개념적 요약이다. 정확한 값은 `dynamic_policy_for_version()`을 확인하라.

### V1 / early phases

- Python 기반 개인 Bitcoin/Crypto research engine
- Upbit market/candle 수집
- point-in-time feature/label/backtest 기초
- FastAPI와 개발 dashboard
- Paper portfolio/order/fill/accounting
- APScheduler 자동 수집·판단·outcome 평가

### V2.1–V2.3

- 15분 dynamic universe rebalance
- momentum, liquidity, volatility 기반 후보 선택
- confirmations, cooldown, turnover/fee/loss 한도
- bullish BTC/breadth 조건에 따른 동적 회전율

### V2.4

- extreme score 비선형 penalty
- 고정 선형 expected-relative-return 계산
- fee-adjusted entry/replacement 조건
- 선택 집중도 제한
- rolling 반복 손실 종목 제한
- BTC derivatives shadow observation

### V2.5

- V2.4에서 관측된 4시간 손실을 검증하기 위한 4h 조건 변경
- Binance official basis 품질 문제 때문에 Mark–Index basis proxy인 Squeeze V3 사용

### V2.6

- high-volatility/high-4h-pump 진입 억제
- 최대 보유 1시간
- Squeeze V3 기반 funding/basis/long-short fail-closed entry gate
- 파생 신호는 candidate rank를 바꾸지 않고 BTC context/gate로 사용

### V2.7

- 1h falling-knife 범위 축소
- 재진입 cooldown 4시간
- Algorithmic Crowding V1 entry gate
- Squeeze V3와 Crowding V1은 분리된 신호

### V2.7 accuracy patch

- 신규 진입에서 completed-candle reference 최대 age를 20분으로 제한
- 보유 valuation은 기존 30분 fail-safe 유지

### V2.8

- production-control, momentum shadow, long-short gate, soft-penalty 동시 실험
- derivatives 역할과 측정 정확성 검증
- 결과적으로 많은 논의 끝에 ranking 자체와 gate를 분리해야 한다는 문제 제기

### V2.9

V2.8 production control을 frozen baseline으로 한 one-change-at-a-time suite다.

- production-control: V2.8 control과 동일
- volatility-relaxed: entry volatility 상한을 0.018로 완화하고 quantile cap 제거
- pullback-window: 1h/4h 음의 모멘텀 구간만 허용
- concentration-relaxed: 최대 선택 집중도를 25%에서 50%로 완화

이 네 실험은 기본 Momentum ranking이 동일하다.

## Actual current V2.9 production policy

코드 기준 핵심 값:

- scoring method: `CALM_PULLBACK_RANK`
- scoring universe: eligible 시장 중 24h quote liquidity 상위 20개
- maximum positions: 2
- invested fraction: 50%
- maximum asset weight: 25%
- score component weights:
  - 1h momentum: 0.45
  - 4h momentum: 0.20
  - 24h momentum: 0.10
  - volatility percentile: 0.25
- extreme penalty threshold: 0.70
- maximum nonlinear penalty: 0.35
- entry score hurdle: 0.60
- hold/exit hurdle: 0.45
- maximum hold rank: 6
- required confirmations: 3
- reentry cooldown: 4h
- maximum holding period: 1h
- estimated round-trip cost: 0.20%
  - fee 0.05% each side
  - slippage 0.05% each side
- entry 1h momentum: -0.7% 이상, +0.5% 이하
- entry 4h momentum: -3.0% 이상, +0.5% 이하
- entry 24h momentum: -8% 이상, +8% 이하
- entry volatility absolute cap: 0.003
- entry volatility cohort quantile cap: 50%
- maximum selection concentration: 25%
- rolling loss lookback: 72h
- derivatives gate:
  - funding >= 0.00005
  - Mark–Index basis proxy >= -0.0005
  - global long/short >= 1.0
- crowding gate:
  - long score maximum 0.70
  - bearish unwind maximum 0.55

중요: 이름은 `CALM_PULLBACK_RANK`지만 score 안에서는 높은 volatility percentile이 양의
기여를 하고, 이후 gate에서는 낮은 volatility를 요구한다. 이 혼합 의미를 숨기지 마라.

## Actual end-to-end data flow

```text
Upbit universe snapshots (append-only JSON)
        ↓
Upbit raw candle JSON + normalized 15m OHLCV Parquet
        ↓
point-in-time available_at filtering
        ↓
1h / 4h / 24h returns, 24h volatility, 24h quote liquidity
        ↓
eligible markets → liquidity top 20
        ↓
cross-sectional component percentiles
        ↓
raw V2.9 score → extreme-score penalty
        ↓
fixed linear expected relative return → fee-adjusted expectation
        ↓
entry/hold/exit guards + confirmations
        ↓
BTC derivatives / Squeeze V3 / Crowding V1 context gates
        ↓
cooldown / concentration / rolling loss restrictions
        ↓
selected target weights
        ↓
daily turnover/fee/loss risk budget
        ↓
execution plan → deterministic Paper fill v2
        ↓
portfolio accounting + frozen candidate snapshots
        ↓
15/30/60/120/240/720/1440m forward outcomes
```

## Data storage

### Market/universe files

- Raw Upbit responses: `data/raw/crypto/price/.../*.json`
- Normalized candles: `data/normalized/crypto/price/minute_15/*.parquet`
- 약 291개 15분 market Parquet 파일
- Universe snapshots: `data/normalized/crypto/universe/*.json`
- 약 43개 point-in-time universe snapshot이 있으며 2026-08-13부터 축적

Parquet 저장은 `(source, symbol, open_time)` 중복 시 최신 ingestion을 남긴다. `available_at`
필터로 미래 candle 사용은 방지하지만, 거래소가 과거 candle을 나중에 수정한 경우 당시 원본 revision을
완전히 복원하지 못할 수 있다.

### PostgreSQL tables

- `paper_portfolio`
- `paper_position`
- `paper_execution`
- `paper_rebalance_decision`
- `observation_experiment`
- `decision_snapshot`
- `decision_outcome`
- `decision_outcome_minute`
- `decision_market_context`
- `decision_selection_variant`
- `derivatives_snapshot`
- `mark_price_observation`
- `squeeze_signal`
- `crowding_signal`
- `liquidation_event`
- `coinbase_price_observation`
- `market_stream_status`

### Current overall PostgreSQL coverage observed recently

- `decision_snapshot`: 400k 이상
- `mark_price_observation`: 1.4M 이상
- `coinbase_price_observation`: 약 500k
- `liquidation_event`: 20k 이상
- derivatives history: 2026-08-27 이후
- liquidation/mark stream history: 2026-09-06 이후

실시간 숫자는 직접 PostgreSQL에서 다시 확인하라.

## Derivatives architecture and known facts

- Binance `btcusdt@markPrice@1s` WebSocket
- Binance liquidation stream
- Coinbase BTC-USD ticker WebSocket
- Mark price, index price, funding, Coinbase premium 저장
- OI와 long/short ratio는 공개 stream이 없어 저빈도 REST 유지
- Binance official `/futures/data/basis`는 반복적인 `-1003` IP ban 때문에 정규 호출을 중단
- official `derivatives_snapshot.basis_rate`가 `MISSING_DATA`인 것은 현재 예상된 상태
- Squeeze V3는 `MARK_INDEX_PROXY`를 versioned basis input으로 사용
- 최근 V3 basis input coverage는 100%였음
- Squeeze V2는 official basis 부족으로 `INSUFFICIENT_DATA`일 수 있으므로 V3와 섞지 마라
- Crowding V1은 별도 신호이며 Momentum score에 합산하지 않는다

최근 관측에서는 Squeeze V3가 대부분 `NONE`, Crowding V1이 대부분 `NEUTRAL`이었다. 따라서
파생 context가 성과를 개선했다고 결론 내릴 표본이 없다.

Known operational issue:

- 로그에 `ignored malformed binance_btcusdt_mark_price message`가 다량 발생한 적이 있다.
- 정상 stream `last_message_at`은 계속 갱신되어 전체 수집 중단은 아니었지만, ping/control/combined
  payload 처리 또는 parser noise를 별도 점검할 가치가 있다.

## Data integrity findings

현재 PostgreSQL V2.9 표본에서 확인된 것:

- `reference_at > decision_time`: 0건
- eligible candle reference age 중앙값: 5분
- eligible 최대 reference age: 약 8.84분
- derivative snapshot/signal future timestamp: 0건
- outcome target timestamp mismatch: 0건
- target 전에 평가된 outcome: 0건

그러나 다음은 계속 주의한다.

- 15분 outcome missing 비율이 약 4.4%로 다른 horizon의 1.1~1.7%보다 높았음
- 주요 missing reason:
  - `TARGET_CANDLE_STALE`
  - `NO_POST_DECISION_CANDLE`
- missingness가 시장/시간대와 상관되는지 확인하기 전에는 MCAR로 가정하지 마라
- 동일 종목의 연속 15분 snapshot과 forward horizon이 강하게 overlap함
- 네 V2.9 experiment의 candidate cohort는 사실상 동일하므로 중복 표본으로 합치지 마라

## Current Paper result snapshot — 2026-09-27 확인

| Portfolio | Return | Executions | Completed sells | Winning sells | Realized PnL |
|---|---:|---:|---:|---:|---:|
| V2.7 accuracy | -0.3046% | 10 | 5 | 0 | -3,045.97 KRW |
| V2.9 production | -0.3046% | 10 | 5 | 0 | -3,045.97 KRW |
| V2.9 concentration-relaxed | -0.3046% | 10 | 5 | 0 | -3,045.97 KRW |
| V2.9 pullback-window | 0.0000% | 0 | 0 | 0 | 0 |
| V2.9 volatility-relaxed | -0.8623% | 24 | 12 | 3 | -8,623.49 KRW |

Interpretation:

- V2.7, V2.9 production, concentration-relaxed는 현재까지 같은 거래를 했다.
- concentration relaxation은 이 구간에서 binding condition이 아니었다.
- pullback-window는 한 건도 체결하지 않아 실험 전략으로 기능하지 못했다.
- volatility-relaxed는 거래 수와 손실 tail을 함께 키웠다.
- 손실은 수수료만의 문제가 아니다. 이전 분해에서 가격 방향 손실이 약 2/3, 비용이 약 1/3이었다.

## Recent research framework already added

V2.9 runtime에 연결하지 않은 별도 research-only 코드가 추가되어 있다.

### Signal Registry

File: `src/investment/crypto/research/signal_registry.py`

등록된 독립 신호:

- M0 `m0_v29_score`
  - 정확히 저장된 V2.9 post-penalty score
- M1a `m1_btc_relative_1h`
  - asset 1h return - BTCKRW 1h return
- M1b `m1_universe_relative_1h`
  - asset 1h return - scored cohort median 1h return
- M2 `m2_acceleration_1h_vs_4h`
  - 1h return - 4h return / 4
- M3 `m3_risk_adjusted_4h`
  - 4h return / (15m volatility × sqrt(16))

이 신호들은 합성하지 않았다.

### Candidate Signal Evaluator

File: `src/investment/crypto/research/signal_evaluator.py`

현재 제공:

- pooled Spearman IC
- decision cohort별 cross-sectional Rank IC 평균
- Top-K absolute return
- after-cost return
- cohort-median-relative return
- BTC-relative return
- MFE/MAE
- raw rows / timestamps / assets
- top-K episode count
- top asset concentration
- greedy non-overlapping time-block count

### Hypothesis Registry

File: `src/investment/crypto/research/hypothesis_registry.py`

- append-only JSON identity
- hypothesis, signals, horizon, universe, metrics, train/validation/OOS 기록
- OOS를 한 번 expose하면 다시 미사용 상태로 돌릴 수 없음

### Baseline runner

File: `research/v29_signal_baseline.py`

- PostgreSQL read-only transaction
- production-control 한 개만 사용해 cohort 중복 방지
- 결과 파일:
  `experiments/output/v29-m0-m3-descriptive-20260926.json`
- output directory는 `.gitignore` 대상이므로 local generated artifact다
- 결과는 명시적으로 `DESCRIPTIVE_IN_SAMPLE_RESEARCH_NOT_OOS_VALIDATION`

## M0–M3 preliminary findings

분석 당시 표본:

- 211 decision times
- 60,979 snapshot rows
- 4,220 scored rows
- 289 observed markets
- scored universe에서 등장한 market 36개

### M0 V2.9 control

| Horizon | Mean cross-sectional IC | Top-3 after cost | Top-3 cohort-relative | Non-overlap blocks |
|---|---:|---:|---:|---:|
| 15m | +0.085 | -0.130% | +0.065%p | 130 |
| 1h | +0.066 | -0.098% | +0.077%p | 46 |
| 4h | +0.029 | +0.114% | +0.187%p | 12 |
| 12h | +0.029 | +1.048% | +0.441%p | 4 |

결론:

- V2.9 ranking이 완전히 무정보하다고 단정할 수 없다.
- 단기 ranking lift는 비용보다 작다.
- 4h 비용 후 양수는 독립 블록 12개뿐이므로 검증된 Alpha가 아니다.
- 12h는 블록 4개라 해석 금지 수준이다.
- 실제 selected/Paper 손실과 raw M0 top-K 상대성과가 다르므로 final gate/confirmation/exit 경로를
  분리 분석해야 한다.

### M1 relative momentum

- 1h IC 약 -0.057
- 4h IC 약 +0.018
- M0보다 개선되지 않음

중요한 수학적 사실:

동일 시점 모든 종목에서 같은 BTC return 또는 같은 universe median을 빼면 cross-sectional order는
바뀌지 않는다. 따라서 단순 `ALT - BTC`는 ranking 개선이 아니라 time/context/threshold 신호다.
진짜 residual ranking에는 asset-specific beta, liquidity-matched benchmark 또는 own-history
normalization이 필요하다.

### M2 acceleration

- 1h IC 약 -0.072
- 1h Top-3 after cost 약 -0.233%
- 4h IC 약 +0.041
- 4h Top-3 after cost 약 +0.285%

가능한 해석: 강한 단기 acceleration은 1h 되돌림을 겪지만 이후 4h continuation과 관련될 수 있다.
그러나 4h 독립 블록은 12개뿐이라 아직 약한 가설이다.

### M3 risk-adjusted momentum

- 1h IC는 사실상 0
- 4h IC 약 -0.040
- 4h after cost 약 -0.144%

현재 단순 정의는 개선 근거가 없다.

### Ranking vs calibration

고정 expected-relative-return calibration은 별도로 부정확하다.

| Horizon | Mean predicted relative | Mean realized cohort-relative | MAE |
|---|---:|---:|---:|
| 1h | -0.0033% | +0.0514% | 0.822% |
| 4h | -0.0055% | +0.2227% | 1.646% |

이 결과는 calibration이 경험적이지 않다는 코드와 일치한다. Calibration 실패를 ranking 실패로
혼동하지 마라.

## Gate responsibility classification

현재 gate를 다음처럼 해석하라.

### Predictive Alpha conditions

- falling knife
- short-term spike
- 4h/24h trend/spike
- confirmations
- expected return threshold

### Market/regime context with predictive semantics

- funding
- basis proxy
- long/short ratio
- Squeeze
- Crowding

현재 구현은 일부를 entry gate로 사용하지만 이것이 진정한 portfolio risk라는 뜻은 아니다.

### Mixed Alpha/Risk

- volatility
- cooldown
- rolling repeated-loss block

### Genuine portfolio risk

- maximum positions
- maximum asset weight
- invested fraction
- concentration
- daily realized loss
- daily turnover
- daily fee limit

### Execution/operational

- minimum order notional
- minimum rebalance fraction
- stale/missing market data
- execution deadline

현재 `candidate_reasons_json`은 atomic flags의 배열이지만 여러 조건이 동시에 발생한다. 단순히
`reason contains X`인 blocked/unblocked 평균을 비교하면 confounding이 크다. incremental gate value는
같은 cohort 내 matching, sequential ablation 또는 미리 정한 counterfactual policy가 필요하다.

## Existing research/lifecycle components

이미 다음이 존재하므로 중복 구현하지 마라.

- `src/investment/core/research/evaluator.py`
- `src/investment/core/research/experiment.py`
- `src/investment/core/research/walk_forward.py`
- `src/investment/crypto/application/research_lifecycle_service.py`
- `src/investment/crypto/infrastructure/research_repository.py`

다만 기존 lifecycle은 주로 일봉/일반 backtest용이며 V2.9 15분 candidate cohort의 중복·overlap·episode를
직접 처리하지 않는다. 최근 추가된 signal evaluator가 그 간극을 최소한으로 보완한다.

## Tests already completed

- Ruff passed
- mypy passed for the new research modules
- unit + architecture tests 244 passed
- New tests: `tests/unit/test_signal_research.py`

검증 명령 예:

```bash
.venv/bin/ruff check src/investment/crypto/research research/v29_signal_baseline.py
.venv/bin/mypy \
  src/investment/crypto/research/signal_registry.py \
  src/investment/crypto/research/signal_evaluator.py \
  src/investment/crypto/research/hypothesis_registry.py
.venv/bin/pytest tests/unit tests/architecture/test_bounded_context_dependencies.py -q
```

DB 분석은 비밀번호를 코드/문서에 넣지 말고 runtime environment 또는 container의 설정을 사용하라.

## Current uncommitted work

`git status`에는 두 종류의 변경이 섞여 있다.

### PostgreSQL migration work

- `.env.example`
- `.gitignore`
- `compose.yaml`
- `pyproject.toml`
- `src/investment/__main__.py`
- observation/runtime/FastAPI dependency files
- `src/investment/database/`
- PostgreSQL migration docs/tests

### New research-only work

- `docs/v29-alpha-research-audit.md`
- `research/v29_signal_baseline.py`
- `src/investment/crypto/research/signal_registry.py`
- `src/investment/crypto/research/signal_evaluator.py`
- `src/investment/crypto/research/hypothesis_registry.py`
- `tests/unit/test_signal_research.py`

이 변경을 사용자 작업으로 보고 덮어쓰지 마라. 커밋 여부를 먼저 확인하거나 논리적 단위로 분리하라.

## Open research questions

아직 해결되지 않은 핵심 질문:

1. M0의 약한 positive IC가 시간 구간을 바꿔도 유지되는가?
2. 실제 selected candidates가 raw M0 Top-K보다 나쁜 이유는 어느 gate/confirmation/path dependence인가?
3. 1h acceleration의 음의 성과가 반복 가능한 short-term reversal인가?
4. 4h M0/M2 성과가 단지 상승장 beta인가?
5. V2.9 expected-return calibration을 폐기/재학습해야 하는가?
6. volatility score reward와 volatility entry rejection의 결합이 합리적인가?
7. derivatives context가 Alpha effectiveness를 조건부로 바꾸는가?
8. 15m outcome missingness가 종목/시간대에 편향되어 있는가?
9. actual strategy exit horizon과 fixed 1h/4h outcome의 mismatch가 성과 진단을 왜곡하는가?
10. mean-reversion challenger가 동일 cohort에서 momentum보다 안정적인가?

## Recommended single next hypothesis

새로운 composite score를 만들지 말고 다음 가설 하나를 먼저 사전 등록하라.

> V2.9 상위 후보 중 1시간 모멘텀이 4시간 추세보다 과도하게 가속된 종목은 1시간 내 되돌림이
> 발생하고, 안정적이거나 감속된 후보는 기존 M0의 4시간 상대 우위를 더 잘 보존한다.

권장 검증:

- Primary metric: 1h/4h mean cross-sectional Rank IC 또는 cohort-relative top-K return 중 하나를
  사전에 하나만 primary로 결정
- Universe: frozen V2.9 scored liquidity top-20
- Same timestamps/outcomes/cost assumptions as M0
- Contiguous train/validation split
- Horizon 이상의 purge/embargo
- Episode/block bootstrap
- threshold optimization 금지
- 사전 정의한 2~3개 acceleration bucket만 비교
- OOS는 hypothesis registry에서 한 번만 expose

이 가설이 실패해도 정상적인 연구 결과다.

## Work requested from you

첫 응답에서 production 코드를 변경하지 마라. 다음 순서로 진행하라.

1. 위 문서와 코드를 직접 확인하고 잘못된 handoff 내용을 지적한다.
2. Docker/PostgreSQL 현재 상태를 read-only로 확인한다.
3. V2.9 production-control 한 개만 사용해 최근 baseline report를 재생성한다.
4. cohort duplication, missing outcomes, overlap, episode concentration을 검증한다.
5. 추천된 acceleration/deceleration 가설을 Hypothesis Registry에 사전 등록할 구체적 specification으로
   작성한다. 아직 OOS를 열지 않는다.
6. 현재 데이터 기간으로 train/validation/OOS가 가능한지 냉정하게 판단한다.
7. 가능하지 않다면 필요한 최소 관측 기간/episode/block 수를 제안한다.
8. gate counterfactual을 위해 현재 저장 데이터가 충분한지 평가하고, 부족하면 production behavior를
   바꾸지 않는 최소 관측 필드만 제안한다.

첫 deliverable 형식:

### Verified / Corrected Context

### Current Runtime and Data Health

### Reproducibility Risks

### Proposed Hypothesis Specification

### Validation Split and Dependence Control

### Data Sufficiency Verdict

### Minimal Next Code Change, If Any

### Explicit Non-Actions

마지막에는 반드시 다음 중 하나를 선택하라.

- `WAIT FOR MORE DATA`
- `RUN IN-SAMPLE RESEARCH ONLY`
- `READY FOR TIME-SERIES VALIDATION`
- `READY FOR ONE-TIME OOS`

선택 근거를 독립 block/episode 수치로 설명하라. 높은 row count를 독립 표본 수처럼 사용하지 마라.

---

End of handoff prompt.
