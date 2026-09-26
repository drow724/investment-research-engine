# PostgreSQL 전환 운영 절차

2026-09-24에 stopped-writer 최종 이관과 PostgreSQL runtime cutover를 완료했다.
`INVESTMENT_DATABASE_URL`이 설정되면 PostgreSQL만 사용하며, 비어 있을 때에만 보존된
SQLite 구현으로 돌아간다. 두 저장소에 동시에 쓰지 않는다.

PostgreSQL 데이터는 기본적으로 Git에서 제외된 `data/postgres`에 저장한다. Docker
Desktop VM의 이미지 저장공간과 분리하기 위한 전용 경로이며, 이 디렉터리의 파일을
SQLite처럼 직접 열지 않는다. 모든 조회와 분석은 PostgreSQL 접속을 통해 수행한다.

## 1. PostgreSQL 시작

```bash
docker compose up -d postgres
docker compose ps postgres
```

기본 개발 DSN:

```text
postgresql://investment_app:investment-local-only@postgres:5432/investment
```

호스트에서 접근할 때는 기존 PostgreSQL과 충돌하지 않도록 기본적으로
`127.0.0.1:55432`에 노출된다.

실제 비밀번호는 `.env`의 `POSTGRES_PASSWORD`로 교체한다. `.env`는 Git에 올리지 않는다.

## 2. Shadow 또는 최종 snapshot 이관

SQLite는 반드시 Docker VM 내부에서 읽는다. 다음 명령은 SQLite online backup으로 먼저
일관된 사본을 만든 뒤 PostgreSQL로 복사한다.

```bash
docker compose run --rm --no-deps investment-engine \
  python -m investment migrate-postgres \
  --dsn postgresql://investment_app:${POSTGRES_PASSWORD}@postgres:5432/investment \
  --paper-db /app/sqlite/paper/crypto-trading.sqlite3 \
  --observation-db /app/sqlite/observations/crypto-forward-v2.9-r2.sqlite3 \
  --derivatives-db /app/sqlite/observations/crypto-derivatives-v2.5-1760aca.sqlite3 \
  --schema investment \
  --direct-read \
  --replace \
  --report /app/experiments/postgres/migration-report.json
```

보고서의 `verified`가 `true`이고 모든 `quick_checks`가 `ok`여야 한다.

`--direct-read`는 Docker VM 내부에서만 사용한다. 디스크 여유가 충분한 환경에서는 이
옵션을 빼면 SQLite online backup 사본을 먼저 생성한다. 최종 cutover에서는 writer를
중지하므로 direct read도 고정된 데이터 집합을 읽는다.

## 3. 에이전트·MCP 읽기 계정

스키마 생성 시 비밀번호가 없는 그룹 역할 `investment_readonly`가 함께 만들어진다.
각 클라이언트에는 별도 로그인과 비밀번호를 발급하고 이 그룹에 가입시킨다.

```sql
CREATE ROLE investment_mcp LOGIN PASSWORD '<긴 임의 비밀번호>';
GRANT investment_readonly TO investment_mcp;
```

MCP와 분석 에이전트에는 `investment_app` 계정을 제공하지 않는다. DB 파일 경로가 아닌
`127.0.0.1:55432`의 PostgreSQL DSN만 제공한다.

## 4. 최종 cutover 전 필수 조건

1. Paper, observation, derivatives PostgreSQL repository parity test
2. 전략 리뷰의 PostgreSQL read-only query 검증
3. PostgreSQL 전용 read-only 분석 role 생성
4. 자동 런타임 중지
5. 최종 이관 및 row-level 검증
6. 새 experiment ID 설정
7. 모든 저장소를 한 번에 PostgreSQL로 전환
8. SQLite 원본을 읽기 전용 archive로 보존

검증 전에는 `.env`의 `INVESTMENT_DATABASE_URL`을 활성화하지 않는다.

런타임은 `INVESTMENT_DATABASE_URL`이 설정되면 세 저장소를 한 PostgreSQL 스키마에
연결하고, 비어 있으면 기존 SQLite 구현으로 되돌아간다. 이 fallback은 cutover 실패 시
롤백을 위한 것이며 두 backend에 동시에 쓰지는 않는다.
