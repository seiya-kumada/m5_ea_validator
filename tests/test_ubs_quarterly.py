from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mt5_ea_validator.ubs_quarterly import (
    UBSQuarterlyError,
    run_ubs_quarterly_suite,
    validate_quarterly_prerequisites,
)
from mt5_ea_validator.ubs_smoke import load_ubs_smoke_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UBSQuarterlyTests(unittest.TestCase):
    def _inputs(self, root: Path, *, tick_status: str = "pass"):
        base = load_ubs_smoke_settings(
            PROJECT_ROOT / "config" / "ubs_gold_smoke.json"
        )
        ea = root / "ubs.ex5"
        ea.write_bytes(b"ea")
        source_smoke = root / "source_smoke"
        source_smoke.mkdir()
        (source_smoke / "tick_data_quality_summary.json").write_text(
            json.dumps(
                {
                    "status": tick_status,
                    "audited_result_count": 7,
                }
            ),
            encoding="utf-8",
        )
        set_directory = root / "faithful"
        set_directory.mkdir()
        entries = []
        for strategy in base.strategies:
            file_name = f"{strategy.strategy_id}.set"
            path = set_directory / file_name
            path.write_text(
                "ForceSymbol=XAUUSD\nUseAutoLoader=false\nRun_Strategy=1\n",
                encoding="utf-8",
            )
            entries.append(
                {
                    "strategy_id": strategy.strategy_id,
                    "derived_file_name": file_name,
                    "derived_set_sha256": _sha(path),
                }
            )
        faithful_manifest = set_directory / "faithful_augmented_sets_manifest.json"
        faithful_manifest.write_text(
            json.dumps(
                {
                    "status": "success",
                    "original_sets_modified": False,
                    "ea_sha256": _sha(ea),
                    "source_smoke_run": str(source_smoke),
                    "sets": entries,
                }
            ),
            encoding="utf-8",
        )
        validation_run = root / "validation"
        validation_run.mkdir()
        (validation_run / "run_manifest.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "faithful_sets_manifest": str(faithful_manifest.resolve()),
                    "source_reproduction_gate": {"passed": True},
                }
            ),
            encoding="utf-8",
        )
        settings = replace(
            base,
            expert_binary=ea,
            output_root=root / "smoke",
        )
        return settings, faithful_manifest, validation_run

    def test_prerequisites_reject_failed_tick_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings, faithful, validation = self._inputs(
                Path(temporary), tick_status="fail"
            )

            with self.assertRaisesRegex(UBSQuarterlyError, "tick-data"):
                validate_quarterly_prerequisites(settings, faithful, validation)

    def test_suite_runs_28_ordered_cases_and_writes_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._inputs(root)

            def fake_case_runner(
                _settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                output.mkdir(parents=True)
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "file_name": strategy.file_name,
                    "set_sha256": "set-hash",
                    "report_file": str(output / "report.htm"),
                    "ini_file": str(output / "tester.ini"),
                    "mt5_return_code": 0,
                    "metrics": {
                        "net_profit": 1.0,
                        "trades": 2,
                        "profit_factor": 1.1,
                        "recovery_factor": 0.2,
                        "sharpe_ratio": 0.3,
                        "equity_drawdown_percent": 0.4,
                    },
                    "deal_audit": {
                        "deal_count": 4,
                        "deal_sequence_sha256": "deal-hash",
                        "commission_total": -1.0,
                        "swap_total": 0.0,
                        "deal_profit_total": 2.0,
                    },
                    "compatibility": {"passed": True},
                    "tick_data_quality": None,
                    "log_snapshots": [],
                }

            run = run_ubs_quarterly_suite(
                settings,
                faithful,
                validation,
                case_runner=fake_case_runner,
                progress=lambda _message: None,
            )
            manifest = json.loads(
                (run / "suite_manifest.json").read_text(encoding="utf-8")
            )
            summary = json.loads(
                (run / "quarterly_summary.json").read_text(encoding="utf-8")
            )

            self.assertEqual(manifest["status"], "success")
            self.assertEqual(len(manifest["results"]), 28)
            self.assertEqual(summary["case_count"], 28)
            self.assertEqual(manifest["results"][0]["wf"], "WF1")
            self.assertEqual(manifest["results"][-1]["wf"], "WF4")
            self.assertTrue((run / "quarterly_summary.csv").is_file())


if __name__ == "__main__":
    unittest.main()
