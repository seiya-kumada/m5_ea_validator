from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mt5_ea_validator.configuration import ConfigurationError, load_scenario
from mt5_ea_validator.mt5 import MT5Error, validate_environment
from mt5_ea_validator.runner import CampaignError, run_campaign
from mt5_ea_validator.setfile import SetFileError, validate_qq_wf_set


DEFAULT_CONFIG = Path("config/qq_wf1_capital.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mt5-ea-validator",
        description="MetaTrader 5 EA validation automation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight", help="設定、ファイル、MT5停止状態を確認します"
    )
    preflight.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    run = subparsers.add_parser(
        "run", help="基準Depositの一致をゲートにCapital Stress Testを直列実行します"
    )
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        scenario = load_scenario(args.config)
        validate_qq_wf_set(
            scenario.set_source,
            scenario.required_set_values,
            label=f"QQ/{scenario.wf}",
        )
        if args.command == "preflight":
            validate_environment(scenario, require_stopped=True)
            print("Preflight OK: 設定、MT5環境、専用set、MT5停止状態を確認しました。")
            return 0
        if args.command == "run":
            run_directory = run_campaign(scenario)
            print(f"Campaign completed: {run_directory}")
            return 0
    except CampaignError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Artifacts: {exc.run_directory}", file=sys.stderr)
        return 1
    except (ConfigurationError, SetFileError, MT5Error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2
