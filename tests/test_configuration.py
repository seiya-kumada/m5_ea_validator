from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mt5_ea_validator.configuration import (
    ConfigurationError,
    load_scenario,
    select_target_deposit,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_project_scenario_has_fixed_qq_wf1_conditions(self) -> None:
        scenario = load_scenario(PROJECT_ROOT / "config" / "qq_wf1_capital.json")

        self.assertEqual(scenario.symbol, "XAUUSD")
        self.assertEqual(scenario.period, "H1")
        self.assertEqual(scenario.model, 4)
        self.assertEqual(scenario.execution_mode, 0)
        self.assertEqual(scenario.optimization, 0)
        self.assertEqual(scenario.forward_mode, 0)
        self.assertEqual(scenario.deposits, (3000, 1500, 1000))

    def test_wf2_to_wf4_scenarios_have_expected_periods_and_parameters(self) -> None:
        expected = {
            "WF2": ("2025.10.01", "2025.12.31", "true", "true", 213.27, 398),
            "WF3": ("2026.01.01", "2026.03.31", "false", "true", 397.15, 330),
            "WF4": ("2026.04.01", "2026.06.30", "false", "false", 362.09, 362),
        }
        for wf, values in expected.items():
            with self.subTest(wf=wf):
                scenario = load_scenario(
                    PROJECT_ROOT / "config" / f"qq_{wf.lower()}_capital.json"
                )
                from_date, to_date, nfp, friday, net_profit, deal_count = values
                self.assertEqual(scenario.wf, wf)
                self.assertEqual((scenario.from_date, scenario.to_date), (from_date, to_date))
                self.assertEqual(scenario.deposits, (3000, 1000))
                self.assertEqual(
                    scenario.required_set_values["InpUseNfpFridayFilter"], nfp
                )
                self.assertEqual(
                    scenario.required_set_values["InpTradingFridayNight"], friday
                )
                self.assertEqual(scenario.benchmark.net_profit, net_profit)
                self.assertEqual(scenario.benchmark.deal_count, deal_count)
                self.assertIsNone(scenario.benchmark.profit_factor)

    def test_smart_gold_hunter_scenarios_have_expected_modes(self) -> None:
        expected = {
            "WF1": ("1", 31.08, 91, 182),
            "WF2": ("1", -113.27, 90, 180),
            "WF3": ("2", 113.78, 33, 66),
            "WF4": ("2", 74.37, 27, 54),
        }
        for wf, values in expected.items():
            with self.subTest(wf=wf):
                scenario = load_scenario(
                    PROJECT_ROOT
                    / "config"
                    / f"smart_gold_hunter_{wf.lower()}_capital.json"
                )
                mode, net_profit, trades, deals = values
                self.assertEqual(scenario.ea_id, "smart_gold_hunter")
                self.assertEqual(scenario.artifact_prefix, "SGH")
                self.assertEqual(scenario.required_set_values["inp_Mode"], mode)
                self.assertEqual(scenario.required_set_values["inp_Lots"], "0.01")
                self.assertEqual(scenario.benchmark.net_profit, net_profit)
                self.assertEqual(scenario.benchmark.trades, trades)
                self.assertEqual(scenario.benchmark.deal_count, deals)

    def test_wave_rider_scenarios_have_expected_risk_profiles(self) -> None:
        expected = {
            "WF1": ("2", 587.37, 382, 764),
            "WF2": ("2", 603.59, 461, 922),
            "WF3": ("0", 632.62, 378, 756),
            "WF4": ("0", 491.77, 245, 490),
        }
        for wf, values in expected.items():
            with self.subTest(wf=wf):
                scenario = load_scenario(
                    PROJECT_ROOT
                    / "config"
                    / f"wave_rider_{wf.lower()}_capital.json"
                )
                risk_profile, net_profit, trades, deals = values
                self.assertEqual(scenario.ea_id, "wave_rider")
                self.assertEqual(scenario.artifact_prefix, "WR")
                self.assertEqual(
                    scenario.required_set_values["Inp_RiskProfile"], risk_profile
                )
                self.assertEqual(
                    scenario.required_set_values["Inp_FixedLotSize"], "0.01"
                )
                self.assertEqual(scenario.benchmark.net_profit, net_profit)
                self.assertEqual(scenario.benchmark.trades, trades)
                self.assertEqual(scenario.benchmark.deal_count, deals)

    def test_rejects_period_that_does_not_match_wf(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "config" / "qq_wf2_capital.json").read_text(
                encoding="utf-8"
            )
        )
        source["from_date"] = "2025.07.01"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            path = Path(temporary) / "scenario.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "WF2期間"):
                load_scenario(path)

    def test_rejects_wrong_first_deposit(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "config" / "qq_wf1_capital.json").read_text(
                encoding="utf-8"
            )
        )
        source["deposits"] = [1500, 3000]
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            path = Path(temporary) / "scenario.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "最初のDeposit"):
                load_scenario(path)

    def test_rejects_non_real_tick_model(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "config" / "qq_wf1_capital.json").read_text(
                encoding="utf-8"
            )
        )
        source["model"] = 0
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary:
            path = Path(temporary) / "scenario.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "固定条件"):
                load_scenario(path)


    def test_selects_only_benchmark_and_target_deposit(self) -> None:
        scenario = load_scenario(PROJECT_ROOT / "config" / "qq_wf1_capital.json")

        selected = select_target_deposit(scenario, 750)

        self.assertEqual(selected.deposits, (3000, 750))
        self.assertEqual(scenario.deposits, (3000, 1500, 1000))

    def test_rejects_invalid_target_deposit(self) -> None:
        scenario = load_scenario(PROJECT_ROOT / "config" / "qq_wf1_capital.json")
        for target in (0, -1, 3000):
            with self.subTest(target=target):
                with self.assertRaises(ConfigurationError):
                    select_target_deposit(scenario, target)


if __name__ == "__main__":
    unittest.main()
