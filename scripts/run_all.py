#!/usr/bin/env python3
"""Run all project scripts from one entry point."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"


@dataclass(frozen=True)
class ScriptCommand:
    name: str
    path: Path


SCRIPT_COMMANDS = (
    ScriptCommand("rolling_one_year_return_moomoo", SCRIPTS_DIR / "rolling_one_year_return_moomoo.py"),
    ScriptCommand("amzn_open_close_avg_p5_moomoo", SCRIPTS_DIR / "amzn_open_close_avg_p5_moomoo.py"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all scripts in the project.")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Run remaining scripts even if one script fails.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures: list[str] = []

    for command in SCRIPT_COMMANDS:
        print(f"\n=== Running {command.name} ===")
        result = subprocess.run([sys.executable, str(command.path)], cwd=ROOT_DIR)
        if result.returncode != 0:
            failures.append(command.name)
            print(f"=== {command.name} failed with exit code {result.returncode} ===")
            if not args.continue_on_error:
                return result.returncode

    if failures:
        print("\nFailed scripts:")
        for name in failures:
            print(f"- {name}")
        return 1

    print("\nAll scripts completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
