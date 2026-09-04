from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.setfile import parse_set_values, read_set_text
from mt5_ea_validator.ubs_risk_sensitivity import (
    ANNUAL_ADJUSTMENT_LOTS,
    ANNUAL_CANDIDATE_LOTS,
    LOT_INPUTS,
    PILOT_VARIATIONS,
    RISK_MODE_PILOT_VARIATIONS,
    RISK_SELECTOR_PILOT_VARIATIONS,
    UBSRiskSensitivityCampaignError,
    UBSRiskSensitivityError,
    audit_saved_ubs_entry_volumes,
    build_ubs_same_risk_final_selection,
    run_ubs_risk_mode_pilot,
    run_ubs_risk_selector_pilot,
    run_ubs_risk_sensitivity_pilot,
    run_ubs_same_risk_annual_adjustments,
    run_ubs_same_risk_annual_candidates,
    run_ubs_same_risk_quarterly_suite,
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

    def _annual_model_run(self, root: Path) -> Path:
        run = root / "annual_model"
        run.mkdir()
        volumes = {
            "xau_sr_scalp_h1": "0.04",
            "xau_h1_c5": "0.01",
            "daily_l": "0.02",
            "e": "0.05",
            "goldtradepro_a": "0.01",
            "mt5_longterm_e": "0.01",
            "mt5_longterm_j": "0.01",
        }
        results = []
        for index, strategy_id in enumerate(STRATEGIES, start=1):
            case = run / f"case_{index}"
            case.mkdir()
            report = case / "report.htm"
            report.write_text(_report(volumes[strategy_id]), encoding="utf-8")
            results.append(
                {
                    "status": "success",
                    "model": 4,
                    "strategy_id": strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 100.0,
                        "trades": 1,
                        "equity_drawdown_percent": 5.0,
                    },
                    "deal_audit": {"deal_sequence_sha256": "annual-baseline"},
                }
            )
        (run / "suite_manifest.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "from_date": "2025.07.01",
                    "to_date": "2026.06.30",
                    "execution_mode": 0,
                    "deposit": 3000,
                    "currency": "USD",
                    "leverage": 500,
                    "results": results,
                }
            ),
            encoding="utf-8",
        )
        return run

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

    def test_risk_mode_pilot_changes_only_declared_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._pilot_inputs(root)
            for case_number, volume in ((1, "0.04"), (4, "0.04"), (5, "0.01")):
                (validation / f"case_{case_number}" / "report.htm").write_text(
                    _report(volume), encoding="utf-8"
                )
            observed: list[str] = []

            def fake_case_runner(
                _settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                output.mkdir(parents=True)
                report = output / "report.htm"
                report.write_text(_report("0.02"), encoding="utf-8")
                observed.append(strategy.file_name)
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 20.0,
                        "trades": 1,
                        "equity_drawdown_percent": 2.0,
                    },
                    "deal_audit": {"deal_sequence_sha256": "mode-pilot"},
                }

            run = run_ubs_risk_mode_pilot(
                settings,
                faithful,
                validation,
                case_runner=fake_case_runner,
            )

            manifest = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run.parent.name, "mode_pilot")
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["pilot_kind"], "risk_mode")
            self.assertEqual(len(observed), len(RISK_MODE_PILOT_VARIATIONS))
            specs = {
                spec["case_id"]: spec for spec in manifest["pilot_variations"]
            }
            fixed = specs["xau_sr_fixed_004"]
            self.assertEqual(
                fixed["changed_inputs"],
                ["AdjustLotsizeToVariableValues", "StartLots"],
            )
            fixed_values = parse_set_values(
                read_set_text(Path(fixed["derived_file"]))
            )
            self.assertEqual(fixed_values["AdjustLotsizeToVariableValues"], "false")
            self.assertEqual(fixed_values["StartLots"], "0.04")
            self.assertEqual(
                specs["gold_strategy_risk_half"]["changed_inputs"],
                ["MaxRiskPerStrategy_Value"],
            )

    def test_risk_mode_pilot_rejects_fixed_lot_at_twice_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._pilot_inputs(root)

            with self.assertRaisesRegex(UBSRiskSensitivityError, "2x"):
                run_ubs_risk_mode_pilot(settings, faithful, validation)

    def test_risk_selector_pilot_sets_risk_zero_and_exposure_caps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._pilot_inputs(root)
            for case_number, volume in ((1, "0.04"), (4, "0.04"), (5, "0.01")):
                (validation / f"case_{case_number}" / "report.htm").write_text(
                    _report(volume), encoding="utf-8"
                )

            def fake_case_runner(
                _settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                output.mkdir(parents=True)
                report = output / "report.htm"
                report.write_text(_report("0.02"), encoding="utf-8")
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 20.0,
                        "trades": 1,
                        "equity_drawdown_percent": 2.0,
                    },
                    "deal_audit": {"deal_sequence_sha256": "selector-pilot"},
                }

            run = run_ubs_risk_selector_pilot(
                settings,
                faithful,
                validation,
                case_runner=fake_case_runner,
            )

            manifest = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run.parent.name, "selector_pilot")
            self.assertEqual(manifest["pilot_kind"], "risk_selector")
            self.assertEqual(
                len(manifest["results"]), len(RISK_SELECTOR_PILOT_VARIATIONS)
            )
            specs = {
                spec["case_id"]: spec for spec in manifest["pilot_variations"]
            }
            candidate = specs["xau_sr_risk0_004"]
            self.assertEqual(
                candidate["changed_inputs"],
                [
                    "AdjustLotsizeToVariableValues",
                    "MaxLots",
                    "Risk",
                    "StartLots",
                ],
            )
            values = parse_set_values(read_set_text(Path(candidate["derived_file"])))
            self.assertEqual(values["Risk"], "0")
            self.assertEqual(values["StartLots"], "0.04")
            self.assertEqual(values["MaxLots"], "0.07")

    def test_annual_candidates_resume_and_write_target_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._pilot_inputs(root)
            model_run = self._annual_model_run(root)
            calls: list[str] = []

            def result_for(strategy, output):
                output.mkdir(parents=True)
                report = output / "report.htm"
                report.write_text(
                    _report(ANNUAL_CANDIDATE_LOTS[strategy.strategy_id]),
                    encoding="utf-8",
                )
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 120.0,
                        "trades": 1,
                        "equity_drawdown_percent": 5.0,
                    },
                    "deal_audit": {"deal_sequence_sha256": "annual-baseline"},
                }

            def interrupted_runner(
                _settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                calls.append(strategy.strategy_id)
                if len(calls) == 3:
                    output.mkdir(parents=True)
                    raise RuntimeError("interrupted")
                return result_for(strategy, output)

            with self.assertRaises(UBSRiskSensitivityCampaignError) as captured:
                run_ubs_same_risk_annual_candidates(
                    settings,
                    faithful,
                    validation,
                    model_run,
                    case_runner=interrupted_runner,
                )
            run = captured.exception.run_directory
            failed = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(len(failed["results"]), 2)
            self.assertEqual(
                failed["resource_limits"],
                {
                    "priority": "BelowNormal",
                    "logical_cpu_count": 4,
                    "affinity_mask": "0xf",
                    "timeout_seconds": 7200,
                },
            )

            resumed_calls: list[str] = []
            resumed_timeouts: list[int] = []

            def resumed_runner(
                _settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                resumed_calls.append(strategy.strategy_id)
                resumed_timeouts.append(_settings.timeout_seconds)
                return result_for(strategy, output)

            completed = run_ubs_same_risk_annual_candidates(
                settings,
                faithful,
                validation,
                model_run,
                case_runner=resumed_runner,
                resume_directory=run,
            )

            self.assertEqual(completed, run)
            self.assertEqual(resumed_calls, list(STRATEGIES[2:]))
            self.assertEqual(resumed_timeouts, [7200] * len(STRATEGIES[2:]))
            manifest = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            summary = json.loads(
                (run / "annual_candidate_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(len(manifest["results"]), 7)
            self.assertEqual(summary["case_count"], 7)
            self.assertTrue(
                all(
                    row["target_dd_status"] == "within_target"
                    for row in summary["results"]
                )
            )
            for spec in manifest["candidate_variations"]:
                values = parse_set_values(read_set_text(Path(spec["derived_file"])))
                expected_lot = ANNUAL_CANDIDATE_LOTS[spec["strategy_id"]]
                self.assertEqual(values["Risk"], "0")
                self.assertEqual(values["StartLots"], expected_lot)
                self.assertEqual(values["MaxLots"], expected_lot)

    def test_annual_adjustments_reuse_runner_for_four_adjacent_lots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._pilot_inputs(root)
            model_run = self._annual_model_run(root)
            initial_run = root / "initial_candidates"
            initial_run.mkdir()
            (initial_run / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "results": [
                            {"status": "success", "case_id": f"case_{index}"}
                            for index in range(7)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            calls: list[str] = []

            def fake_case_runner(
                _settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                calls.append(strategy.strategy_id)
                output.mkdir(parents=True)
                report = output / "report.htm"
                report.write_text(
                    _report(ANNUAL_ADJUSTMENT_LOTS[strategy.strategy_id]),
                    encoding="utf-8",
                )
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 150.0,
                        "trades": 1,
                        "equity_drawdown_percent": 5.0,
                    },
                    "deal_audit": {"deal_sequence_sha256": "annual-baseline"},
                }

            run = run_ubs_same_risk_annual_adjustments(
                settings,
                faithful,
                validation,
                model_run,
                initial_run,
                case_runner=fake_case_runner,
            )

            manifest = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            summary = json.loads(
                (run / "annual_adjustment_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(run.parent.name, "annual_adjustments")
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["suite_kind"], "adjacent_lot_adjustments")
            self.assertEqual(manifest["candidate_lots"], ANNUAL_ADJUSTMENT_LOTS)
            self.assertEqual(manifest["cases_requested"], 4)
            self.assertEqual(calls, list(ANNUAL_ADJUSTMENT_LOTS))
            self.assertEqual(summary["case_count"], 4)
            self.assertTrue((run / "annual_adjustment_summary.csv").is_file())
            for spec in manifest["candidate_variations"]:
                values = parse_set_values(read_set_text(Path(spec["derived_file"])))
                expected_lot = ANNUAL_ADJUSTMENT_LOTS[spec["strategy_id"]]
                self.assertEqual(values["Risk"], "0")
                self.assertEqual(values["StartLots"], expected_lot)
                self.assertEqual(values["MaxLots"], expected_lot)

    def test_final_selection_respects_upper_dd_limit_before_distance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial_run = root / "initial"
            adjustment_run = root / "adjustment"
            initial_run.mkdir()
            adjustment_run.mkdir()
            set_directory = root / "candidate_sets"
            set_directory.mkdir()

            def candidate(strategy_id: str, lot: str, dd: float):
                case_id = f"{strategy_id}_{lot.replace('.', '')}"
                set_file = set_directory / f"{case_id}.set"
                set_file.write_text(f"StartLots={lot}\n", encoding="utf-8")
                digest = sha256_file(set_file)
                result = {
                    "status": "success",
                    "case_id": case_id,
                    "strategy_id": strategy_id,
                    "candidate_lot": lot,
                    "report_file": str(root / strategy_id / "report.htm"),
                    "derived_set_sha256": digest,
                    "metrics": {
                        "net_profit": 100.0,
                        "trades": 10,
                        "profit_factor": 1.5,
                        "equity_drawdown_percent": dd,
                    },
                    "entry_volume_audit": {
                        "minimum_entry_volume": lot,
                        "maximum_entry_volume": lot,
                    },
                    "baseline_deal_sequence_same": True,
                }
                variation = {
                    "case_id": case_id,
                    "strategy_id": strategy_id,
                    "derived_file": str(set_file.resolve()),
                    "derived_sha256": digest,
                }
                return result, variation

            initial_dd = {
                "xau_sr_scalp_h1": 6.34,
                "xau_h1_c5": 3.63,
                "daily_l": 4.75,
                "e": 4.88,
                "goldtradepro_a": 10.86,
                "mt5_longterm_e": 3.84,
                "mt5_longterm_j": 3.89,
            }
            initial_pairs = [
                candidate(strategy_id, lot, initial_dd[strategy_id])
                for strategy_id, lot in ANNUAL_CANDIDATE_LOTS.items()
            ]
            initial_manifest = initial_run / "run_manifest.json"
            initial_manifest.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "results": [pair[0] for pair in initial_pairs],
                        "candidate_variations": [pair[1] for pair in initial_pairs],
                    }
                ),
                encoding="utf-8",
            )
            adjustment_dd = {
                "xau_sr_scalp_h1": 4.90,
                "xau_h1_c5": 5.29,
                "mt5_longterm_e": 7.48,
                "mt5_longterm_j": 5.64,
            }
            adjustment_pairs = [
                candidate(strategy_id, lot, adjustment_dd[strategy_id])
                for strategy_id, lot in ANNUAL_ADJUSTMENT_LOTS.items()
            ]
            (adjustment_run / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "source_initial_candidate_manifest": str(
                            initial_manifest.resolve()
                        ),
                        "source_initial_candidate_manifest_sha256": sha256_file(
                            initial_manifest
                        ),
                        "results": [pair[0] for pair in adjustment_pairs],
                        "candidate_variations": [
                            pair[1] for pair in adjustment_pairs
                        ],
                    }
                ),
                encoding="utf-8",
            )

            output = build_ubs_same_risk_final_selection(
                initial_run, adjustment_run
            )

            payload = json.loads(
                (output / "final_selection.json").read_text(encoding="utf-8")
            )
            selected = {
                row["strategy_id"]: row for row in payload["selections"]
            }
            expected_lots = {
                "xau_sr_scalp_h1": "0.03",
                "xau_h1_c5": "0.03",
                "daily_l": "0.04",
                "e": "0.03",
                "goldtradepro_a": "0.01",
                "mt5_longterm_e": "0.01",
                "mt5_longterm_j": "0.02",
            }
            self.assertEqual(
                {name: row["selected_lot"] for name, row in selected.items()},
                expected_lots,
            )
            self.assertEqual(
                selected["mt5_longterm_j"]["selection_status"],
                "lot_granularity_limited_below",
            )
            self.assertEqual(
                selected["goldtradepro_a"]["selection_status"],
                "minimum_lot_limited_above",
            )
            self.assertTrue(
                all(Path(row["selected_set_file"]).is_file() for row in selected.values())
            )
            self.assertTrue((output / "final_selection.csv").is_file())

    def test_same_risk_quarterly_resumes_and_marks_dd_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings, faithful, validation = self._pilot_inputs(root)
            selection_directory = root / "selection"
            selection_directory.mkdir()
            initial_manifest = selection_directory / "initial.json"
            adjustment_manifest = selection_directory / "adjustment.json"
            initial_manifest.write_text("{}", encoding="utf-8")
            adjustment_manifest.write_text("{}", encoding="utf-8")
            selected_lots = {
                "xau_sr_scalp_h1": "0.03",
                "xau_h1_c5": "0.03",
                "daily_l": "0.04",
                "e": "0.03",
                "goldtradepro_a": "0.01",
                "mt5_longterm_e": "0.01",
                "mt5_longterm_j": "0.02",
            }
            selections = []
            for strategy_id, lot in selected_lots.items():
                set_file = selection_directory / f"{strategy_id}.set"
                set_file.write_text(
                    "\n".join(
                        [
                            "Risk=0",
                            "AdjustLotsizeToVariableValues=false",
                            f"StartLots={lot}",
                            f"MaxLots={lot}",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )
                selections.append(
                    {
                        "strategy_id": strategy_id,
                        "selected_lot": lot,
                        "selection_status": "within_target",
                        "equity_drawdown_percent": 5.0,
                        "selected_result_file": str(
                            selection_directory / f"{strategy_id}_result.json"
                        ),
                        "selected_set_file": str(set_file.resolve()),
                        "selected_set_sha256": sha256_file(set_file),
                    }
                )
            selection_path = selection_directory / "final_selection.json"
            selection_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "initial_candidate_manifest": str(
                            initial_manifest.resolve()
                        ),
                        "initial_candidate_manifest_sha256": sha256_file(
                            initial_manifest
                        ),
                        "adjustment_manifest": str(adjustment_manifest.resolve()),
                        "adjustment_manifest_sha256": sha256_file(
                            adjustment_manifest
                        ),
                        "strategy_count": 7,
                        "selections": selections,
                    }
                ),
                encoding="utf-8",
            )

            first_calls: list[tuple[str, str, str, int]] = []

            def result_for(_settings, strategy, output):
                output.mkdir(parents=True)
                lot = selected_lots[strategy.strategy_id]
                report = output / "report.htm"
                report.write_text(_report(lot), encoding="utf-8")
                dd = 8.0 if strategy.strategy_id == "xau_sr_scalp_h1" else 4.0
                return {
                    "status": "success",
                    "strategy_id": strategy.strategy_id,
                    "report_file": str(report),
                    "metrics": {
                        "net_profit": 10.0,
                        "trades": 1,
                        "profit_factor": 1.2,
                        "recovery_factor": 0.8,
                        "sharpe_ratio": 0.5,
                        "equity_drawdown_percent": dd,
                    },
                    "deal_audit": {
                        "deal_count": 2,
                        "deal_sequence_sha256": "quarterly-deals",
                        "commission_total": -0.5,
                        "swap_total": 0.0,
                        "deal_profit_total": 10.5,
                    },
                    "tick_data_quality": {
                        "passed": True,
                        # No fallback warning is represented as None by the
                        # real-tick log parser and must be treated as zero.
                        "fallback_minute_count": None,
                    },
                }

            def interrupted_runner(
                case_settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                first_calls.append(
                    (
                        strategy.strategy_id,
                        case_settings.from_date,
                        case_settings.to_date,
                        case_settings.model,
                    )
                )
                if len(first_calls) == 3:
                    output.mkdir(parents=True)
                    raise RuntimeError("interrupted")
                return result_for(case_settings, strategy, output)

            with self.assertRaises(UBSRiskSensitivityCampaignError) as captured:
                run_ubs_same_risk_quarterly_suite(
                    settings,
                    selection_path,
                    faithful,
                    validation,
                    case_runner=interrupted_runner,
                    progress=lambda _message: None,
                )
            run = captured.exception.run_directory
            failed = json.loads(
                (run / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(len(failed["results"]), 2)

            resumed_calls: list[str] = []

            def resumed_runner(
                case_settings,
                strategy,
                _scenario,
                output,
                **_kwargs,
            ):
                resumed_calls.append(strategy.strategy_id)
                self.assertEqual(case_settings.model, 4)
                self.assertEqual(case_settings.execution_mode, 0)
                self.assertEqual(case_settings.timeout_seconds, 7200)
                return result_for(case_settings, strategy, output)

            completed = run_ubs_same_risk_quarterly_suite(
                settings,
                selection_path,
                faithful,
                validation,
                case_runner=resumed_runner,
                resume_directory=run,
                progress=lambda _message: None,
            )

            manifest = json.loads(
                (completed / "run_manifest.json").read_text(encoding="utf-8")
            )
            summary = json.loads(
                (completed / "quarterly_same_risk_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(len(manifest["results"]), 28)
            self.assertEqual(len(resumed_calls), 26)
            self.assertEqual(summary["case_count"], 28)
            self.assertEqual(summary["warning_count"], 4)
            self.assertEqual(
                manifest["tick_data_quality_limits"],
                {
                    "max_generated_fallback_ratio": 0.0003,
                    "max_generated_fallback_minutes": 24,
                },
            )
            self.assertTrue(
                (completed / "quarterly_same_risk_summary.csv").is_file()
            )
            self.assertEqual(first_calls[0][1:], ("2025.07.01", "2025.09.30", 4))


if __name__ == "__main__":
    unittest.main()
