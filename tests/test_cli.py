from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mt5_ea_validator.cli import main
from mt5_ea_validator.configuration import load_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_ubs_tick_audit_uses_saved_run(self) -> None:
        result_path = PROJECT_ROOT / "data" / "tick_data_quality_summary.json"
        with patch(
            "mt5_ea_validator.cli.audit_saved_ubs_run",
            return_value=result_path,
        ) as audit_run:
            exit_code = main(["audit-ubs-tick-data", "--run", "saved-run"])

        self.assertEqual(exit_code, 0)
        audit_run.assert_called_once_with(Path("saved-run"))

    def test_ubs_default_control_uses_existing_smoke_run(self) -> None:
        settings = object()
        result_path = PROJECT_ROOT / "data" / "ubs_default_test"
        with (
            patch(
                "mt5_ea_validator.cli.load_ubs_smoke_settings",
                return_value=settings,
            ),
            patch(
                "mt5_ea_validator.cli.run_ubs_default_control",
                return_value=result_path,
            ) as run_control,
        ):
            exit_code = main(
                [
                    "run-ubs-default-control",
                    "--config",
                    "smoke.json",
                    "--smoke-run",
                    "existing-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        run_control.assert_called_once_with(settings, Path("existing-run"))

    def test_ubs_smoke_loads_config_and_runs_suite(self) -> None:
        settings = object()
        result_path = PROJECT_ROOT / "data" / "ubs_smoke_test"
        with (
            patch(
                "mt5_ea_validator.cli.load_ubs_smoke_settings",
                return_value=settings,
            ) as load_settings,
            patch(
                "mt5_ea_validator.cli.run_ubs_smoke_suite",
                return_value=result_path,
            ) as run_suite,
        ):
            exit_code = main(["run-ubs-smoke", "--config", "smoke.json"])

        self.assertEqual(exit_code, 0)
        load_settings.assert_called_once_with(Path("smoke.json"))
        run_suite.assert_called_once()
        self.assertIs(run_suite.call_args.args[0], settings)
        self.assertIsNone(run_suite.call_args.kwargs["strategy_ids"])

    def test_ubs_smoke_can_select_one_strategy(self) -> None:
        settings = object()
        result_path = PROJECT_ROOT / "data" / "ubs_smoke_test"
        with (
            patch(
                "mt5_ea_validator.cli.load_ubs_smoke_settings",
                return_value=settings,
            ),
            patch(
                "mt5_ea_validator.cli.run_ubs_smoke_suite",
                return_value=result_path,
            ) as run_suite,
        ):
            exit_code = main(
                [
                    "run-ubs-smoke",
                    "--config",
                    "smoke.json",
                    "--strategy-id",
                    "xau_sr_scalp_h1",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run_suite.call_args.kwargs["strategy_ids"], ("xau_sr_scalp_h1",)
        )

    def test_ubs_inventory_passes_reproducibility_inputs(self) -> None:
        result_path = PROJECT_ROOT / "data" / "ubs_inventory_test"
        with patch(
            "mt5_ea_validator.cli.create_ubs_inventory", return_value=result_path
        ) as create_inventory:
            exit_code = main(
                [
                    "ubs-inventory",
                    "--set-directory",
                    "sets",
                    "--ea-path",
                    "ubs.ex5",
                    "--ea-version-label",
                    "7.5 demo",
                    "--symbol",
                    "XAUUSD",
                    "--output-root",
                    "inventory",
                ]
            )

        self.assertEqual(exit_code, 0)
        create_inventory.assert_called_once_with(
            Path("sets"),
            Path("ubs.ex5"),
            Path("inventory"),
            ea_version_label="7.5 demo",
            symbol="XAUUSD",
        )

    def test_run_applies_target_deposit_override(self) -> None:
        scenario = load_scenario(PROJECT_ROOT / "config" / "qq_wf1_capital.json")
        with (
            patch("mt5_ea_validator.cli.load_scenario", return_value=scenario),
            patch("mt5_ea_validator.cli.validate_dedicated_set"),
            patch(
                "mt5_ea_validator.cli.run_campaign",
                return_value=PROJECT_ROOT / "data" / "test",
            ) as run_campaign,
        ):
            exit_code = main(
                [
                    "run",
                    "--config",
                    "ignored.json",
                    "--target-deposit",
                    "750",
                ]
            )

        self.assertEqual(exit_code, 0)
        selected = run_campaign.call_args.args[0]
        self.assertEqual(selected.deposits, (3000, 750))


if __name__ == "__main__":
    unittest.main()
