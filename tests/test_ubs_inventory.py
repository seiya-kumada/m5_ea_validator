from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mt5_ea_validator.ubs_inventory import (
    UBSInventoryError,
    build_ubs_inventory,
    create_ubs_inventory,
)


class UBSInventoryTests(unittest.TestCase):
    def _write_set(self, path: Path, *, symbol: str, lot_step: int = 30) -> None:
        path.write_text(
            "; test set\n"
            f"ForceSymbol={symbol}\n"
            "UseAutoLoader=false||false||0||true||N\n"
            "Run_Strategy=1\n"
            "ST1_Timeframe=16385\n"
            f"LotPerBalance_step={lot_step}\n",
            encoding="utf-16",
        )

    def test_build_filters_symbol_and_records_all_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            set_directory = root / "sets"
            set_directory.mkdir()
            self._write_set(set_directory / "gold.set", symbol="XAUUSD")
            self._write_set(set_directory / "nas.set", symbol="NAS100")
            ea = root / "Ultimate Breakout System.ex5"
            ea.write_bytes(b"demo-ea")

            inventory = build_ubs_inventory(
                set_directory,
                ea,
                ea_version_label="7.5 demo",
                generated_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

            self.assertEqual(inventory["set_count"], 1)
            self.assertEqual(inventory["ea"]["version_label"], "7.5 demo")
            record = inventory["sets"][0]
            self.assertEqual(record["file_name"], "gold.set")
            self.assertEqual(record["all_settings"]["ForceSymbol"], "XAUUSD")
            self.assertEqual(record["major_settings"]["LotPerBalance_step"], "30")
            self.assertIsNone(record["tester_period"])
            self.assertEqual(record["tester_period_status"], "unresolved")

    def test_create_writes_unique_json_and_csv_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            set_directory = root / "sets"
            set_directory.mkdir()
            self._write_set(set_directory / "gold.set", symbol="XAUUSD")
            ea = root / "ubs.ex5"
            ea.write_bytes(b"demo-ea")

            run_directory = create_ubs_inventory(
                set_directory,
                ea,
                root / "output",
                ea_version_label="7.5 demo",
                now=lambda: datetime(2026, 9, 2, 1, 2, 3, tzinfo=timezone.utc),
            )

            manifest = json.loads(
                (run_directory / "ubs_gold_set_inventory.json").read_text(
                    encoding="utf-8"
                )
            )
            with (run_directory / "ubs_gold_major_settings.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(manifest["set_count"], 1)
            self.assertEqual(rows[0]["ForceSymbol"], "XAUUSD")
            self.assertEqual(rows[0]["file_name"], "gold.set")

            with self.assertRaises(FileExistsError):
                create_ubs_inventory(
                    set_directory,
                    ea,
                    root / "output",
                    ea_version_label="7.5 demo",
                    now=lambda: datetime(
                        2026, 9, 2, 1, 2, 3, tzinfo=timezone.utc
                    ),
                )

    def test_duplicate_parameter_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            set_directory = root / "sets"
            set_directory.mkdir()
            (set_directory / "duplicate.set").write_text(
                "ForceSymbol=XAUUSD\nForceSymbol=XAUUSD\n", encoding="utf-16"
            )
            ea = root / "ubs.ex5"
            ea.write_bytes(b"demo-ea")

            with self.assertRaisesRegex(UBSInventoryError, "重複パラメータ"):
                build_ubs_inventory(
                    set_directory,
                    ea,
                    ea_version_label="7.5 demo",
                )

    def test_missing_gold_sets_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            set_directory = root / "sets"
            set_directory.mkdir()
            self._write_set(set_directory / "nas.set", symbol="NAS100")
            ea = root / "ubs.ex5"
            ea.write_bytes(b"demo-ea")

            with self.assertRaisesRegex(UBSInventoryError, "ForceSymbol=XAUUSD"):
                build_ubs_inventory(
                    set_directory,
                    ea,
                    ea_version_label="7.5 demo",
                )


if __name__ == "__main__":
    unittest.main()
