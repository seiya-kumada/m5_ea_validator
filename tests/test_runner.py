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
from mt5_ea_validator.runner import CampaignError, run_campaign


PROJECT_ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))


def _report(net_profit: float, trades: int) -> str:
    return f"""<html><body><table>
    <tr><td>Total Net Profit:</td><td>{net_profit:.2f}</td></tr>
    <tr><td>Profit Factor:</td><td>1.26</td></tr>
    <tr><td>Recovery Factor:</td><td>0.54</td></tr>
    <tr><td>Sharpe Ratio:</td><td>0.31</td></tr>
    <tr><td>Equity Drawdown Relative:</td><td>3.35% (100.50)</td></tr>
    <tr><td>Total Trades:</td><td>{trades}</td></tr>
    </table></body></html>"""


class FakeExecutor:
    def __init__(self, net_profit: float = 53.98, trades: int = 147) -> None:
        self.net_profit = net_profit
        self.trades = trades
        self.calls: list[int] = []
        self.prepared = False

    def prepare(self) -> None:
        self.prepared = True

    def execute(self, deposit: int, output_directory: Path) -> TestExecution:
        self.calls.append(deposit)
        output_directory.mkdir(parents=True, exist_ok=False)
        ini = output_directory / "tester.ini"
        report = output_directory / f"QQ_WF1_CAPITAL_{deposit}.htm"
        ini.write_text(f"Deposit={deposit}\n", encoding="utf-16")
        report.write_text(_report(self.net_profit, self.trades), encoding="utf-16")
        return TestExecution(deposit, ini, report, 0)


class RunnerTests(unittest.TestCase):
    def _scenario(
        self,
        temporary: str,
        config_name: str = "qq_wf1_capital.json",
    ):
        scenario = load_scenario(
            PROJECT_ROOT / "config" / config_name
        )
        root = Path(temporary)
        data_directory = root / "terminal-data"
        expert = data_directory / "MQL5" / "Experts" / "Market" / "QQ.ex5"
        expert.parent.mkdir(parents=True)
        expert.write_bytes(b"test ea")
        set_source = root / scenario.staged_set_name
        set_source.write_bytes(scenario.set_source.read_bytes())
        return replace(
            scenario,
            data_directory=data_directory,
            expert_binary=expert,
            set_source=set_source,
            output_root=root / "data" / "results",
            benchmark=replace(
                scenario.benchmark,
                deal_count=0,
                deal_sequence_sha256=hashlib.sha256(b"\n").hexdigest(),
                sharpe_ratio=(
                    0.31
                    if scenario.benchmark.sharpe_ratio is not None
                    else None
                ),
            ),
        )

    def test_runs_second_deposit_only_after_benchmark_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = self._scenario(temporary)
            executor = FakeExecutor(net_profit=53.91)
            run_directory = run_campaign(
                scenario,
                executor=executor,
                now_factory=lambda: datetime(2026, 8, 20, 12, 0, tzinfo=JST),
            )
            manifest = json.loads(
                (run_directory / "run_manifest.json").read_text(encoding="utf-8")
            )

        self.assertTrue(executor.prepared)
        self.assertEqual(executor.calls, [3000, 1500, 1000])
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["deposits_completed"], [3000, 1500, 1000])
        benchmark_result = manifest["results"][0]
        self.assertTrue(benchmark_result["benchmark"]["passed"])
        self.assertEqual(
            benchmark_result["accounting_note"]["classification"],
            "minor accounting difference",
        )
        self.assertEqual(benchmark_result["accounting_note"]["difference"], -0.07)

    def test_stops_after_benchmark_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = self._scenario(temporary)
            executor = FakeExecutor(net_profit=50.00)
            with self.assertRaises(CampaignError) as captured:
                run_campaign(
                    scenario,
                    executor=executor,
                    now_factory=lambda: datetime(2026, 8, 20, 12, 0, tzinfo=JST),
                )
            manifest = json.loads(
                (captured.exception.run_directory / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(executor.calls, [3000])
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["deposits_completed"], [3000])

    def test_wf2_uses_optional_benchmark_and_runs_1000_after_3000(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = self._scenario(temporary, "qq_wf2_capital.json")
            executor = FakeExecutor(net_profit=213.27, trades=199)
            run_directory = run_campaign(
                scenario,
                executor=executor,
                now_factory=lambda: datetime(2026, 8, 20, 12, 0, tzinfo=JST),
            )
            manifest = json.loads(
                (run_directory / "run_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(executor.calls, [3000, 1000])
        self.assertEqual(manifest["wf"], "WF2")
        self.assertEqual(manifest["status"], "success")
        checks = manifest["results"][0]["benchmark"]["checks"]
        self.assertEqual(
            set(checks),
            {"net_profit", "trades", "deal_count", "deal_sequence_sha256"},
        )

    def test_non_qq_scenario_is_recorded_and_runs_both_deposits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = self._scenario(
                temporary, "smart_gold_hunter_wf1_capital.json"
            )
            executor = FakeExecutor(net_profit=31.08, trades=91)
            run_directory = run_campaign(
                scenario,
                executor=executor,
                now_factory=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=JST),
            )
            manifest = json.loads(
                (run_directory / "run_manifest.json").read_text(encoding="utf-8")
            )

        self.assertEqual(executor.calls, [3000, 1000])
        self.assertEqual(manifest["ea_id"], "smart_gold_hunter")
        self.assertEqual(manifest["wf"], "WF1")
        self.assertEqual(manifest["status"], "success")


if __name__ == "__main__":
    unittest.main()
