from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from mt5_ea_validator.configuration import Scenario
from mt5_ea_validator.mt5 import (
    MT5Executor,
    run_below_normal_two_cpus,
    sha256_file,
)
from mt5_ea_validator.report import parse_entry_volume_audit
from mt5_ea_validator.setfile import (
    override_set_values,
    parse_set_values,
    read_set_text,
    write_mt5_unicode,
)
from mt5_ea_validator.ubs_quarterly import validate_quarterly_prerequisites
from mt5_ea_validator.ubs_smoke import (
    JST,
    Executor,
    UBSSmokeSettings,
    UBSStrategy,
    _scenario,
    execute_ubs_case,
)


LOT_INPUTS = (
    "AdjustLotsizeToVariableValues",
    "Risk",
    "StartLots",
    "Manual_RiskPerTrade",
    "MaxRiskInDollar_input",
    "LotPerBalance_step",
    "MaxRiskPerStrategy_Value",
    "HistoricalMaxDD",
    "MaxLots",
    "MaxTrades",
)


class UBSRiskSensitivityError(ValueError):
    """Raised when UBS risk-sensitivity evidence is incomplete or unsafe."""


class UBSRiskSensitivityCampaignError(RuntimeError):
    def __init__(self, message: str, run_directory: Path) -> None:
        super().__init__(message)
        self.run_directory = run_directory


@dataclass(frozen=True)
class PilotVariation:
    case_id: str
    strategy_id: str
    parameter: str
    factor: str


PILOT_VARIATIONS = (
    PilotVariation("xau_sr_hmaxdd_half", "xau_sr_scalp_h1", "HistoricalMaxDD", "0.5"),
    PilotVariation("xau_sr_hmaxdd_double", "xau_sr_scalp_h1", "HistoricalMaxDD", "2"),
    PilotVariation("xau_sr_lpb_half", "xau_sr_scalp_h1", "LotPerBalance_step", "0.5"),
    PilotVariation("xau_sr_lpb_double", "xau_sr_scalp_h1", "LotPerBalance_step", "2"),
    PilotVariation("xau_sr_startlots_half", "xau_sr_scalp_h1", "StartLots", "0.5"),
    PilotVariation("gold_hmaxdd_half", "goldtradepro_a", "HistoricalMaxDD", "0.5"),
    PilotVariation("gold_hmaxdd_double", "goldtradepro_a", "HistoricalMaxDD", "2"),
)


def _load_json(path: Path, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UBSRiskSensitivityError(f"Cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UBSRiskSensitivityError(f"{label} must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"Existing audit is not overwritten: {path}")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if path.exists():
        raise FileExistsError(f"Existing audit is not overwritten: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def audit_saved_ubs_entry_volumes(model_run: Path, output_directory: Path) -> Path:
    """Audit entry volumes in the seven saved real-tick UBS cases."""
    model_run = model_run.resolve()
    manifest_path = model_run / "suite_manifest.json"
    manifest = _load_json(manifest_path, label="model-comparison manifest")
    results = manifest.get("results")
    if manifest.get("status") != "success" or not isinstance(results, list):
        raise UBSRiskSensitivityError("A successful model-comparison run is required")
    real_results = [
        result
        for result in results
        if isinstance(result, dict)
        and int(result.get("model", -1)) == 4
        and result.get("status") == "success"
    ]
    if len(real_results) != 7:
        raise UBSRiskSensitivityError(
            f"Expected seven successful real-tick cases, found {len(real_results)}"
        )

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for result in real_results:
        strategy_id = str(result.get("strategy_id", ""))
        if not strategy_id or strategy_id in seen:
            raise UBSRiskSensitivityError(
                f"Missing or duplicate strategy_id: {strategy_id!r}"
            )
        seen.add(strategy_id)
        report_path = Path(str(result.get("report_file", "")))
        if not report_path.is_file():
            raise UBSRiskSensitivityError(
                f"Saved report does not exist: {strategy_id}: {report_path}"
            )
        applied_path = report_path.parent / "applied_inputs.json"
        applied = _load_json(applied_path, label=f"applied inputs for {strategy_id}")
        missing = [name for name in LOT_INPUTS if name not in applied]
        if missing:
            raise UBSRiskSensitivityError(
                f"Applied inputs are missing lot fields: {strategy_id}: {missing}"
            )
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            raise UBSRiskSensitivityError(f"Metrics are missing: {strategy_id}")
        volume = parse_entry_volume_audit(report_path)
        rows.append(
            {
                "strategy_id": strategy_id,
                "net_profit": metrics["net_profit"],
                "trades": metrics["trades"],
                "equity_drawdown_percent": metrics["equity_drawdown_percent"],
                **volume.to_dict(),
                **{name: applied[name] for name in LOT_INPUTS},
                "report_file": str(report_path),
            }
        )

    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    summary = {
        "schema_version": 1,
        "status": "success",
        "source_model_run": str(model_run),
        "source_manifest_sha256": sha256_file(manifest_path),
        "model": 4,
        "model_label": "Every tick based on real ticks",
        "from_date": manifest.get("from_date"),
        "to_date": manifest.get("to_date"),
        "deposit": manifest.get("deposit"),
        "strategy_count": len(rows),
        "lot_inputs": list(LOT_INPUTS),
        "results": rows,
    }
    _write_json(output_directory / "entry_volume_summary.json", summary)
    _write_csv(output_directory / "entry_volume_summary.csv", rows)
    return output_directory


def _scaled_value(current: str, factor: str) -> str:
    try:
        result = Decimal(current) * Decimal(factor)
    except InvalidOperation as exc:
        raise UBSRiskSensitivityError(
            f"Pilot input is not numeric: current={current!r}, factor={factor!r}"
        ) from exc
    if result <= 0:
        raise UBSRiskSensitivityError("Pilot input must remain positive")
    rendered = format(result, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _pilot_specs(
    set_directory: Path,
    strategies: tuple[UBSStrategy, ...],
) -> list[dict[str, object]]:
    strategy_by_id = {strategy.strategy_id: strategy for strategy in strategies}
    specs: list[dict[str, object]] = []
    for variation in PILOT_VARIATIONS:
        strategy = strategy_by_id.get(variation.strategy_id)
        if strategy is None:
            raise UBSRiskSensitivityError(
                f"Pilot strategy is not configured: {variation.strategy_id}"
            )
        source = set_directory / strategy.file_name
        values = parse_set_values(read_set_text(source))
        if variation.parameter not in values:
            raise UBSRiskSensitivityError(
                f"Pilot parameter is missing: {variation.strategy_id}/"
                f"{variation.parameter}"
            )
        original = values[variation.parameter]
        updated = _scaled_value(original, variation.factor)
        factor = Decimal(variation.factor)
        if factor > Decimal("2"):
            raise UBSRiskSensitivityError(
                f"Pilot factor exceeds 2x: {variation.case_id}={factor}"
            )
        if Decimal(updated) / Decimal(original) != factor:
            raise UBSRiskSensitivityError(
                f"Generated pilot value does not preserve the requested factor: "
                f"{variation.case_id}: {original} -> {updated}, factor={factor}"
            )
        specs.append(
            {
                **asdict(variation),
                "original_value": original,
                "updated_value": updated,
                "source_file": str(source.resolve()),
                "source_sha256": sha256_file(source),
                "derived_file_name": f"{variation.case_id}.set",
            }
        )
    return specs


def _build_pilot_sets(
    specs: list[dict[str, object]], derived_directory: Path
) -> None:
    derived_directory.mkdir(parents=True, exist_ok=False)
    for spec in specs:
        source = Path(str(spec["source_file"]))
        parameter = str(spec["parameter"])
        updated = str(spec["updated_value"])
        source_text = read_set_text(source)
        derived_text = override_set_values(source_text, {parameter: updated})
        source_values = parse_set_values(source_text)
        derived_values = parse_set_values(derived_text)
        changed = {
            name
            for name in set(source_values) | set(derived_values)
            if source_values.get(name) != derived_values.get(name)
        }
        if changed != {parameter}:
            raise UBSRiskSensitivityError(
                f"Derived set changed unexpected inputs: {spec['case_id']}: {changed}"
            )
        destination = derived_directory / str(spec["derived_file_name"])
        write_mt5_unicode(destination, derived_text)
        spec["derived_file"] = str(destination.resolve())
        spec["derived_sha256"] = sha256_file(destination)
        spec["changed_inputs"] = sorted(changed)


def _load_baselines(
    faithful_validation_run: Path,
    settings: UBSSmokeSettings,
) -> dict[str, dict[str, object]]:
    manifest = _load_json(
        faithful_validation_run / "run_manifest.json",
        label="faithful-validation baseline manifest",
    )
    conditions = manifest.get("conditions")
    expected = {
        "symbol": settings.symbol,
        "period": settings.period,
        "from_date": settings.from_date,
        "to_date": settings.to_date,
        "model": settings.model,
        "execution_mode": settings.execution_mode,
        "deposit": settings.deposit,
        "currency": settings.currency,
        "leverage": settings.leverage,
    }
    if manifest.get("status") != "success" or conditions != expected:
        raise UBSRiskSensitivityError(
            "Faithful-validation baseline conditions do not match the pilot"
        )
    raw_results = manifest.get("results")
    if not isinstance(raw_results, list) or len(raw_results) != 7:
        raise UBSRiskSensitivityError("Faithful-validation baseline must have 7 results")
    baselines: dict[str, dict[str, object]] = {}
    for result in raw_results:
        if not isinstance(result, dict) or result.get("status") != "success":
            raise UBSRiskSensitivityError("Faithful-validation baseline is incomplete")
        strategy_id = str(result["strategy_id"])
        report = Path(str(result["report_file"]))
        enriched = dict(result)
        enriched["entry_volume_audit"] = parse_entry_volume_audit(report).to_dict()
        baselines[strategy_id] = enriched
    return baselines


def _run_id(value: datetime) -> str:
    return f"{value.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"


def _summary_rows(
    results: list[dict[str, object]],
    baselines: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        strategy_id = str(result["strategy_id"])
        metrics = result["metrics"]
        volumes = result["entry_volume_audit"]
        baseline = baselines[strategy_id]
        baseline_metrics = baseline["metrics"]
        baseline_volumes = baseline["entry_volume_audit"]
        baseline_mean = float(baseline_volumes["mean_entry_volume"])
        current_mean = float(volumes["mean_entry_volume"])
        rows.append(
            {
                "case_id": result["case_id"],
                "strategy_id": strategy_id,
                "parameter": result["parameter"],
                "original_value": result["original_value"],
                "updated_value": result["updated_value"],
                "factor": result["factor"],
                "baseline_mean_entry_volume": baseline_mean,
                "pilot_mean_entry_volume": current_mean,
                "mean_entry_volume_ratio": (
                    current_mean / baseline_mean if baseline_mean else None
                ),
                "baseline_minimum_entry_volume": baseline_volumes[
                    "minimum_entry_volume"
                ],
                "pilot_minimum_entry_volume": volumes["minimum_entry_volume"],
                "baseline_maximum_entry_volume": baseline_volumes[
                    "maximum_entry_volume"
                ],
                "pilot_maximum_entry_volume": volumes["maximum_entry_volume"],
                "baseline_net_profit": baseline_metrics["net_profit"],
                "pilot_net_profit": metrics["net_profit"],
                "baseline_equity_drawdown_percent": baseline_metrics[
                    "equity_drawdown_percent"
                ],
                "pilot_equity_drawdown_percent": metrics[
                    "equity_drawdown_percent"
                ],
                "baseline_trades": baseline_metrics["trades"],
                "pilot_trades": metrics["trades"],
                "deal_sequence_same": result["deal_audit"]["deal_sequence_sha256"]
                == baseline["deal_audit"]["deal_sequence_sha256"],
            }
        )
    return rows


CaseRunner = Callable[..., dict[str, object]]


def _limited_executor_factory(scenario: Scenario) -> MT5Executor:
    return MT5Executor(scenario, process_runner=run_below_normal_two_cpus)


def run_ubs_risk_sensitivity_pilot(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = _limited_executor_factory,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    progress: Callable[[str], None] = print,
) -> Path:
    faithful, strategies = validate_quarterly_prerequisites(
        settings, faithful_manifest_path, faithful_validation_run
    )
    if settings.model != 4 or settings.execution_mode != 0:
        raise UBSRiskSensitivityError("Pilot requires real ticks and No Delay")
    baselines = _load_baselines(faithful_validation_run, settings)
    specs = _pilot_specs(faithful_manifest_path.parent, strategies)
    started = now_factory()
    output_root = settings.output_root.parent / "risk_sensitivity" / "pilot"
    output_root.mkdir(parents=True, exist_ok=True)
    run_directory = output_root / _run_id(started)
    run_directory.mkdir(exist_ok=False)
    derived_directory = run_directory / "derived_sets"
    _build_pilot_sets(specs, derived_directory)
    manifest_path = run_directory / "run_manifest.json"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_directory.name,
        "status": "running",
        "started_at_jst": started.isoformat(timespec="seconds"),
        "finished_at_jst": None,
        "conditions": {
            "symbol": settings.symbol,
            "period": settings.period,
            "from_date": settings.from_date,
            "to_date": settings.to_date,
            "model": settings.model,
            "execution_mode": settings.execution_mode,
            "deposit": settings.deposit,
            "currency": settings.currency,
            "leverage": settings.leverage,
        },
        "resource_limits": {
            "priority": "BelowNormal",
            "logical_cpu_count": 2,
            "affinity_mask": "0x3",
        },
        "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
        "faithful_validation_run": str(faithful_validation_run.resolve()),
        "source_smoke_run": faithful["source_smoke_run"],
        "pilot_variations": specs,
        "cases_requested": len(specs),
        "results": [],
        "error": None,
    }
    _write_json(manifest_path, manifest)
    logger = logging.getLogger(
        f"mt5_ea_validator.ubs_risk_sensitivity.{run_directory.name}"
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(run_directory / "execution.log", encoding="utf-8")
    logger.addHandler(handler)
    results = manifest["results"]
    assert isinstance(results, list)
    try:
        for case_number, spec in enumerate(specs, start=1):
            strategy_id = str(spec["strategy_id"])
            derived_strategy = UBSStrategy(
                strategy_id=strategy_id,
                file_name=str(spec["derived_file_name"]),
            )
            pilot_settings = replace(
                settings,
                scenario_id=f"ubs_risk_pilot_{spec['case_id']}",
                set_directory=derived_directory,
                strategies=(derived_strategy,),
            )
            scenario = replace(
                _scenario(pilot_settings, derived_strategy),
                scenario_id=f"ubs_risk_pilot_{spec['case_id']}",
                wf="RISK_PILOT",
                staged_set_name=(
                    f"UBS_RISK_PILOT_{spec['case_id']}_"
                    f"{str(spec['derived_sha256'])[:8]}.set"
                ),
            )
            output = run_directory / f"{case_number:02d}_{spec['case_id']}"
            progress(f"START [{case_number}/{len(specs)}] {spec['case_id']}")
            result = case_runner(
                pilot_settings,
                derived_strategy,
                scenario,
                output,
                executor_factory=executor_factory,
                require_report_covered_inputs=True,
            )
            result.update(
                {
                    "case_number": case_number,
                    "case_id": spec["case_id"],
                    "parameter": spec["parameter"],
                    "factor": spec["factor"],
                    "original_value": spec["original_value"],
                    "updated_value": spec["updated_value"],
                    "source_set_sha256": spec["source_sha256"],
                    "derived_set_sha256": spec["derived_sha256"],
                }
            )
            if result.get("status") != "success":
                raise UBSRiskSensitivityCampaignError(
                    f"Risk-sensitivity pilot failed: {spec['case_id']}", run_directory
                )
            report_path = Path(str(result["report_file"]))
            result["entry_volume_audit"] = parse_entry_volume_audit(
                report_path
            ).to_dict()
            results.append(result)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            progress(f"PASS [{case_number}/{len(specs)}] {spec['case_id']}")
        rows = _summary_rows(results, baselines)
        _write_json(
            run_directory / "pilot_summary.json",
            {"schema_version": 1, "case_count": len(rows), "results": rows},
        )
        _write_csv(run_directory / "pilot_summary.csv", rows)
        manifest["status"] = "success"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        logger.exception("risk-sensitivity pilot failed")
        if isinstance(exc, UBSRiskSensitivityCampaignError):
            raise
        raise UBSRiskSensitivityCampaignError(str(exc), run_directory) from exc
    finally:
        manifest["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        handler.close()
        logger.removeHandler(handler)
    return run_directory
