from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.ubs_risk_sensitivity import (
    LOT_INPUTS,
    PILOT_VARIATIONS,
    UBSRiskSensitivityError,
    audit_saved_ubs_entry_volumes,
    run_ubs_risk_sensitivity_pilot,
)
from mt5_ea_validator.ubs_smoke import load_ubs_smoke_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


STRATEGIES = (
    "xau_sr_scalp_h1",
    "xau_h1_c5",
    "daily_l",
    "e",
    "goldtradepro_a",
    "mt5_longterm_e",
    "mt5_longterm_j",
)


def _report(volume: str) -> str:
    return f"""<html><table>
    <tr><td>2025.07.01 01:02:03</td><td>1</td><td>XAUUSD</td>
    <td>buy</td><td>in</td><td>{volume}</td><td>3300.10</td><td>1</td>
    <td>-0.72</td><td>0.00</td><td>0.00</td><td>2999.28</td><td>entry</td></tr>
    <tr><td>2025.07.01 02:03:04</td><td>2</td><td>XAUUSD</td>
    <td>sell</td><td>out</td><td>{volume}</td><td>3301.20</td><td>2</td>
    <td>0.00</td><td>0.00</td><td>1.00</td><td>3000.28</td><td>exit</td></tr>
    </table></html>"""


class UBSRiskSensitivityTests(unittest.TestCase):
    def _model_run(self, root: Path, *, result_count: int = 7) -> Path:
        run = root / "model"
        run.mkdir()
        results = []
        for index, strategy in enumerate(STRATEGIES[:result_count], start=1):
            case = run / f"case_{index}"
            case.mkdir()
            report = case / "report.htm"
            report.write_text(_report(f"0.0{index}"), encoding="utf-8")
            applied = {name: "1" for name in LOT_INPUTS}
            applied.update(
                {
                    "StartLots": "0.1",
                    "LotPerBalance_step": "30",
                    "HistoricalMaxDD": str(index * 10),
                    "MaxTrades": "99",
                }
            )
            (case / "applied_inputs.json").write_text(
                json.dumps(applied), encoding="utf-8"
            )
            results.append(
                {
                    "status": "success",
                    "model": 4,
                    "strategy_id": strategy,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 10.0 * index,
                        "trades": index,
                        "equity_drawdown_percent": float(index),
                    },
                }
            )
        (run / "suite_manifest.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "from_date": "2025.07.01",
                    "to_date": "2026.06.30",
                    "deposit": 3000,
                    "results": results,
                }
            ),
            encoding="utf-8",
        )
        return run

    def test_audits_all_saved_real_tick_entry_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self._model_run(root)

            output = audit_saved_ubs_entry_volumes(run, root / "audit")

            payload = json.loads(
                (output / "entry_volume_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["strategy_count"], 7)
            self.assertEqual(payload["results"][0]["entry_deal_count"], 1)
            self.assertEqual(payload["results"][0]["first_entry_volume"], 0.01)
            self.assertEqual(payload["results"][6]["maximum_entry_volume"], 0.07)
            self.assertTrue((output / "entry_volume_summary.csv").is_file())

    def test_rejects_incomplete_real_tick_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = self._model_run(root, result_count=6)

            with self.assertRaisesRegex(UBSRiskSensitivityError, "seven"):
                audit_saved_ubs_entry_volumes(run, root / "audit")

    def _pilot_inputs(self, root: Path):
        base = load_ubs_smoke_settings(PROJECT_ROOT / "config" / "ubs_gold_smoke.json")
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
                "\n".join(
                    [
                        "ForceSymbol=XAUUSD",
                        "UseAutoLoader=false",
                        "Run_Strategy=1",
                        "AdjustLotsizeToVariableValues=true",
                        "Risk=999",
                        "StartLots=0.1",
                        "Manual_RiskPerTrade=1",
                        "MaxRiskInDollar_input=100",
                        "LotPerBalance_step=500"
                        if strategy.strategy_id == "xau_sr_scalp_h1"
                        else "LotPerBalance_step=30",
                        "MaxRiskPerStrategy_Value=1",
                        "HistoricalMaxDD=76"
                        if strategy.strategy_id == "xau_sr_scalp_h1"
                        else "HistoricalMaxDD=60",
                        "MaxLots=99",
                        "MaxTrades=8",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            entries.append(
                {
                    "strategy_id": strategy.strategy_id,
                    "derived_file_name": file_name,
                    "derived_set_sha256": sha256_file(path),
                }
            )
        faithful = set_directory / "faithful_augmented_sets_manifest.json"
        faithful.write_text(
            json.dumps(
                {
                    "status": "success",
                    "original_sets_modified": False,
                    "ea_sha256": sha256_file(ea),
                    "source_smoke_run": str(source_smoke),
                    "sets": entries,
                }
            ),
            encoding="utf-8",
        )
        validation = root / "validation"
        validation.mkdir()
        baseline_results = []
        for index, strategy in enumerate(base.strategies, start=1):
            case = validation / f"case_{index}"
            case.mkdir()
            report = case / "report.htm"
            report.write_text(_report("0.01"), encoding="utf-8")
            baseline_results.append(
                {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 10.0,
                        "trades": 1,
                        "equity_drawdown_percent": 1.0,
                    },
                    "deal_audit": {"deal_sequence_sha256": "baseline"},
                }
            )
        validation_manifest = {
            "status": "success",
            "faithful_sets_manifest": str(faithful.resolve()),
            "source_reproduction_gate": {"passed": True},
            "conditions": {
                "symbol": base.symbol,
                "period": base.period,
                "from_date": base.from_date,
                "to_date": base.to_date,
                "model": base.model,
                "execution_mode": base.execution_mode,
                "deposit": base.deposit,
                "currency": base.currency,
                "leverage": base.leverage,
            },
            "results": baseline_results,
        }
        (validation / "run_manifest.json").write_text(
            json.dumps(validation_manifest), encoding="utf-8"
        )
        settings = replace(base, expert_binary=ea, output_root=root / "smoke")
        return settings, faithful, validation

    def test_pilot_changes_only_one_input_and_writes_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._pilot_inputs(root)
            observed: list[tuple[str, str]] = []

            def fake_case_runner(
                _settings,
                strategy,
                scenario,
                output,
                **_kwargs,
            ):
                output.mkdir(parents=True)
                report = output / "report.htm"
                report.write_text(_report("0.02"), encoding="utf-8")
                observed.append((strategy.strategy_id, scenario.staged_set_name))
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 20.0,
                        "trades": 1,
                        "equity_drawdown_percent": 2.0,
                    },
                    "deal_audit": {"deal_sequence_sha256": "pilot"},
                }

            run = run_ubs_risk_sensitivity_pilot(
                settings,
                faithful,
                validation,
                case_runner=fake_case_runner,
            )

            manifest = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            summary = json.loads(
                (run / "pilot_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(len(observed), len(PILOT_VARIATIONS))
            self.assertTrue(
                all(
                    name.removesuffix(".set").rsplit("_", 1)[-1].isalnum()
                    and len(name.removesuffix(".set").rsplit("_", 1)[-1]) == 8
                    for _, name in observed
                )
            )
            self.assertEqual(summary["case_count"], len(PILOT_VARIATIONS))
            self.assertEqual(summary["results"][0]["mean_entry_volume_ratio"], 2.0)
            values_by_case = {
                spec["case_id"]: spec["updated_value"]
                for spec in manifest["pilot_variations"]
            }
            self.assertEqual(values_by_case["xau_sr_lpb_double"], "1000")
            self.assertEqual(values_by_case["gold_hmaxdd_double"], "120")
            for spec in manifest["pilot_variations"]:
                self.assertEqual(spec["changed_inputs"], [spec["parameter"]])
                self.assertTrue(Path(spec["derived_file"]).is_file())


if __name__ == "__main__":
    unittest.main()
