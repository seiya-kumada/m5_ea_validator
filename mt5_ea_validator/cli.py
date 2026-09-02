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
from mt5_ea_validator.slippage_tolerance import (
    DEFAULT_SLIPPAGE_POINTS,
    SlippageToleranceCampaignError,
    SlippageToleranceError,
    run_slippage_tolerance_suite,
)
from mt5_ea_validator.transaction_cost import (
    DEFAULT_BUILD_FROM,
    DEFAULT_BUILD_TO_EXCLUSIVE,
    DEFAULT_STRESS_POINTS,
    CustomSymbolBuilder,
    TransactionCostError,
    load_build_index,
    run_cost_stress_suite,
)
from mt5_ea_validator.ubs_inventory import UBSInventoryError, create_ubs_inventory


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

    slippage_suite = subparsers.add_parser(
        "run-slippage-suite",
        help="QQのEA側許容偏差を比較するスリッページ耐性検証第2層を実行します",
    )
    slippage_suite.add_argument("--config", type=Path, nargs="+", required=True)
    slippage_suite.add_argument(
        "--slippage-points",
        type=int,
        nargs="+",
        default=list(DEFAULT_SLIPPAGE_POINTS),
        help="比較するInpSlippage水準。既存基準100を必ず含めます",
    )
    slippage_suite.add_argument(
        "--execution-mode",
        type=int,
        default=0,
        help="MT5 ExecutionMode: 0=No Delay, -1=Random Delay, 正数=固定ms",
    )
    slippage_suite.add_argument(
        "--resume-run",
        type=Path,
        help="中断済みの第2層実行フォルダから未完了ケースだけ再開します",
    )

    ubs_inventory = subparsers.add_parser(
        "ubs-inventory",
        help="UBSの指定銘柄setとEAバイナリの再現用目録を保存します",
    )
    ubs_inventory.add_argument("--set-directory", type=Path, required=True)
    ubs_inventory.add_argument("--ea-path", type=Path, required=True)
    ubs_inventory.add_argument("--ea-version-label", required=True)
    ubs_inventory.add_argument("--symbol", default="XAUUSD")
    ubs_inventory.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/ubs_strategy_comparison/inventory"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "ubs-inventory":
            run_directory = create_ubs_inventory(
                args.set_directory,
                args.ea_path,
                args.output_root,
                ea_version_label=args.ea_version_label,
                symbol=args.symbol,
            )
            print(f"UBS inventory completed: {run_directory}")
            return 0

        if args.command == "run-slippage-suite":
            scenarios = tuple(load_scenario(path) for path in args.config)
            for scenario in scenarios:
                validate_dedicated_set(
                    scenario.set_source,
                    scenario.required_set_values,
                    label=f"{scenario.ea_id}/{scenario.wf}",
                )
            run_directory = run_slippage_tolerance_suite(
                scenarios,
                slippage_points=args.slippage_points,
                execution_mode=args.execution_mode,
                resume_directory=args.resume_run,
                progress=print,
            )
            print(f"Slippage tolerance Layer 2 completed: {run_directory}")
            return 0

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
    except SlippageToleranceCampaignError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Artifacts: {exc.run_directory}", file=sys.stderr)
        return 1
    except (
        ConfigurationError,
        SetFileError,
        MT5Error,
        TransactionCostError,
        SlippageToleranceError,
        UBSInventoryError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2
