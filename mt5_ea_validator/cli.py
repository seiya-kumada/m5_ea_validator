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
from mt5_ea_validator.tick_history import (
    TickHistoryError,
    TickHistoryGateError,
    audit_saved_ubs_run,
)
from mt5_ea_validator.ubs_inventory import UBSInventoryError, create_ubs_inventory
from mt5_ea_validator.ubs_model_comparison import (
    UBSModelComparisonCampaignError,
    UBSModelComparisonError,
    run_ubs_model_comparison_suite,
)
from mt5_ea_validator.ubs_quarterly import (
    UBSQuarterlyCampaignError,
    UBSQuarterlyError,
    run_ubs_quarterly_suite,
)
from mt5_ea_validator.ubs_smoke import (
    UBSSmokeCampaignError,
    UBSSmokeError,
    build_ubs_effective_sets,
    build_ubs_faithful_augmented_sets,
    load_ubs_smoke_settings,
    run_ubs_default_control,
    run_ubs_effective_validation,
    run_ubs_faithful_validation,
    run_ubs_smoke_suite,
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

    ubs_tick_audit = subparsers.add_parser(
        "audit-ubs-tick-data",
        help="Audit real-tick synchronization and fallback warnings in a saved UBS run",
    )
    ubs_tick_audit.add_argument("--run", type=Path, required=True)

    ubs_smoke = subparsers.add_parser(
        "run-ubs-smoke",
        help="UBS Gold 7戦略のv7.5互換性スモークテストを直列実行します",
    )
    ubs_smoke.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_smoke.add_argument(
        "--strategy-id",
        action="append",
        help="Run only the selected configured strategy; repeat for multiple strategies",
    )

    ubs_default = subparsers.add_parser(
        "run-ubs-default-control",
        help="ExpertParametersなしでUBS v7.5のTester基準入力を記録します",
    )
    ubs_default.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_default.add_argument("--smoke-run", type=Path, required=True)

    ubs_build_effective = subparsers.add_parser(
        "build-ubs-effective-sets",
        help="Build fully specified UBS v7.5 sets from applied MT5 inputs",
    )
    ubs_build_effective.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_build_effective.add_argument("--smoke-run", type=Path, required=True)
    ubs_build_effective.add_argument(
        "--default-control", type=Path, required=True
    )

    ubs_validate_effective = subparsers.add_parser(
        "run-ubs-effective-validation",
        help="Reload fully specified UBS v7.5 sets and require all 219 inputs",
    )
    ubs_validate_effective.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_validate_effective.add_argument(
        "--derived-manifest", type=Path, required=True
    )

    ubs_build_faithful = subparsers.add_parser(
        "build-ubs-faithful-sets",
        help="Preserve original UBS inputs and append missing v7.5 inputs",
    )
    ubs_build_faithful.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_build_faithful.add_argument("--smoke-run", type=Path, required=True)
    ubs_build_faithful.add_argument(
        "--default-control", type=Path, required=True
    )

    ubs_validate_faithful = subparsers.add_parser(
        "run-ubs-faithful-validation",
        help="Validate UBS visible inputs and reproduce the source smoke behavior",
    )
    ubs_validate_faithful.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_validate_faithful.add_argument(
        "--derived-manifest", type=Path, required=True
    )

    ubs_quarterly = subparsers.add_parser(
        "run-ubs-quarterly",
        help="Run seven UBS strategies across WF1-WF4 with 1 minute OHLC",
    )
    ubs_quarterly.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_quarterly.add_argument(
        "--faithful-manifest", type=Path, required=True
    )
    ubs_quarterly.add_argument(
        "--faithful-validation-run", type=Path, required=True
    )
    ubs_quarterly.add_argument("--resume-run", type=Path)

    ubs_model_comparison = subparsers.add_parser(
        "run-ubs-model-comparison",
        help="Compare seven UBS strategies with OHLC and real ticks over one year",
    )
    ubs_model_comparison.add_argument(
        "--config", type=Path, default=Path("config/ubs_gold_smoke.json")
    )
    ubs_model_comparison.add_argument(
        "--faithful-manifest", type=Path, required=True
    )
    ubs_model_comparison.add_argument(
        "--faithful-validation-run", type=Path, required=True
    )
    ubs_model_comparison.add_argument("--resume-run", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run-ubs-model-comparison":
            settings = load_ubs_smoke_settings(args.config)
            run_directory = run_ubs_model_comparison_suite(
                settings,
                args.faithful_manifest,
                args.faithful_validation_run,
                resume_directory=args.resume_run,
                progress=print,
            )
            print(f"UBS model comparison completed: {run_directory}")
            return 0

        if args.command == "run-ubs-quarterly":
            settings = load_ubs_smoke_settings(args.config)
            run_directory = run_ubs_quarterly_suite(
                settings,
                args.faithful_manifest,
                args.faithful_validation_run,
                resume_directory=args.resume_run,
                progress=print,
            )
            print(f"UBS quarterly comparison completed: {run_directory}")
            return 0

        if args.command == "audit-ubs-tick-data":
            summary_path = audit_saved_ubs_run(args.run)
            print(f"UBS tick-data audit completed: {summary_path}")
            return 0

        if args.command == "run-ubs-faithful-validation":
            settings = load_ubs_smoke_settings(args.config)
            run_directory = run_ubs_faithful_validation(
                settings, args.derived_manifest
            )
            print(f"UBS faithful-set validation completed: {run_directory}")
            return 0

        if args.command == "build-ubs-faithful-sets":
            settings = load_ubs_smoke_settings(args.config)
            manifest_path = build_ubs_faithful_augmented_sets(
                settings,
                args.smoke_run,
                args.default_control,
            )
            print(f"UBS faithful sets completed: {manifest_path}")
            return 0

        if args.command == "run-ubs-effective-validation":
            settings = load_ubs_smoke_settings(args.config)
            run_directory = run_ubs_effective_validation(
                settings, args.derived_manifest
            )
            print(f"UBS effective-input validation completed: {run_directory}")
            return 0

        if args.command == "build-ubs-effective-sets":
            settings = load_ubs_smoke_settings(args.config)
            manifest_path = build_ubs_effective_sets(
                settings,
                args.smoke_run,
                args.default_control,
            )
            print(f"UBS effective sets completed: {manifest_path}")
            return 0

        if args.command == "run-ubs-default-control":
            settings = load_ubs_smoke_settings(args.config)
            run_directory = run_ubs_default_control(
                settings, args.smoke_run
            )
            print(f"UBS default control completed: {run_directory}")
            return 0

        if args.command == "run-ubs-smoke":
            settings = load_ubs_smoke_settings(args.config)
            selected = tuple(args.strategy_id) if args.strategy_id else None
            run_directory = run_ubs_smoke_suite(
                settings,
                progress=print,
                strategy_ids=selected,
            )
            print(f"UBS smoke completed: {run_directory}")
            return 0

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
    except UBSSmokeCampaignError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Artifacts: {exc.run_directory}", file=sys.stderr)
        return 1
    except UBSQuarterlyCampaignError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Artifacts: {exc.run_directory}", file=sys.stderr)
        return 1
    except UBSModelComparisonCampaignError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Artifacts: {exc.run_directory}", file=sys.stderr)
        return 1
    except TickHistoryGateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Artifacts: {exc.summary_path}", file=sys.stderr)
        return 1
    except (
        ConfigurationError,
        SetFileError,
        MT5Error,
        TransactionCostError,
        SlippageToleranceError,
        UBSInventoryError,
        UBSSmokeError,
        TickHistoryError,
        UBSQuarterlyError,
        UBSModelComparisonError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2
