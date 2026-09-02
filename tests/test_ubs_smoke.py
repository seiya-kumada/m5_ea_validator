from __future__ import annotations

import unittest
import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from mt5_ea_validator.ubs_smoke import (
    UBSSmokeError,
    build_ubs_effective_sets,
    build_ubs_faithful_augmented_sets,
    compare_default_to_smoke,
    compare_ubs_inputs,
    load_ubs_smoke_settings,
    validate_ubs_smoke_settings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UBSSmokeTests(unittest.TestCase):
    def test_default_control_matches_inputs_absent_from_old_set(self) -> None:
        settings = load_ubs_smoke_settings(
            PROJECT_ROOT / "config" / "ubs_gold_smoke.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            set_directory = root / "sets"
            set_directory.mkdir()
            source = set_directory / "old.set"
            source.write_text(
                "ForceSymbol=XAUUSD\n"
                "UseAutoLoader=false\n"
                "Run_Strategy=1\n",
                encoding="utf-16",
            )
            report_directory = root / "result"
            report_directory.mkdir()
            report_path = report_directory / "report.htm"
            report_path.write_text("report", encoding="utf-8")
            (report_directory / "applied_inputs.json").write_text(
                json.dumps(
                    {
                        "ForceSymbol": "XAUUSD",
                        "UseAutoLoader": "false",
                        "Run_Strategy": "1",
                        "EntryModel": "1",
                    }
                ),
                encoding="utf-8",
            )
            smoke_run = root / "smoke"
            smoke_run.mkdir()
            (smoke_run / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "smoke-1",
                        "status": "success",
                        "results": [
                            {
                                "strategy_id": "old",
                                "file_name": "old.set",
                                "report_file": str(report_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            comparison = compare_default_to_smoke(
                {"EntryModel": "1.0"},
                replace(settings, set_directory=set_directory),
                smoke_run,
            )

        self.assertTrue(comparison["passed"])
        self.assertEqual(
            comparison["strategies"][0]["filled_inputs"], ["EntryModel"]
        )

    def test_project_config_has_seven_h1_real_tick_strategies(self) -> None:
        settings = load_ubs_smoke_settings(
            PROJECT_ROOT / "config" / "ubs_gold_smoke.json"
        )

        self.assertEqual(len(settings.strategies), 7)
        self.assertEqual(settings.symbol, "XAUUSD")
        self.assertEqual(settings.period, "H1")
        self.assertEqual(settings.model, 4)
        self.assertEqual(settings.execution_mode, 0)
        self.assertEqual(settings.deposit, 3000)
        self.assertEqual(settings.from_date, "2026.08.01")
        self.assertEqual(settings.to_date, "2026.08.31")

    def test_rejects_non_h1_smoke_settings(self) -> None:
        settings = load_ubs_smoke_settings(
            PROJECT_ROOT / "config" / "ubs_gold_smoke.json"
        )

        with self.assertRaisesRegex(UBSSmokeError, "XAUUSD/H1"):
            validate_ubs_smoke_settings(replace(settings, period="M15"))

    def test_compatibility_accepts_numeric_formatting_differences(self) -> None:
        compatibility = compare_ubs_inputs(
            {
                "ForceSymbol": "XAUUSD",
                "UseAutoLoader": "false",
                "Run_Strategy": "1",
                "Manual_RiskPerTrade": "1.0",
            },
            {
                "ForceSymbol": "XAUUSD",
                "UseAutoLoader": "false",
                "Run_Strategy": "1.0",
                "Manual_RiskPerTrade": "1",
                "NewV75Input": "true",
            },
        )

        self.assertTrue(compatibility["passed"])
        self.assertEqual(compatibility["common_value_mismatches"], {})
        self.assertEqual(
            compatibility["report_inputs_not_present_in_set"], ["NewV75Input"]
        )

    def test_compatibility_rejects_missing_required_input(self) -> None:
        compatibility = compare_ubs_inputs(
            {
                "ForceSymbol": "XAUUSD",
                "UseAutoLoader": "false",
                "Run_Strategy": "1",
            },
            {"ForceSymbol": "XAUUSD", "UseAutoLoader": "false"},
        )

        self.assertFalse(compatibility["passed"])
        self.assertFalse(
            compatibility["required_checks"]["Run_Strategy"]["passed"]
        )

    def test_exact_compatibility_rejects_any_extra_report_input(self) -> None:
        set_values = {
            "ForceSymbol": "XAUUSD",
            "UseAutoLoader": "false",
            "Run_Strategy": "1",
        }

        compatibility = compare_ubs_inputs(
            set_values,
            {**set_values, "EntryModel": "1"},
            require_exact=True,
        )

        self.assertFalse(compatibility["passed"])
        self.assertFalse(compatibility["exact_match"])
        self.assertEqual(compatibility["validation_mode"], "exact")

    def test_report_covered_mode_allows_legacy_set_only_inputs(self) -> None:
        compatibility = compare_ubs_inputs(
            {
                "ForceSymbol": "XAUUSD",
                "UseAutoLoader": "false",
                "Run_Strategy": "1",
                "LegacyHiddenInput": "999",
                "EntryModel": "1",
            },
            {
                "ForceSymbol": "XAUUSD",
                "UseAutoLoader": "false",
                "Run_Strategy": "1",
                "EntryModel": "1.0",
            },
            require_report_covered=True,
        )

        self.assertTrue(compatibility["passed"])
        self.assertTrue(compatibility["report_covered_match"])
        self.assertFalse(compatibility["exact_match"])
        self.assertEqual(compatibility["validation_mode"], "report_covered")

    def test_builds_seven_fully_specified_sets_without_changing_originals(self) -> None:
        base = load_ubs_smoke_settings(
            PROJECT_ROOT / "config" / "ubs_gold_smoke.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_directory = root / "original"
            original_directory.mkdir()
            output_root = root / "smoke_output"
            smoke_run = root / "smoke_run"
            smoke_run.mkdir()
            applied = {
                "ForceSymbol": "XAUUSD",
                "UseAutoLoader": "false",
                "Run_Strategy": "1",
                **{f"Param{index:03d}": str(index) for index in range(216)},
            }
            results = []
            original_bytes = {}
            for strategy in base.strategies:
                source = original_directory / strategy.file_name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(
                    "ForceSymbol=XAUUSD\nUseAutoLoader=false\nRun_Strategy=1\n",
                    encoding="utf-8",
                )
                original_bytes[strategy.strategy_id] = source.read_bytes()
                report_directory = smoke_run / strategy.strategy_id
                report_directory.mkdir()
                report_path = report_directory / "report.htm"
                report_path.write_text("report", encoding="utf-8")
                (report_directory / "applied_inputs.json").write_text(
                    json.dumps(applied), encoding="utf-8"
                )
                results.append(
                    {
                        "strategy_id": strategy.strategy_id,
                        "file_name": strategy.file_name,
                        "set_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "report_file": str(report_path),
                    }
                )
            (smoke_run / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "smoke-219",
                        "status": "success",
                        "ea_sha256": "ea-hash",
                        "results": results,
                    }
                ),
                encoding="utf-8",
            )
            control_run = root / "control_run"
            control_run.mkdir()
            (control_run / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "control-1",
                        "status": "success",
                        "comparison": {
                            "passed": True,
                            "smoke_run_id": "smoke-219",
                        },
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(
                base,
                set_directory=original_directory,
                output_root=output_root,
            )

            manifest_path = build_ubs_effective_sets(
                settings, smoke_run, control_run
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            faithful_manifest_path = build_ubs_faithful_augmented_sets(
                settings, smoke_run, control_run
            )
            faithful_manifest = json.loads(
                faithful_manifest_path.read_text(encoding="utf-8")
            )

            self.assertEqual(len(manifest["sets"]), 7)
            self.assertTrue(all(item["input_count"] == 219 for item in manifest["sets"]))
            self.assertTrue(all(item["local_reread_exact"] for item in manifest["sets"]))
            self.assertEqual(len(faithful_manifest["sets"]), 7)
            self.assertTrue(
                all(
                    item["original_input_count"] == 3
                    and item["appended_v75_input_count"] == 216
                    and item["derived_input_count"] == 219
                    for item in faithful_manifest["sets"]
                )
            )
            for strategy in base.strategies:
                self.assertEqual(
                    (original_directory / strategy.file_name).read_bytes(),
                    original_bytes[strategy.strategy_id],
                )


if __name__ == "__main__":
    unittest.main()
