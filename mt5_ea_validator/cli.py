from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mt5_ea_validator.configuration import (
    ConfigurationError,
    load_scenario,
    select_target_deposit,
)
from mt5_ea_validator.mt5 import MT5Error, validate_environment
from mt5_ea_validator.runner import CampaignError, run_campaign
from mt5_ea_validator.setfile import SetFileError, validate_dedicated_set
from mt5_ea_validator.transaction_cost import (
    DEFAULT_BUILD_FROM,
    DEFAULT_BUILD_TO_EXCLUSIVE,
    DEFAULT_STRESS_POINTS,
    CustomSymbolBuilder,
    TransactionCostError,
    load_build_index,
    run_cost_stress_suite,
)


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
    run.add_argument(
        "--target-deposit",
        type=int,
        help="run benchmark gate followed by only this deposit",
    )

    build_cost = subparsers.add_parser(
        "build-cost-symbols",
        help="XAUUSDリアルティックからTransaction Cost Stress用カスタムシンボルを生成します",
    )
    build_cost.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    build_cost.add_argument(
        "--stress-points",
        type=int,
        nargs="+",
        default=list(DEFAULT_STRESS_POINTS),
        help="片道の不利価格幅（points）。0から昇順で指定します",
    )
    build_cost.add_argument("--from-date", default=DEFAULT_BUILD_FROM)
    build_cost.add_argument(
        "--to-date-exclusive", default=DEFAULT_BUILD_TO_EXCLUSIVE
    )
    build_cost.add_argument("--timeout-seconds", type=int, default=7200)

    cost_suite = subparsers.add_parser(
        "run-cost-suite",
        help="S=0ゲート後にTransaction Cost Stress第1層を直列実行します",
    )
    cost_suite.add_argument("--build-index", type=Path, nargs="+", required=True)
    cost_suite.add_argument("--config", type=Path, nargs="+", required=True)
    cost_suite.add_argument(
        "--resume-run",
        type=Path,
        help="中断済み実行フォルダの完了結果を検証し、未完了ケースだけ再開します",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run-cost-suite":
            scenarios = tuple(load_scenario(path) for path in args.config)
            for scenario in scenarios:
                validate_dedicated_set(
                    scenario.set_source,
                    scenario.required_set_values,
                    label=f"{scenario.ea_id}/{scenario.wf}",
                )
            run_directory = run_cost_stress_suite(
                scenarios,
                tuple(
                    build
                    for index_path in args.build_index
                    for build in load_build_index(index_path)
                ),
                resume_directory=args.resume_run,
            )
            print(f"Transaction Cost Stress completed: {run_directory}")
            return 0

        scenario = load_scenario(args.config)
        if args.command == "build-cost-symbols":
            index_path = CustomSymbolBuilder(scenario).build(
                stress_points=args.stress_points,
                from_date=args.from_date,
                to_date_exclusive=args.to_date_exclusive,
                timeout_seconds=args.timeout_seconds,
            )
            print(f"Custom symbols completed: {index_path}")
            return 0
        if args.command == "run" and args.target_deposit is not None:
            scenario = select_target_deposit(scenario, args.target_deposit)
        validate_dedicated_set(
            scenario.set_source,
            scenario.required_set_values,
            label=f"{scenario.ea_id}/{scenario.wf}",
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
    except (
        ConfigurationError,
        SetFileError,
        MT5Error,
        TransactionCostError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2
