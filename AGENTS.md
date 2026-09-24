# Repository operation rules

## SQLite safety with Docker Desktop

- Never open, query, copy, migrate, or run integrity checks against `data/**/*.sqlite3` from the
  macOS host while the Compose service is running. This includes `sqlite3`, Python `sqlite3`,
  DuckDB, and other readers, even in read-only mode.
- Docker Desktop's VM and the macOS host do not reliably share SQLite WAL locks on bind mounts.
  A host-side read can therefore corrupt a database that the container is writing.
- While Compose is running, inspect SQLite-backed state through the HTTP API or execute the query
  inside the running container. If neither is available, stop the service first with
  `docker compose up -d --scale investment-engine=0`, perform the host-side operation, and restart
  it with `docker compose up -d investment-engine`.
