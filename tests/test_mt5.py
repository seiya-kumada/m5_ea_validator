from __future__ import annotations

import tempfile
import unittest
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from mt5_ea_validator.configuration import load_scenario
from mt5_ea_validator.mt5 import (
    MT5Executor,
    logical_cpu_mask,
    render_tester_ini,
    running_terminal_paths,
    target_terminal_is_running,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MT5ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = load_scenario(
            PROJECT_ROOT / "config" / "qq_wf1_capital.json"
        )

    def test_logical_cpu_mask_uses_at_most_requested_cpus(self) -> None:
        self.assertEqual(logical_cpu_mask(12, 2), 0x3)
        self.assertEqual(logical_cpu_mask(1, 2), 0x1)
        self.assertEqual(logical_cpu_mask(None, 2), 0x1)

    def test_ini_has_all_fixed_conditions(self) -> None:
        report = Path("MQL5") / "Files" / "test" / "report.htm"
        text = render_tester_ini(self.scenario, deposit=3000, report_path=report)

        required = (
            "Expert=Market\\Quantum Queen X MT5.ex5",
            "ExpertParameters=QQ_WF1.set",
            "Symbol=XAUUSD",
            "Period=H1",
            "Optimization=0",
            "Model=4",
            "FromDate=2025.07.01",
            "ToDate=2025.09.30",
            "ForwardMode=0",
            "Deposit=3000",
            "Currency=USD",
            "Leverage=500",
            "ExecutionMode=0",
            "ReplaceReport=0",
            "ShutdownTerminal=1",
        )
        for line in required:
            self.assertIn(line, text)
        self.assertIn(f"Report={report}", text)

    def test_deposit_is_the_only_difference_between_capital_inis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = render_tester_ini(
                self.scenario, deposit=3000, report_path=root / "same.htm"
            )
            second = render_tester_ini(
                self.scenario, deposit=1500, report_path=root / "same.htm"
            )

        first_without_deposit = [
            line for line in first.splitlines() if not line.startswith("Deposit=")
        ]
        second_without_deposit = [
            line for line in second.splitlines() if not line.startswith("Deposit=")
        ]
        self.assertEqual(first_without_deposit, second_without_deposit)
        self.assertIn("Deposit=3000", first)
        self.assertIn("Deposit=1500", second)

    def test_ini_can_omit_expert_parameters_for_default_control(self) -> None:
        text = render_tester_ini(
            self.scenario,
            deposit=3000,
            report_path=Path("default.htm"),
            include_expert_parameters=False,
        )

        self.assertNotIn("ExpertParameters=", text)
        self.assertIn("Expert=Market\\Quantum Queen X MT5.ex5", text)
        self.assertIn("Symbol=XAUUSD", text)

    def test_report_name_uses_configured_wf(self) -> None:
        scenario = load_scenario(
            PROJECT_ROOT / "config" / "qq_wf2_capital.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = replace(
                scenario,
                data_directory=root / "terminal-data",
            )
            output = root / "results" / "run-2" / "deposit_3000"

            def fake_run(command, **kwargs):
                staging = (
                    scenario.data_directory
                    / "MQL5"
                    / "Files"
                    / "mt5_ea_validator"
                    / "run-2"
                    / "deposit_3000"
                )
                (staging / "QQ_WF2_CAPITAL_3000.htm").write_text(
                    "report", encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0)

            execution = MT5Executor(scenario, process_runner=fake_run).execute(
                3000, output
            )

        self.assertEqual(execution.report_path.name, "QQ_WF2_CAPITAL_3000.htm")

    def test_report_name_uses_configured_ea_prefix(self) -> None:
        cases = (
            ("smart_gold_hunter_wf1_capital.json", "SGH_WF1_CAPITAL_3000.htm"),
            ("wave_rider_wf3_capital.json", "WR_WF3_CAPITAL_3000.htm"),
        )
        for config_name, expected_name in cases:
            with self.subTest(config=config_name), tempfile.TemporaryDirectory() as temporary:
                scenario = load_scenario(PROJECT_ROOT / "config" / config_name)
                root = Path(temporary)
                scenario = replace(scenario, data_directory=root / "terminal-data")
                output = root / "results" / "run-prefix" / "deposit_3000"

                def fake_run(command, **kwargs):
                    staging = (
                        scenario.data_directory
                        / "MQL5"
                        / "Files"
                        / "mt5_ea_validator"
                        / "run-prefix"
                        / "deposit_3000"
                    )
                    (staging / expected_name).write_text("report", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0)

                execution = MT5Executor(scenario, process_runner=fake_run).execute(
                    3000, output
                )
                self.assertEqual(execution.report_path.name, expected_name)

    @patch("mt5_ea_validator.mt5.running_terminal_paths")
    def test_detects_only_the_target_terminal(self, running_paths) -> None:
        running_paths.return_value = (
            Path("C:/Program Files/XM Trading MT5/terminal64.exe"),
        )
        self.assertFalse(target_terminal_is_running(self.scenario))

        running_paths.return_value = (self.scenario.terminal_path,)
        self.assertTrue(target_terminal_is_running(self.scenario))

    @patch("mt5_ea_validator.mt5.subprocess.run")
    def test_no_running_terminal_is_a_successful_detection(self, process_run) -> None:
        process_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        self.assertEqual(running_terminal_paths(), ())

    def test_executor_stages_report_in_mt5_data_and_moves_all_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = replace(
                self.scenario,
                terminal_path=root / "terminal64.exe",
                data_directory=root / "terminal-data",
            )
            output = root / "results" / "run-1" / "deposit_3000"

            def fake_run(command, **kwargs):
                staging = (
                    scenario.data_directory
                    / "MQL5"
                    / "Files"
                    / "mt5_ea_validator"
                    / "run-1"
                    / "deposit_3000"
                )
                (staging / "QQ_WF1_CAPITAL_3000.htm").write_text(
                    "report", encoding="utf-8"
                )
                (staging / "QQ_WF1_CAPITAL_3000.png").write_bytes(b"png")
                return subprocess.CompletedProcess(command, 0)

            execution = MT5Executor(scenario, process_runner=fake_run).execute(
                3000, output
            )

            self.assertEqual(execution.report_path, output / "QQ_WF1_CAPITAL_3000.htm")
            self.assertTrue(execution.report_path.is_file())
            self.assertTrue((output / "QQ_WF1_CAPITAL_3000.png").is_file())
            ini = (output / "tester.ini").read_text(encoding="utf-16")
            self.assertIn(
                "Report=MQL5\\Files\\mt5_ea_validator\\run-1\\deposit_3000"
                "\\QQ_WF1_CAPITAL_3000.htm",
                ini,
            )


if __name__ == "__main__":
    unittest.main()
