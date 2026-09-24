"""Run the autonomous engine and its administration API."""

import argparse

import uvicorn

from investment.crypto.observation.cli import run_observation_command
from investment.crypto.strategy_registry import StrategyConfigError
from investment.crypto.strategy_review_cli import (
    configure_strategy_review_parser,
    run_strategy_review_command,
)
from investment.crypto.strategy_review_workflow import StrategyReviewError


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m investment")
    subcommands = parser.add_subparsers(dest="command")
    observation = subcommands.add_parser("observation")
    observation.add_argument("action", choices=("start", "status", "evaluate", "report", "stop"))
    configure_strategy_review_parser(subcommands)
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
    uvicorn.run("investment.interfaces.api.fastapi.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
