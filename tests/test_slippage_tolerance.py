from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mt5_ea_validator.configuration import load_scenario
from mt5_ea_validator.mt5 import TestExecution
from mt5_ea_validator.report import DealAudit, ReportMetrics
from mt5_ea_validator.slippage_tolerance import (
    SlippageToleranceCampaignError,
    SlippageToleranceError,
    evaluate_baseline_gate,
    execution_delay_label,
    run_slippage_tolerance_suite,
    validate_slippage_points,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
EMPTY_DEAL_HASH = hashlib.sha256(b"\n").hexdigest()


def _report(net_profit: float) -> str:
    return f"""<html><body><table>
    <tr><td>Total Net Profit:</td><td>{net_profit:.2f}</td></tr>
    <tr><td>Profit Factor:</td><td>1.50</td></tr>
    <tr><td>Recovery Factor:</td><td>2.00</td></tr>
    <tr><td>Sharpe Ratio:</td><td>3.00</td></tr>
    <tr><td>Equity Drawdown Relative:</td><td>4.00% (120.00)</td></tr>
    <tr><td>Total Trades:</td><td>2</td></tr>
    </table></body></html>"""


class FakeExecutor:
    def __init__(
        self,
        scenario,
        points: int,
        calls: list[tuple[str, int]],
        *,
        fail_baseline: bool = False,
    ) -> None:
        self.scenario = scenario
        self.points = points
        self.calls = calls
        self.fail_baseline = fail_baseline

    def prepare(self) -> None:
        return None

    def execute(self, deposit: int, output_directory: Path) -> TestExecution:
        self.calls.append((self.scenario.wf, self.points))
        output_directory.mkdir(parents=True, exist_ok=False)
        ini = output_directory / "tester.ini"
        report = output_directory / "report.htm"
        ini.write_text(f"Deposit={deposit}\n", encoding="utf-16")
        profit = 10.0 + (0 if self.points == 100 else self.points / 100)
        if self.fail_baseline and self.points == 100:
            profit = 9.0
        report.write_text(_report(profit), encoding="utf-16")
        return TestExecution(deposit, ini, report, 0)


class SlippageToleranceTests(unittest.TestCase):
    def test_accepts_reconciled_swap_accounting_drift(self) -> None:
        benchmark = load_scenario(
            PROJECT_ROOT / "config" / "qq_wf1_capital.json"
        ).benchmark
        metrics = ReportMetrics(52.96, 147, 1.25, 0.52, 2.93, 3.36)
        deals = DealAudit(
            deal_count=294,
            deal_sequence_sha256=benchmark.deal_sequence_sha256,
            commission_total=-105.84,
            swap_total=-21.11,
            deal_profit_total=179.91,
        )

        gate = evaluate_baseline_gate(metrics, deals, benchmark)

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["status"], "PASS_WITH_ACCOUNTING_DRIFT")
        drift = gate["accounting_drift"]
        self.assertEqual(drift["difference"]["swap_total"], -0.95)
        self.assertEqual(drift["difference"]["net_profit"], -0.95)

    def _scenarios(self, root: Path):
        data_directory = root / "terminal-data"
        expert = data_directory / "MQL5" / "Experts" / "Market" / "QQ.ex5"
        expert.parent.mkdir(parents=True)
        expert.write_bytes(b"test ea")
        scenarios = []
        for number in range(1, 5):
            scenario = load_scenario(
                PROJECT_ROOT / "config" / f"qq_wf{number}_capital.json"
            )
            set_source = root / "source_sets" / scenario.set_source.name
            set_source.parent.mkdir(parents=True, exist_ok=True)
            set_source.write_bytes(scenario.set_source.read_bytes())
            benchmark = replace(
                scenario.benchmark,
                net_profit=10.0,
                trades=2,
                deal_count=0,
                deal_sequence_sha256=EMPTY_DEAL_HASH,
                profit_factor=(
                    1.5 if scenario.benchmark.profit_factor is not None else None
                ),
                recovery_factor=(
                    2.0 if scenario.benchmark.recovery_factor is not None else None
                ),
                sharpe_ratio=(
                    3.0 if scenario.benchmark.sharpe_ratio is not None else None
                ),
                equity_drawdown_percent=(
                    4.0
                    if scenario.benchmark.equity_drawdown_percent is not None
                    else None
                ),
            )
            scenarios.append(
                replace(
                    scenario,
                    project_root=root,
                    data_directory=data_directory,
                    expert_binary=expert,
                    set_source=set_source,
                    benchmark=benchmark,
                )
            )
        return tuple(scenarios)

    def test_orders_baseline_first_and_records_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenarios = self._scenarios(root)
            calls: list[tuple[str, int]] = []
            run_directory = run_slippage_tolerance_suite(
                scenarios,
                slippage_points=(0, 2, 5, 10, 100),
                executor_factory=lambda scenario, points: FakeExecutor(
                    scenario, points, calls
                ),
                now_factory=lambda: datetime(2026, 8, 27, 12, 0, tzinfo=JST),
            )
            manifest = json.loads(
                (run_directory / "suite_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(calls[:4], [(f"WF{n}", 100) for n in range(1, 5)])
        self.assertEqual(len(calls), 20)
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(len(manifest["results"]), 20)
        point_two = next(
            result
            for result in manifest["results"]
            if result["wf"] == "WF1" and result["slippage_points"] == 2
        )
        self.assertEqual(point_two["metric_deltas_from_100"]["net_profit"], 0.02)
        self.assertTrue(point_two["deal_sequence_matches_100"])

    def test_stops_before_nonbaseline_when_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenarios = self._scenarios(root)
            calls: list[tuple[str, int]] = []
            with self.assertRaises(SlippageToleranceCampaignError):
                run_slippage_tolerance_suite(
                    scenarios,
                    executor_factory=lambda scenario, points: FakeExecutor(
                        scenario, points, calls, fail_baseline=True
                    ),
                )

        self.assertEqual(calls, [("WF1", 100)])

    def test_rejects_non_qq_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenarios = list(self._scenarios(Path(temporary)))
            scenarios[0] = replace(scenarios[0], ea_id="wave_rider")
            with self.assertRaisesRegex(SlippageToleranceError, "Quantum Queen"):
                run_slippage_tolerance_suite(scenarios)

    def test_requires_and_orders_baseline(self) -> None:
        self.assertEqual(validate_slippage_points((10, 100, 0)), (100, 0, 10))
        with self.assertRaisesRegex(SlippageToleranceError, "baseline"):
            validate_slippage_points((0, 2, 5, 10))

    def test_applies_fixed_execution_delay_to_derived_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenarios = self._scenarios(Path(temporary))
            calls: list[tuple[str, int]] = []
            observed_modes: list[int] = []

            def factory(scenario, points):
                observed_modes.append(scenario.execution_mode)
                return FakeExecutor(scenario, points, calls)

            run_directory = run_slippage_tolerance_suite(
                scenarios,
                slippage_points=(100,),
                execution_mode=188,
                executor_factory=factory,
            )
            manifest = json.loads(
                (run_directory / "suite_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(observed_modes, [188, 188, 188, 188])
        self.assertEqual(manifest["execution_delay"], "Fixed Delay 188 ms")
        self.assertTrue(
            all(
                result["baseline_gate"]["status"] == "DELAY_MODE_REFERENCE"
                for result in manifest["results"]
            )
        )

    def test_validates_execution_delay(self) -> None:
        self.assertEqual(execution_delay_label(-1), "Random Delay")
        with self.assertRaisesRegex(SlippageToleranceError, "ExecutionMode"):
            execution_delay_label(600_001)


if __name__ == "__main__":
    unittest.main()
