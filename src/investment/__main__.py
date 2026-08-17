"""Run the autonomous engine and its administration API."""

import argparse

import uvicorn

from investment.crypto.observation.cli import run_observation_command


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m investment")
    subcommands = parser.add_subparsers(dest="command")
    observation = subcommands.add_parser("observation")
    observation.add_argument("action", choices=("start", "status", "evaluate", "report", "stop"))
    arguments = parser.parse_args()
    if arguments.command == "observation":
        run_observation_command(arguments.action)
        return
    uvicorn.run("investment.interfaces.api.fastapi.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
