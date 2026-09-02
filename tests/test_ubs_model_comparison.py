from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mt5_ea_validator.ubs_model_comparison import (
    MODEL_FROM_DATE,
    MODEL_TO_DATE,
    run_ubs_model_comparison_suite,
)
from mt5_ea_validator.ubs_smoke import load_ubs_smoke_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UBSModelComparisonTests(unittest.TestCase):
    def _inputs(self, root: Path):
        base = load_ubs_smoke_settings(
            PROJECT_ROOT / "config" / "ubs_gold_smoke.json"
        )
        ea = root / "ubs.ex5"
        ea.write_bytes(b"ea")
        source_smoke = root / "source_smoke"
        source_smoke.mkdir()
        (source_smoke / "tick_data_quality_summary.json").write_text(
            json.dumps({"status": "pass", "audited_result_count": 7}),
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
        settings = replace(base, expert_binary=ea, output_root=root / "smoke")
        return settings, faithful_manifest, validation_run

    def test_suite_runs_ohlc_before_real_ticks_and_writes_pair_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._inputs(root)
            observed: list[tuple[int, str, str, str]] = []

            def fake_case_runner(
                case_settings,
                strategy,
                scenario,
                output,
                **_kwargs,
            ):
                output.mkdir(parents=True)
                observed.append(
                    (
                        scenario.model,
                        strategy.strategy_id,
                        scenario.from_date,
                        scenario.to_date,
                    )
                )
                model_offset = 10.0 if scenario.model == 4 else 0.0
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "file_name": strategy.file_name,
                    "set_sha256": "set-hash",
                    "report_file": str(output / "report.htm"),
                    "ini_file": str(output / "tester.ini"),
                    "mt5_return_code": 0,
                    "metrics": {
                        "net_profit": 100.0 + model_offset,
                        "trades": 2 + int(model_offset),
                        "profit_factor": 1.1 + model_offset,
                        "recovery_factor": 0.2 + model_offset,
                        "sharpe_ratio": 0.3 + model_offset,
                        "equity_drawdown_percent": 0.4 + model_offset,
                    },
                    "deal_audit": {
                        "deal_count": 4 + int(model_offset),
                        "deal_sequence_sha256": f"deal-{scenario.model}",
                        "commission_total": -1.0 - model_offset,
                        "swap_total": model_offset,
                        "deal_profit_total": 101.0 + model_offset,
                    },
                    "compatibility": {"passed": True},
                    "tick_data_quality": (
                        {"passed": True} if case_settings.model == 4 else None
                    ),
                    "log_snapshots": [],
                }

            run = run_ubs_model_comparison_suite(
                settings,
                faithful,
                validation,
                case_runner=fake_case_runner,
                progress=lambda _message: None,
            )
            manifest = json.loads(
                (run / "suite_manifest.json").read_text(encoding="utf-8")
            )
            pairs = json.loads(
                (run / "model_comparison_pairs.json").read_text(encoding="utf-8")
            )

            self.assertEqual(manifest["status"], "success")
            self.assertEqual(len(manifest["results"]), 14)
            self.assertEqual([item[0] for item in observed[:7]], [1] * 7)
            self.assertEqual([item[0] for item in observed[7:]], [4] * 7)
            self.assertTrue(
                all(
                    item[2:] == (MODEL_FROM_DATE, MODEL_TO_DATE)
                    for item in observed
                )
            )
            self.assertEqual(pairs["strategy_count"], 7)
            self.assertEqual(
                pairs["results"][0]["net_profit_delta_real_minus_ohlc"],
                10.0,
            )
            self.assertFalse(pairs["results"][0]["deal_sequence_same"])
            self.assertTrue((run / "model_comparison_summary.csv").is_file())
            self.assertTrue((run / "model_comparison_pairs.csv").is_file())


if __name__ == "__main__":
    unittest.main()
