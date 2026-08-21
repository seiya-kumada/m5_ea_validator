from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mt5_ea_validator.cli import main
from mt5_ea_validator.configuration import load_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
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
