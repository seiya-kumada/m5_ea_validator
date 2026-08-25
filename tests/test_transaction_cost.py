from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mt5_ea_validator.configuration import load_scenario
from mt5_ea_validator.mt5 import TestExecution
from mt5_ea_validator.report import DealAudit, ReportMetrics
from mt5_ea_validator.transaction_cost import (
    TransactionCostError,
    CostStressExecutor,
    load_build_index,
    metric_deltas,
    parse_symbol_build_manifest,
    render_symbol_builder_ini,
    render_symbol_builder_set,
    run_cost_stress_suite,
    validate_stress_points,
    validate_generation_stress_points,
    validate_metaeditor_compile,
    validate_symbol_build_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))


def _manifest_text(
    *,
    stress: int = 0,
    output_hash: str | None = None,
    source_m1_hash: str = "AAAABBBBCCCCDDDD",
    output_m1_hash: str | None = None,
) -> str:
    source_hash = "0123456789ABCDEF"
    return "\n".join(
        (
            "schema_version=2",
            "status=success",
            "error=",
            "source_symbol=XAUUSD",
            f"custom_symbol=XAUUSD_TCS{stress}_abcdef12",
            "custom_group=MT5EAValidator\\TransactionCost",
            "from_msc=1748736000000",
            "to_msc_exclusive=1782864000000",
            "digits=2",
            "point=0.01",
            "tick_size=0.01",
            f"stress_points={stress}",
            f"stress_price={stress * 0.01:.2f}",
            "source_tick_count=12345",
            "written_tick_count=12345",
            "persisted_tick_count=12345",
            "first_time_msc=1748736000001",
            "last_time_msc=1782863999999",
            f"source_tick_audit_fnv1a64={source_hash}",
            f"output_tick_audit_fnv1a64={output_hash or source_hash}",
            f"persisted_tick_audit_fnv1a64={output_hash or source_hash}",
            "readback_mismatch_from_msc=0",
            "readback_expected_day_count=0",
            "readback_actual_day_count=0",
            "source_m1_bar_count=500000",
            "written_m1_bar_count=500000",
            "persisted_m1_bar_count=500000",
            "first_m1_time=1704157200",
            "last_m1_time=1782863940",
            f"source_m1_audit_fnv1a64={source_m1_hash}",
            f"output_m1_audit_fnv1a64={output_m1_hash or source_m1_hash}",
            f"persisted_m1_audit_fnv1a64={output_m1_hash or source_m1_hash}",
            "m1_readback_mismatch_from=0",
            "m1_readback_expected_day_count=0",
            "m1_readback_actual_day_count=0",
            "source_spread_min_points=10",
            "source_spread_mean_points=21.5000",
            "source_spread_p50_points=20",
            "source_spread_p95_points=35",
            "source_spread_p99_points=50",
            "source_spread_max_points=100",
            "",
        )
    )


class TransactionCostTests(unittest.TestCase):
    def test_stress_points_require_zero_first_and_ascending(self) -> None:
        self.assertEqual(validate_stress_points([0, 10, 30]), (0, 10, 30))
        for invalid in ([10, 30], [0, 30, 10], [0, 10, 10], [0, -1]):
            with self.subTest(invalid=invalid), self.assertRaises(
                TransactionCostError
            ):
                validate_stress_points(invalid)

    def test_generation_can_add_positive_levels_after_zero_inspection(self) -> None:
        self.assertEqual(validate_generation_stress_points([2, 5, 10]), (2, 5, 10))

    def test_metaeditor_exit_one_is_success_when_log_and_ex5_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compiled = root / "script.ex5"
            compiled.write_bytes(b"compiled")
            log = root / "compile.log"
            log.write_text(
                "Result: 0 errors, 0 warnings, 720 ms elapsed",
                encoding="utf-16",
            )
            self.assertEqual(
                validate_metaeditor_compile(
                    return_code=1,
                    compiled_path=compiled,
                    compile_log_path=log,
                ),
                (0, 0),
            )

    def test_cost_executor_stages_and_removes_temporary_commission_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = load_scenario(PROJECT_ROOT / "config" / "qq_wf1_capital.json")
            terminal_data = root / "terminal-data"
            scenario = replace(
                base,
                project_root=root,
                data_directory=terminal_data,
            )
            source = (
                root
                / "config"
                / "transaction_cost"
                / "TitanFX-MT5-Demo_demo.txt"
            )
            source.parent.mkdir(parents=True)
            source.write_bytes(
                (
                    PROJECT_ROOT
                    / "config"
                    / "transaction_cost"
                    / "TitanFX-MT5-Demo_demo.txt"
                ).read_bytes()
            )
            output = root / "results" / "stress_00000"

            def fake_run(command, **kwargs):
                profile = (
                    terminal_data
                    / "MQL5"
                    / "Profiles"
                    / "Tester"
                    / "Groups"
                    / "TitanFX-MT5-Demo_demo.txt"
                )
                self.assertTrue(profile.is_file())
                self.assertIn(
                    "CommissionValue=72.0000",
                    profile.read_text(encoding="utf-16"),
                )
                staging = (
                    terminal_data
                    / "MQL5"
                    / "Files"
                    / "mt5_ea_validator"
                    / "results"
                    / "stress_00000"
                )
                (staging / "QQ_WF1_CAPITAL_3000.htm").write_text(
                    "report", encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0)

            executor = CostStressExecutor(
                scenario,
                stress_points=0,
                process_runner=fake_run,
            )
            execution = executor.execute(3000, output)
            target = executor.commission_profile_target

            self.assertFalse(target.exists())
            self.assertTrue((output / "tester_group_settings.txt").is_file())
            self.assertEqual(execution.report_path.name, "QQ_WF1_TCS_0_3000.htm")

    def test_builder_files_preserve_no_delay_isolation_inputs(self) -> None:
        preset = render_symbol_builder_set(
            source_symbol="XAUUSD",
            custom_symbol="XAUUSD_TCS10_abcdef12",
            from_date="2024.01.01",
            to_date_exclusive="2026.07.01",
            stress_points=10,
            output_file=Path("mt5_ea_validator/run/manifest.txt"),
        )
        ini = render_symbol_builder_ini(
            script_name="mt5_ea_validator\\BuildCostStressSymbol_abcdef12",
            preset_name="builder.set",
        )
        self.assertIn("InpStressPoints=10", preset)
        self.assertIn("InpSourceSymbol=XAUUSD", preset)
        self.assertIn("InpFromDate=2024.01.01", preset)
        self.assertIn("InpOutputFile=mt5_ea_validator\\run\\manifest.txt", preset)
        self.assertIn("AllowLiveTrading=0", ini)
        self.assertIn("AllowDllImport=0", ini)
        self.assertIn("ShutdownTerminal=1", ini)
        self.assertNotIn("Symbol=", ini)

    def test_parses_and_validates_zero_stress_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.txt"
            path.write_text(_manifest_text(), encoding="utf-8")
            manifest = parse_symbol_build_manifest(path)
            validate_symbol_build_manifest(
                manifest,
                source_symbol="XAUUSD",
                custom_symbol="XAUUSD_TCS0_abcdef12",
                stress_points=0,
            )
        self.assertEqual(manifest.source_tick_count, 12345)
        self.assertEqual(manifest.persisted_tick_count, 12345)
        self.assertEqual(manifest.source_spread_p95_points, 35)

    def test_zero_stress_requires_identical_tick_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.txt"
            path.write_text(
                _manifest_text(output_hash="FEDCBA9876543210"), encoding="utf-8"
            )
            manifest = parse_symbol_build_manifest(path)
            with self.assertRaisesRegex(TransactionCostError, "S=0"):
                validate_symbol_build_manifest(manifest)

    def test_build_index_requires_identical_source_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            zero_path = root / "zero.txt"
            ten_path = root / "ten.txt"
            zero_path.write_text(_manifest_text(), encoding="utf-8")
            ten_path.write_text(
                _manifest_text(
                    stress=10,
                    output_hash="FEDCBA9876543210",
                    output_m1_hash="DDDDEEEEFFFF0000",
                ),
                encoding="utf-8",
            )
            zero = parse_symbol_build_manifest(zero_path).to_dict()
            ten = parse_symbol_build_manifest(ten_path).to_dict()
            index = root / "index.json"
            index.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "status": "success",
                        "symbols": [zero, ten],
                    }
                ),
                encoding="utf-8",
            )
            builds = load_build_index(index)
        self.assertEqual([build.stress_points for build in builds], [0, 10])

    def test_build_index_requires_identical_source_m1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            zero_path = root / "zero.txt"
            ten_path = root / "ten.txt"
            zero_path.write_text(_manifest_text(), encoding="utf-8")
            ten_path.write_text(
                _manifest_text(
                    stress=10,
                    output_hash="FEDCBA9876543210",
                    source_m1_hash="1111222233334444",
                    output_m1_hash="DDDDEEEEFFFF0000",
                ),
                encoding="utf-8",
            )
            zero = parse_symbol_build_manifest(zero_path).to_dict()
            ten = parse_symbol_build_manifest(ten_path).to_dict()
            index = root / "index.json"
            index.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "status": "success",
                        "symbols": [zero, ten],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TransactionCostError, "M1"):
                load_build_index(index)

    def test_rejects_legacy_build_index_without_persisted_tick_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "zero.txt"
            manifest_path.write_text(_manifest_text(), encoding="utf-8")
            zero = parse_symbol_build_manifest(manifest_path).to_dict()
            index = root / "index.json"
            index.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "success",
                        "symbols": [zero],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TransactionCostError, "schema"):
                load_build_index(index)

    def test_rejects_persisted_tick_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.txt"
            text = _manifest_text().replace(
                "persisted_tick_audit_fnv1a64=0123456789ABCDEF",
                "persisted_tick_audit_fnv1a64=FEDCBA9876543210",
            )
            path.write_text(text, encoding="utf-8")
            manifest = parse_symbol_build_manifest(path)
            with self.assertRaisesRegex(TransactionCostError, "永続化"):
                validate_symbol_build_manifest(manifest)

    def test_rejects_persisted_m1_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.txt"
            text = _manifest_text().replace(
                "persisted_m1_audit_fnv1a64=AAAABBBBCCCCDDDD",
                "persisted_m1_audit_fnv1a64=FEDCBA9876543210",
            )
            path.write_text(text, encoding="utf-8")
            manifest = parse_symbol_build_manifest(path)
            with self.assertRaisesRegex(TransactionCostError, "M1"):
                validate_symbol_build_manifest(manifest)

    def test_metric_deltas_are_measured_from_generated_zero_baseline(self) -> None:
        baseline = ReportMetrics(100.0, 10, 2.0, 1.0, 3.0, 5.0)
        actual = ReportMetrics(70.0, 8, 1.5, 0.7, 2.0, 7.5)
        baseline_deals = DealAudit(20, "a", 0.0, 0.0, 100.0)
        actual_deals = DealAudit(16, "b", 0.0, 0.0, 70.0)
        deltas = metric_deltas(baseline, actual, baseline_deals, actual_deals)
        self.assertEqual(deltas["net_profit"], -30.0)
        self.assertEqual(deltas["trades"], -2)
        self.assertEqual(deltas["equity_drawdown_percent_points"], 2.5)
        self.assertEqual(deltas["deal_count"], -4)

    def test_suite_runs_positive_stress_only_after_zero_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = load_scenario(PROJECT_ROOT / "config" / "qq_wf1_capital.json")
            terminal_data = root / "terminal-data"
            expert = terminal_data / "MQL5" / "Experts" / "Market" / "QQ.ex5"
            expert.parent.mkdir(parents=True)
            expert.write_bytes(b"ea")
            set_source = root / "QQ_WF1.set"
            set_source.write_bytes(base.set_source.read_bytes())
            scenario = replace(
                base,
                project_root=root,
                data_directory=terminal_data,
                expert_binary=expert,
                set_source=set_source,
                benchmark=replace(
                    base.benchmark,
                    deal_count=0,
                    deal_sequence_sha256=hashlib.sha256(b"\n").hexdigest(),
                ),
            )
            zero_path = root / "zero.txt"
            ten_path = root / "ten.txt"
            zero_path.write_text(_manifest_text(), encoding="utf-8")
            ten_path.write_text(
                _manifest_text(stress=10, output_hash="FEDCBA9876543210"),
                encoding="utf-8",
            )
            builds = (
                parse_symbol_build_manifest(zero_path),
                parse_symbol_build_manifest(ten_path),
            )
            calls: list[int] = []

            class FakeCostExecutor:
                def __init__(self, custom_scenario, stress: int) -> None:
                    self.scenario = custom_scenario
                    self.stress = stress

                def prepare(self) -> None:
                    pass

                def execute(self, deposit: int, output_directory: Path):
                    calls.append(self.stress)
                    output_directory.mkdir(parents=True, exist_ok=False)
                    report = output_directory / "report.htm"
                    profit = 53.98 if self.stress == 0 else 40.00
                    report.write_text(
                        f"""<html><body><table>
                        <tr><td>Total Net Profit:</td><td>{profit:.2f}</td></tr>
                        <tr><td>Profit Factor:</td><td>1.26</td></tr>
                        <tr><td>Recovery Factor:</td><td>0.54</td></tr>
                        <tr><td>Sharpe Ratio:</td><td>2.99</td></tr>
                        <tr><td>Equity Drawdown Relative:</td><td>3.35%</td></tr>
                        <tr><td>Total Trades:</td><td>147</td></tr>
                        </table></body></html>""",
                        encoding="utf-8",
                    )
                    ini = output_directory / "tester.ini"
                    ini.write_text("test", encoding="utf-8")
                    return TestExecution(deposit, ini, report, 0)

            run_directory = run_cost_stress_suite(
                [scenario],
                builds,
                executor_factory=lambda custom_scenario, stress: FakeCostExecutor(
                    custom_scenario, stress
                ),
                now_factory=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=JST),
            )
            suite = json.loads(
                (run_directory / "suite_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(calls, [0, 10])
        self.assertEqual(suite["status"], "success")
        self.assertTrue(suite["results"][0]["zero_stress_gate"]["passed"])
        self.assertEqual(
            suite["results"][1]["metric_deltas_from_zero"]["net_profit"],
            -13.98,
        )

    def test_suite_resume_skips_completed_and_archives_partial_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = load_scenario(PROJECT_ROOT / "config" / "qq_wf1_capital.json")
            terminal_data = root / "terminal-data"
            expert = terminal_data / "MQL5" / "Experts" / "Market" / "QQ.ex5"
            expert.parent.mkdir(parents=True)
            expert.write_bytes(b"ea")
            set_source = root / "QQ_WF1.set"
            set_source.write_bytes(base.set_source.read_bytes())
            scenario = replace(
                base,
                project_root=root,
                data_directory=terminal_data,
                expert_binary=expert,
                set_source=set_source,
                benchmark=replace(
                    base.benchmark,
                    deal_count=0,
                    deal_sequence_sha256=hashlib.sha256(b"\n").hexdigest(),
                ),
            )
            zero_path = root / "zero.txt"
            ten_path = root / "ten.txt"
            zero_path.write_text(_manifest_text(), encoding="utf-8")
            ten_path.write_text(
                _manifest_text(stress=10, output_hash="FEDCBA9876543210"),
                encoding="utf-8",
            )
            builds = (
                parse_symbol_build_manifest(zero_path),
                parse_symbol_build_manifest(ten_path),
            )
            calls: list[int] = []

            class InterruptingExecutor:
                def __init__(self, custom_scenario, stress: int) -> None:
                    self.stress = stress

                def prepare(self) -> None:
                    pass

                def execute(self, deposit: int, output_directory: Path):
                    calls.append(self.stress)
                    output_directory.mkdir(parents=True, exist_ok=False)
                    if self.stress == 10:
                        (output_directory / "partial.txt").write_text(
                            "interrupted", encoding="utf-8"
                        )
                        raise RuntimeError("simulated interruption")
                    report = output_directory / "report.htm"
                    report.write_text(
                        """<html><body><table>
                        <tr><td>Total Net Profit:</td><td>53.98</td></tr>
                        <tr><td>Profit Factor:</td><td>1.26</td></tr>
                        <tr><td>Recovery Factor:</td><td>0.54</td></tr>
                        <tr><td>Sharpe Ratio:</td><td>2.99</td></tr>
                        <tr><td>Equity Drawdown Relative:</td><td>3.35%</td></tr>
                        <tr><td>Total Trades:</td><td>147</td></tr>
                        </table></body></html>""",
                        encoding="utf-8",
                    )
                    ini = output_directory / "tester.ini"
                    ini.write_text("test", encoding="utf-8")
                    return TestExecution(deposit, ini, report, 0)

            fixed_now = lambda: datetime(2026, 8, 25, 12, 30, tzinfo=JST)
            with self.assertRaises(TransactionCostError):
                run_cost_stress_suite(
                    [scenario],
                    builds,
                    executor_factory=lambda custom_scenario, stress: InterruptingExecutor(
                        custom_scenario, stress
                    ),
                    now_factory=fixed_now,
                )
            run_directory = next(
                (root / "data" / "transaction_cost_stress" / "results").iterdir()
            )
            staging_partial = (
                terminal_data
                / "MQL5"
                / "Files"
                / "mt5_ea_validator"
                / scenario.scenario_id
                / "stress_00010"
            )
            staging_partial.mkdir(parents=True)
            (staging_partial / "staged-partial.txt").write_text(
                "interrupted", encoding="utf-8"
            )

            class ResumeExecutor(InterruptingExecutor):
                def execute(self, deposit: int, output_directory: Path):
                    calls.append(self.stress)
                    output_directory.mkdir(parents=True, exist_ok=False)
                    report = output_directory / "report.htm"
                    report.write_text(
                        """<html><body><table>
                        <tr><td>Total Net Profit:</td><td>40.00</td></tr>
                        <tr><td>Profit Factor:</td><td>1.10</td></tr>
                        <tr><td>Recovery Factor:</td><td>0.40</td></tr>
                        <tr><td>Sharpe Ratio:</td><td>2.00</td></tr>
                        <tr><td>Equity Drawdown Relative:</td><td>4.00%</td></tr>
                        <tr><td>Total Trades:</td><td>140</td></tr>
                        </table></body></html>""",
                        encoding="utf-8",
                    )
                    ini = output_directory / "tester.ini"
                    ini.write_text("test", encoding="utf-8")
                    return TestExecution(deposit, ini, report, 0)

            resumed = run_cost_stress_suite(
                [scenario],
                builds,
                executor_factory=lambda custom_scenario, stress: ResumeExecutor(
                    custom_scenario, stress
                ),
                now_factory=fixed_now,
                resume_directory=run_directory,
            )
            suite = json.loads(
                (resumed / "suite_manifest.json").read_text(encoding="utf-8")
            )
            archived = list(
                (run_directory / scenario.scenario_id).glob(
                    "stress_00010_interrupted_*"
                )
            )
            archived_staging = list(
                staging_partial.parent.glob("stress_00010_interrupted_*")
            )
            archived_partial_exists = (
                len(archived) == 1 and (archived[0] / "partial.txt").is_file()
            )
            archived_staging_exists = (
                len(archived_staging) == 1
                and (archived_staging[0] / "staged-partial.txt").is_file()
            )

        self.assertEqual(calls, [0, 10, 10])
        self.assertEqual(suite["status"], "success")
        self.assertEqual(len(suite["results"]), 2)
        self.assertEqual(len(archived), 1)
        self.assertTrue(archived_partial_exists)
        self.assertTrue(archived_staging_exists)


if __name__ == "__main__":
    unittest.main()
