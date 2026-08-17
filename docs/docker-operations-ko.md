# Docker 운영 가이드

Investment Engine은 FastAPI와 내부 APScheduler를 하나의 프로세스에서 실행한다. SQLite와
스케줄러의 단일 실행 보장을 위해 Compose service는 항상 한 개만 실행한다.

## 시작과 종료

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f investment-engine
docker compose down
```

코드가 변경되면 이미지를 다시 빌드한다.

```bash
docker compose up -d --build
```

## 데이터 보존

다음 호스트 디렉터리를 컨테이너에 bind mount한다.

```text
./data        → /app/data
./runtime     → /app/runtime
./experiments → /app/experiments
./models      → /app/models
```

`docker compose down`은 이 디렉터리를 삭제하지 않는다. `.env`는 이미지에 포함하지 않고
Compose 실행 시에만 주입한다. `docker compose down -v`는 사용할 필요가 없다.

## 상태 확인

```bash
curl http://127.0.0.1:8000/api/v1/health
docker compose ps
docker compose logs --tail=100 investment-engine
```

- Trading dashboard: <http://127.0.0.1:8000/dashboard>
- Signal diagnostics: <http://127.0.0.1:8000/diagnostics>
- API documentation: <http://127.0.0.1:8000/api/v1/docs>

## 안전 규칙

- 호스트 uvicorn과 Compose를 동시에 실행하지 않는다.
- `docker compose up --scale investment-engine=2`처럼 service를 복제하지 않는다.
- SQLite 파일을 컨테이너 이미지에 복사하지 않는다.
- `.env`의 `INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE=false`를 유지한다.
- Mac이 절전 상태가 되면 Docker 스케줄러도 정상적인 실시간 실행을 보장하지 않는다.
