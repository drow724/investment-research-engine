"""Run the autonomous engine and its administration API."""

import argparse
from pathlib import Path

import uvicorn

from investment.crypto.observation.cli import run_observation_command
from investment.crypto.strategy_registry import StrategyConfigError
from investment.crypto.strategy_review_cli import (
    configure_strategy_review_parser,
    run_strategy_review_command,
)
from investment.crypto.strategy_review_workflow import StrategyReviewError
from investment.database import migrate_sqlite_to_postgres


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m investment")
    subcommands = parser.add_subparsers(dest="command")
    observation = subcommands.add_parser("observation")
    observation.add_argument("action", choices=("start", "status", "evaluate", "report", "stop"))
    configure_strategy_review_parser(subcommands)
    migration = subcommands.add_parser("migrate-postgres")
    migration.add_argument("--dsn", required=True)
    migration.add_argument("--schema", default="investment")
    migration.add_argument("--paper-db", type=Path, required=True)
    migration.add_argument("--observation-db", type=Path, required=True)
    migration.add_argument("--derivatives-db", type=Path, required=True)
    migration.add_argument("--replace", action="store_true")
    migration.add_argument(
        "--direct-read",
        action="store_true",
        help="read SQLite in place; use only inside the Docker VM or after stopping writers",
    )
    migration.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "observation":
        run_observation_command(arguments.action)
        return
    if arguments.command == "strategy-review":
        try:
            run_strategy_review_command(arguments)
        except (StrategyConfigError, StrategyReviewError) as error:
            raise SystemExit(str(error)) from error
        return
    if arguments.command == "migrate-postgres":
        report = migrate_sqlite_to_postgres(
            arguments.dsn,
            paper_database=arguments.paper_db,
            observation_database=arguments.observation_db,
            derivatives_database=arguments.derivatives_db,
            schema=arguments.schema,
            replace=arguments.replace,
            snapshot_sources=not arguments.direct_read,
        )
        payload = report.to_json()
        if arguments.report is not None:
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            arguments.report.write_text(payload + "\n")
        print(payload)
        return
    uvicorn.run("investment.interfaces.api.fastapi.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
