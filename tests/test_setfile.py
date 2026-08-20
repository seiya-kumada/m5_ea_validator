from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mt5_ea_validator.setfile import (
    SetFileError,
    parse_set_values,
    read_set_text,
    stage_dedicated_set,
    validate_qq_wf_set,
    validate_qq_wf1_set,
)
from mt5_ea_validator.configuration import load_scenario


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SET_PATH = PROJECT_ROOT / "config" / "sets" / "QQ_WF1.set"


class SetFileTests(unittest.TestCase):
    def test_dedicated_set_has_required_values(self) -> None:
        values = validate_qq_wf1_set(SET_PATH)

        self.assertEqual(values["InpLotsFixed"], "0.01")
        self.assertEqual(values["InpOrdersMax"], "10")
        self.assertEqual(values["InpUseNfpFridayFilter"], "true")
        self.assertEqual(values["InpTradingFridayNight"], "false")
        self.assertEqual(values["InpDDMode"], "0")
        self.assertEqual(values["InpDDValue"], "0.0")

    def test_each_wf_set_matches_its_scenario_requirements(self) -> None:
        for wf in ("WF2", "WF3", "WF4"):
            with self.subTest(wf=wf):
                scenario = load_scenario(
                    PROJECT_ROOT / "config" / f"qq_{wf.lower()}_capital.json"
                )
                values = validate_qq_wf_set(
                    scenario.set_source,
                    scenario.required_set_values,
                    label=f"QQ/{wf}",
                )
                self.assertEqual(values["InpLotsFixed"], "0.01")
                self.assertEqual(values["InpOrdersMax"], "10")
                self.assertEqual(values["InpS07Strategy"], "1")

    def test_staging_writes_utf16_and_reuses_identical_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "QQ_WF1.set"
            stage_dedicated_set(SET_PATH, destination)
            first = destination.read_bytes()
            stage_dedicated_set(SET_PATH, destination)

            self.assertTrue(first.startswith(b"\xff\xfe"))
            self.assertEqual(
                parse_set_values(read_set_text(destination)),
                parse_set_values(read_set_text(SET_PATH)),
            )

    def test_staging_refuses_different_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "QQ_WF1.set"
            destination.write_text("InpOrdersMax=99\n", encoding="utf-8")

            with self.assertRaisesRegex(SetFileError, "上書きしません"):
                stage_dedicated_set(SET_PATH, destination)


if __name__ == "__main__":
    unittest.main()
