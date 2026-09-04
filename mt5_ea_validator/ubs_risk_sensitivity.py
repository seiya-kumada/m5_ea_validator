from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping

from mt5_ea_validator.configuration import WF_PERIODS, Scenario
from mt5_ea_validator.mt5 import (
    MT5Executor,
    run_below_normal_four_cpus,
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


@dataclass(frozen=True)
class PilotOverrideVariation:
    case_id: str
    strategy_id: str
    overrides: tuple[tuple[str, str], ...]


PILOT_VARIATIONS = (
    PilotVariation("xau_sr_hmaxdd_half", "xau_sr_scalp_h1", "HistoricalMaxDD", "0.5"),
    PilotVariation("xau_sr_hmaxdd_double", "xau_sr_scalp_h1", "HistoricalMaxDD", "2"),
    PilotVariation("xau_sr_lpb_half", "xau_sr_scalp_h1", "LotPerBalance_step", "0.5"),
    PilotVariation("xau_sr_lpb_double", "xau_sr_scalp_h1", "LotPerBalance_step", "2"),
    PilotVariation("xau_sr_startlots_half", "xau_sr_scalp_h1", "StartLots", "0.5"),
    PilotVariation("gold_hmaxdd_half", "goldtradepro_a", "HistoricalMaxDD", "0.5"),
    PilotVariation("gold_hmaxdd_double", "goldtradepro_a", "HistoricalMaxDD", "2"),
)


RISK_MODE_PILOT_VARIATIONS = (
    PilotOverrideVariation(
        "xau_sr_fixed_002",
        "xau_sr_scalp_h1",
        (("AdjustLotsizeToVariableValues", "false"), ("StartLots", "0.02")),
    ),
    PilotOverrideVariation(
        "xau_sr_fixed_004",
        "xau_sr_scalp_h1",
        (("AdjustLotsizeToVariableValues", "false"), ("StartLots", "0.04")),
    ),
    PilotOverrideVariation(
        "xau_sr_fixed_007",
        "xau_sr_scalp_h1",
        (("AdjustLotsizeToVariableValues", "false"), ("StartLots", "0.07")),
    ),
    PilotOverrideVariation(
        "e_fixed_002",
        "e",
        (("AdjustLotsizeToVariableValues", "false"), ("StartLots", "0.02")),
    ),
    PilotOverrideVariation(
        "e_fixed_004",
        "e",
        (("AdjustLotsizeToVariableValues", "false"), ("StartLots", "0.04")),
    ),
    PilotOverrideVariation(
        "gold_fixed_001",
        "goldtradepro_a",
        (("AdjustLotsizeToVariableValues", "false"), ("StartLots", "0.01")),
    ),
    PilotOverrideVariation(
        "e_strategy_risk_half",
        "e",
        (("MaxRiskPerStrategy_Value", "0.5"),),
    ),
    PilotOverrideVariation(
        "gold_strategy_risk_half",
        "goldtradepro_a",
        (("MaxRiskPerStrategy_Value", "0.5"),),
    ),
)


RISK_SELECTOR_PILOT_VARIATIONS = (
    PilotOverrideVariation(
        "xau_sr_risk0_002",
        "xau_sr_scalp_h1",
        (
            ("Risk", "0"),
            ("AdjustLotsizeToVariableValues", "false"),
            ("StartLots", "0.02"),
            ("MaxLots", "0.07"),
        ),
    ),
    PilotOverrideVariation(
        "xau_sr_risk0_004",
        "xau_sr_scalp_h1",
        (
            ("Risk", "0"),
            ("AdjustLotsizeToVariableValues", "false"),
            ("StartLots", "0.04"),
            ("MaxLots", "0.07"),
        ),
    ),
    PilotOverrideVariation(
        "xau_sr_risk0_007",
        "xau_sr_scalp_h1",
        (
            ("Risk", "0"),
            ("AdjustLotsizeToVariableValues", "false"),
            ("StartLots", "0.07"),
            ("MaxLots", "0.07"),
        ),
    ),
    PilotOverrideVariation(
        "e_risk0_002",
        "e",
        (
            ("Risk", "0"),
            ("AdjustLotsizeToVariableValues", "false"),
            ("StartLots", "0.02"),
            ("MaxLots", "0.07"),
        ),
    ),
    PilotOverrideVariation(
        "e_risk0_004",
        "e",
        (
            ("Risk", "0"),
            ("AdjustLotsizeToVariableValues", "false"),
            ("StartLots", "0.04"),
            ("MaxLots", "0.07"),
        ),
    ),
    PilotOverrideVariation(
        "gold_risk0_001",
        "goldtradepro_a",
        (
            ("Risk", "0"),
            ("AdjustLotsizeToVariableValues", "false"),
            ("StartLots", "0.01"),
            ("MaxLots", "0.01"),
        ),
    ),
)


ANNUAL_CANDIDATE_LOTS = {
    "xau_sr_scalp_h1": "0.04",
    "xau_h1_c5": "0.02",
    "daily_l": "0.04",
    "e": "0.03",
    "goldtradepro_a": "0.01",
    "mt5_longterm_e": "0.01",
    "mt5_longterm_j": "0.02",
}
ANNUAL_ADJUSTMENT_LOTS = {
    "xau_sr_scalp_h1": "0.03",
    "xau_h1_c5": "0.03",
    "mt5_longterm_e": "0.02",
    "mt5_longterm_j": "0.03",
}
ANNUAL_FROM_DATE = "2025.07.01"
ANNUAL_TO_DATE = "2026.06.30"
TARGET_DD_MIN = 4.5
TARGET_DD_MAX = 5.5
MAX_GENERATED_FALLBACK_RATIO = 0.0001
SAME_RISK_QUARTERLY_DD_WARNING = 7.5
SAME_RISK_QUARTERLY_MAX_FALLBACK_RATIO = 0.0003
SAME_RISK_QUARTERLY_MAX_FALLBACK_MINUTES = 24
EXPECTED_WFS = ("WF1", "WF2", "WF3", "WF4")


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
                "overrides": {variation.parameter: updated},
                "expected_changed_inputs": [variation.parameter],
                "source_file": str(source.resolve()),
                "source_sha256": sha256_file(source),
                "derived_file_name": f"{variation.case_id}.set",
            }
        )
    return specs


def _explicit_pilot_specs(
    set_directory: Path,
    strategies: tuple[UBSStrategy, ...],
    variations: tuple[PilotOverrideVariation, ...],
) -> list[dict[str, object]]:
    strategy_by_id = {strategy.strategy_id: strategy for strategy in strategies}
    specs: list[dict[str, object]] = []
    for variation in variations:
        strategy = strategy_by_id.get(variation.strategy_id)
        if strategy is None:
            raise UBSRiskSensitivityError(
                f"Pilot strategy is not configured: {variation.strategy_id}"
            )
        source = set_directory / strategy.file_name
        values = parse_set_values(read_set_text(source))
        overrides = dict(variation.overrides)
        if len(overrides) != len(variation.overrides):
            raise UBSRiskSensitivityError(
                f"Duplicate override input: {variation.case_id}"
            )
        missing = sorted(set(overrides) - set(values))
        if missing:
            raise UBSRiskSensitivityError(
                f"Pilot parameters are missing: {variation.case_id}: {missing}"
            )
        unchanged = sorted(
            name for name, updated in overrides.items() if values[name] == updated
        )
        if unchanged:
            raise UBSRiskSensitivityError(
                f"Pilot overrides must change their inputs: "
                f"{variation.case_id}: {unchanged}"
            )
        originals = {name: values[name] for name in overrides}
        specs.append(
            {
                "case_id": variation.case_id,
                "strategy_id": variation.strategy_id,
                "parameter": "+".join(overrides),
                "factor": "explicit",
                "original_value": json.dumps(
                    originals, ensure_ascii=False, sort_keys=True
                ),
                "updated_value": json.dumps(
                    overrides, ensure_ascii=False, sort_keys=True
                ),
                "overrides": overrides,
                "expected_changed_inputs": sorted(overrides),
                "source_file": str(source.resolve()),
                "source_sha256": sha256_file(source),
                "derived_file_name": f"{variation.case_id}.set",
            }
        )
    return specs


def _risk_mode_pilot_specs(
    set_directory: Path,
    strategies: tuple[UBSStrategy, ...],
) -> list[dict[str, object]]:
    return _explicit_pilot_specs(
        set_directory, strategies, RISK_MODE_PILOT_VARIATIONS
    )


def _risk_selector_pilot_specs(
    set_directory: Path,
    strategies: tuple[UBSStrategy, ...],
) -> list[dict[str, object]]:
    return _explicit_pilot_specs(
        set_directory, strategies, RISK_SELECTOR_PILOT_VARIATIONS
    )


def _build_pilot_sets(
    specs: list[dict[str, object]], derived_directory: Path
) -> None:
    derived_directory.mkdir(parents=True, exist_ok=False)
    for spec in specs:
        source = Path(str(spec["source_file"]))
        raw_overrides = spec.get("overrides")
        if not isinstance(raw_overrides, dict) or not raw_overrides:
            raise UBSRiskSensitivityError(
                f"Pilot overrides are missing: {spec['case_id']}"
            )
        overrides = {str(name): str(value) for name, value in raw_overrides.items()}
        source_text = read_set_text(source)
        derived_text = override_set_values(source_text, overrides)
        source_values = parse_set_values(source_text)
        derived_values = parse_set_values(derived_text)
        changed = {
            name
            for name in set(source_values) | set(derived_values)
            if source_values.get(name) != derived_values.get(name)
        }
        expected = {str(name) for name in spec["expected_changed_inputs"]}
        if changed != expected:
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


def _validate_risk_mode_exposure(
    specs: list[dict[str, object]],
    baselines: dict[str, dict[str, object]],
) -> None:
    for spec in specs:
        strategy_id = str(spec["strategy_id"])
        baseline = baselines[strategy_id]
        baseline_volumes = baseline["entry_volume_audit"]
        overrides = spec["overrides"]
        if not isinstance(baseline_volumes, dict) or not isinstance(overrides, dict):
            raise UBSRiskSensitivityError(
                f"Cannot validate risk-mode exposure: {spec['case_id']}"
            )
        if "StartLots" in overrides:
            maximum = Decimal(str(baseline_volumes["maximum_entry_volume"]))
            requested = Decimal(str(overrides["StartLots"]))
            if maximum <= 0 or requested / maximum >= Decimal("2"):
                raise UBSRiskSensitivityError(
                    f"Fixed-lot pilot reaches or exceeds 2x baseline exposure: "
                    f"{spec['case_id']}: {requested}/{maximum}"
                )
        if "MaxLots" in overrides:
            maximum = Decimal(str(baseline_volumes["maximum_entry_volume"]))
            requested_cap = Decimal(str(overrides["MaxLots"]))
            if maximum <= 0 or requested_cap / maximum >= Decimal("2"):
                raise UBSRiskSensitivityError(
                    f"Pilot MaxLots reaches or exceeds 2x baseline exposure: "
                    f"{spec['case_id']}: {requested_cap}/{maximum}"
                )
        if "MaxRiskPerStrategy_Value" in overrides:
            originals = json.loads(str(spec["original_value"]))
            original = Decimal(str(originals["MaxRiskPerStrategy_Value"]))
            requested = Decimal(str(overrides["MaxRiskPerStrategy_Value"]))
            if requested >= original:
                raise UBSRiskSensitivityError(
                    f"Risk-cap pilot must reduce the original value: "
                    f"{spec['case_id']}: {original} -> {requested}"
                )


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


def _limited_annual_executor_factory(scenario: Scenario) -> MT5Executor:
    return MT5Executor(scenario, process_runner=run_below_normal_four_cpus)


def run_ubs_risk_sensitivity_pilot(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = _limited_executor_factory,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    progress: Callable[[str], None] = print,
    spec_factory: Callable[
        [Path, tuple[UBSStrategy, ...]], list[dict[str, object]]
    ] = _pilot_specs,
    spec_validator: Callable[
        [list[dict[str, object]], dict[str, dict[str, object]]], None
    ]
    | None = None,
    output_leaf: str = "pilot",
    pilot_kind: str = "numeric_sensitivity",
) -> Path:
    faithful, strategies = validate_quarterly_prerequisites(
        settings, faithful_manifest_path, faithful_validation_run
    )
    if settings.model != 4 or settings.execution_mode != 0:
        raise UBSRiskSensitivityError("Pilot requires real ticks and No Delay")
    baselines = _load_baselines(faithful_validation_run, settings)
    specs = spec_factory(faithful_manifest_path.parent, strategies)
    if spec_validator is not None:
        spec_validator(specs, baselines)
    started = now_factory()
    output_root = settings.output_root.parent / "risk_sensitivity" / output_leaf
    output_root.mkdir(parents=True, exist_ok=True)
    run_directory = output_root / _run_id(started)
    run_directory.mkdir(exist_ok=False)
    derived_directory = run_directory / "derived_sets"
    _build_pilot_sets(specs, derived_directory)
    manifest_path = run_directory / "run_manifest.json"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_directory.name,
        "pilot_kind": pilot_kind,
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


def run_ubs_risk_mode_pilot(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = _limited_executor_factory,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    progress: Callable[[str], None] = print,
) -> Path:
    """Test fixed-lot and per-strategy risk-cap paths with bounded exposure."""
    return run_ubs_risk_sensitivity_pilot(
        settings,
        faithful_manifest_path,
        faithful_validation_run,
        executor_factory=executor_factory,
        case_runner=case_runner,
        now_factory=now_factory,
        progress=progress,
        spec_factory=_risk_mode_pilot_specs,
        spec_validator=_validate_risk_mode_exposure,
        output_leaf="mode_pilot",
        pilot_kind="risk_mode",
    )


def run_ubs_risk_selector_pilot(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = _limited_executor_factory,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    progress: Callable[[str], None] = print,
) -> Path:
    """Test Risk=0 with bounded StartLots and MaxLots values."""
    return run_ubs_risk_sensitivity_pilot(
        settings,
        faithful_manifest_path,
        faithful_validation_run,
        executor_factory=executor_factory,
        case_runner=case_runner,
        now_factory=now_factory,
        progress=progress,
        spec_factory=_risk_selector_pilot_specs,
        spec_validator=_validate_risk_mode_exposure,
        output_leaf="selector_pilot",
        pilot_kind="risk_selector",
    )


def _load_annual_baselines(
    model_run: Path,
    strategies: tuple[UBSStrategy, ...],
    settings: UBSSmokeSettings,
) -> tuple[dict[str, dict[str, object]], Path]:
    manifest_path = model_run.resolve() / "suite_manifest.json"
    manifest = _load_json(manifest_path, label="annual model-comparison manifest")
    expected_top_level = {
        "status": "success",
        "from_date": ANNUAL_FROM_DATE,
        "to_date": ANNUAL_TO_DATE,
        "execution_mode": 0,
        "deposit": settings.deposit,
        "currency": settings.currency,
        "leverage": settings.leverage,
    }
    mismatches = {
        name: (manifest.get(name), expected)
        for name, expected in expected_top_level.items()
        if manifest.get(name) != expected
    }
    if mismatches:
        raise UBSRiskSensitivityError(
            f"Annual baseline conditions do not match: {mismatches}"
        )
    raw_results = manifest.get("results")
    if not isinstance(raw_results, list):
        raise UBSRiskSensitivityError("Annual baseline results must be a list")
    real_results = [
        result
        for result in raw_results
        if isinstance(result, dict)
        and result.get("status") == "success"
        and int(result.get("model", -1)) == 4
    ]
    expected_ids = {strategy.strategy_id for strategy in strategies}
    result_ids = {str(result.get("strategy_id", "")) for result in real_results}
    if len(real_results) != len(strategies) or result_ids != expected_ids:
        raise UBSRiskSensitivityError(
            "Annual baseline must contain one successful real-tick result per strategy"
        )
    baselines: dict[str, dict[str, object]] = {}
    for result in real_results:
        strategy_id = str(result["strategy_id"])
        report = Path(str(result["report_file"]))
        if not report.is_file():
            raise UBSRiskSensitivityError(
                f"Annual baseline report does not exist: {strategy_id}: {report}"
            )
        enriched = dict(result)
        enriched["entry_volume_audit"] = parse_entry_volume_audit(report).to_dict()
        baselines[strategy_id] = enriched
    return baselines, manifest_path


def _annual_candidate_specs(
    set_directory: Path,
    strategies: tuple[UBSStrategy, ...],
    candidate_lots: Mapping[str, str] = ANNUAL_CANDIDATE_LOTS,
) -> list[dict[str, object]]:
    configured_ids = {strategy.strategy_id for strategy in strategies}
    if not candidate_lots or not set(candidate_lots).issubset(configured_ids):
        raise UBSRiskSensitivityError(
            "Annual candidate lots must select configured strategies"
        )
    variations = tuple(
        PilotOverrideVariation(
            case_id=f"{strategy.strategy_id}_fixed_{lot.replace('.', '')}",
            strategy_id=strategy.strategy_id,
            overrides=(
                ("Risk", "0"),
                ("AdjustLotsizeToVariableValues", "false"),
                ("StartLots", lot),
                ("MaxLots", lot),
            ),
        )
        for strategy in strategies
        if strategy.strategy_id in candidate_lots
        for lot in (candidate_lots[strategy.strategy_id],)
    )
    return _explicit_pilot_specs(set_directory, strategies, variations)


def _validate_annual_candidate_exposure(
    specs: list[dict[str, object]],
    baselines: dict[str, dict[str, object]],
    *,
    maximum_baseline_multiple: Decimal = Decimal("2"),
) -> None:
    for spec in specs:
        strategy_id = str(spec["strategy_id"])
        baseline_volumes = baselines[strategy_id]["entry_volume_audit"]
        overrides = spec["overrides"]
        if not isinstance(baseline_volumes, dict) or not isinstance(overrides, dict):
            raise UBSRiskSensitivityError(
                f"Cannot validate annual candidate exposure: {spec['case_id']}"
            )
        baseline_maximum = Decimal(
            str(baseline_volumes["maximum_entry_volume"])
        )
        candidate = Decimal(str(overrides["StartLots"]))
        cap = Decimal(str(overrides["MaxLots"]))
        if candidate < Decimal("0.01") or cap != candidate:
            raise UBSRiskSensitivityError(
                f"Invalid fixed-lot candidate: {spec['case_id']}: {candidate}/{cap}"
            )
        if (
            baseline_maximum <= 0
            or candidate / baseline_maximum > maximum_baseline_multiple
        ):
            raise UBSRiskSensitivityError(
                f"Annual candidate exceeds {maximum_baseline_multiple}x "
                f"baseline exposure: "
                f"{spec['case_id']}: {candidate}/{baseline_maximum}"
            )


def _target_dd_status(value: object) -> str:
    drawdown = float(value)
    if drawdown < TARGET_DD_MIN:
        return "below_target"
    if drawdown > TARGET_DD_MAX:
        return "above_target"
    return "within_target"


def _archive_case(path: Path, value: datetime) -> None:
    if not path.exists():
        return
    destination = path.with_name(
        f"{path.name}_interrupted_{value.strftime('%Y%m%dT%H%M%S%f')}"
    )
    path.replace(destination)


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_ubs_same_risk_annual_candidates(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    model_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = _limited_annual_executor_factory,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    resume_directory: Path | None = None,
    progress: Callable[[str], None] = print,
    candidate_lots: Mapping[str, str] = ANNUAL_CANDIDATE_LOTS,
    output_subdirectory: str = "annual_candidates",
    suite_kind: str = "initial_candidates",
    summary_stem: str = "annual_candidate_summary",
    maximum_baseline_multiple: Decimal = Decimal("2"),
    source_initial_candidate_run: Path | None = None,
) -> Path:
    """Run bounded fixed-lot candidates over the audited one-year range."""
    faithful, strategies = validate_quarterly_prerequisites(
        settings, faithful_manifest_path, faithful_validation_run
    )
    baselines, baseline_manifest_path = _load_annual_baselines(
        model_run, strategies, settings
    )
    source_initial_manifest: Path | None = None
    if source_initial_candidate_run is not None:
        source_initial_manifest = (
            source_initial_candidate_run.resolve() / "run_manifest.json"
        )
        source_payload = _load_json(
            source_initial_manifest, label="initial annual candidate manifest"
        )
        if source_payload.get("status") != "success":
            raise UBSRiskSensitivityError(
                "Initial annual candidate run must be successful"
            )
        source_results = source_payload.get("results")
        if not isinstance(source_results, list) or len(source_results) != len(
            ANNUAL_CANDIDATE_LOTS
        ):
            raise UBSRiskSensitivityError(
                "Initial annual candidate run must contain all seven results"
            )
    output_root = (
        settings.output_root.parent / "risk_sensitivity" / output_subdirectory
    )
    output_root.mkdir(parents=True, exist_ok=True)
    started = now_factory()

    if resume_directory is None:
        run_directory = output_root / _run_id(started)
        run_directory.mkdir(exist_ok=False)
        derived_directory = run_directory / "derived_sets"
        specs = _annual_candidate_specs(
            faithful_manifest_path.parent, strategies, candidate_lots
        )
        _validate_annual_candidate_exposure(
            specs,
            baselines,
            maximum_baseline_multiple=maximum_baseline_multiple,
        )
        _build_pilot_sets(specs, derived_directory)
        manifest: dict[str, object] = {
            "schema_version": 1,
            "run_id": run_directory.name,
            "suite_kind": suite_kind,
            "status": "running",
            "started_at_jst": started.isoformat(timespec="seconds"),
            "finished_at_jst": None,
            "conditions": {
                "symbol": settings.symbol,
                "period": settings.period,
                "from_date": ANNUAL_FROM_DATE,
                "to_date": ANNUAL_TO_DATE,
                "model": 4,
                "execution_mode": 0,
                "deposit": settings.deposit,
                "currency": settings.currency,
                "leverage": settings.leverage,
            },
            "target_equity_drawdown_percent": {
                "minimum": TARGET_DD_MIN,
                "target": 5.0,
                "maximum": TARGET_DD_MAX,
            },
            "resource_limits": {
                "priority": "BelowNormal",
                "logical_cpu_count": 4,
                "affinity_mask": "0xf",
                "timeout_seconds": 7200,
            },
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "annual_baseline_manifest": str(baseline_manifest_path),
            "annual_baseline_manifest_sha256": sha256_file(baseline_manifest_path),
            "source_initial_candidate_manifest": (
                str(source_initial_manifest) if source_initial_manifest else None
            ),
            "source_initial_candidate_manifest_sha256": (
                sha256_file(source_initial_manifest)
                if source_initial_manifest
                else None
            ),
            "source_smoke_run": faithful["source_smoke_run"],
            "candidate_variations": specs,
            "candidate_lots": dict(candidate_lots),
            "cases_requested": len(specs),
            "results": [],
            "error": None,
        }
        manifest_path = run_directory / "run_manifest.json"
        _write_manifest(manifest_path, manifest)
    else:
        run_directory = resume_directory.resolve()
        manifest_path = run_directory / "run_manifest.json"
        manifest = _load_json(manifest_path, label="same-risk annual manifest")
        expected = {
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "annual_baseline_manifest": str(baseline_manifest_path),
            "annual_baseline_manifest_sha256": sha256_file(baseline_manifest_path),
            "suite_kind": suite_kind,
            "candidate_lots": dict(candidate_lots),
            "source_initial_candidate_manifest": (
                str(source_initial_manifest) if source_initial_manifest else None
            ),
            "source_initial_candidate_manifest_sha256": (
                sha256_file(source_initial_manifest)
                if source_initial_manifest
                else None
            ),
            "cases_requested": len(candidate_lots),
        }
        mismatches = {
            name: (manifest.get(name), value)
            for name, value in expected.items()
            if manifest.get(name) != value
        }
        if mismatches:
            raise UBSRiskSensitivityError(
                f"Resume manifest does not match annual candidates: {mismatches}"
            )
        raw_specs = manifest.get("candidate_variations")
        raw_results = manifest.get("results")
        if not isinstance(raw_specs, list) or not isinstance(raw_results, list):
            raise UBSRiskSensitivityError(
                "Annual resume manifest is missing specs or results"
            )
        specs = raw_specs
        for spec in specs:
            derived = Path(str(spec["derived_file"]))
            if not derived.is_file() or sha256_file(derived) != spec["derived_sha256"]:
                raise UBSRiskSensitivityError(
                    f"Derived annual candidate changed: {spec['case_id']}"
                )
        manifest["results"] = [
            result
            for result in raw_results
            if isinstance(result, dict) and result.get("status") == "success"
        ]
        manifest["status"] = "running"
        manifest["finished_at_jst"] = None
        manifest["error"] = None
        manifest["resource_limits"] = {
            "priority": "BelowNormal",
            "logical_cpu_count": 4,
            "affinity_mask": "0xf",
            "timeout_seconds": 7200,
        }
        _write_manifest(manifest_path, manifest)

    logger = logging.getLogger(
        f"mt5_ea_validator.ubs_same_risk_annual.{run_directory.name}"
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(run_directory / "execution.log", encoding="utf-8")
    logger.addHandler(handler)
    results = manifest["results"]
    assert isinstance(results, list)
    completed = {
        str(result["case_id"])
        for result in results
        if isinstance(result, dict) and result.get("status") == "success"
    }
    total = len(specs)
    try:
        for case_number, spec in enumerate(specs, start=1):
            case_id = str(spec["case_id"])
            if case_id in completed:
                progress(f"SKIP [{case_number}/{total}] {case_id}")
                continue
            strategy_id = str(spec["strategy_id"])
            derived = UBSStrategy(
                strategy_id=strategy_id,
                file_name=str(spec["derived_file_name"]),
            )
            annual_settings = replace(
                settings,
                scenario_id=f"ubs_same_risk_annual_{strategy_id}",
                set_directory=Path(str(spec["derived_file"])).parent,
                from_date=ANNUAL_FROM_DATE,
                to_date=ANNUAL_TO_DATE,
                model=4,
                execution_mode=0,
                timeout_seconds=max(settings.timeout_seconds, 7200),
                strategies=(derived,),
            )
            scenario = replace(
                _scenario(annual_settings, derived),
                scenario_id=f"ubs_same_risk_annual_{strategy_id}",
                wf="SAME_RISK_1Y",
                staged_set_name=(
                    f"UBS_SAME_RISK_1Y_{strategy_id}_"
                    f"{str(spec['derived_sha256'])[:8]}.set"
                ),
            )
            output = run_directory / f"{case_number:02d}_{case_id}"
            _archive_case(output, now_factory())
            progress(f"START [{case_number}/{total}] {case_id}")
            result = case_runner(
                annual_settings,
                derived,
                scenario,
                output,
                executor_factory=executor_factory,
                require_report_covered_inputs=True,
                max_generated_fallback_ratio=MAX_GENERATED_FALLBACK_RATIO,
            )
            if result.get("status") != "success":
                raise UBSRiskSensitivityCampaignError(
                    f"Same-risk annual candidate failed: {case_id}", run_directory
                )
            result.update(
                {
                    "case_number": case_number,
                    "case_id": case_id,
                    "strategy_id": strategy_id,
                    "from_date": ANNUAL_FROM_DATE,
                    "to_date": ANNUAL_TO_DATE,
                    "model": 4,
                    "model_label": "Every tick based on real ticks",
                    "candidate_lot": candidate_lots[strategy_id],
                    "parameter": spec["parameter"],
                    "factor": spec["factor"],
                    "original_value": spec["original_value"],
                    "updated_value": spec["updated_value"],
                    "source_set_sha256": spec["source_sha256"],
                    "derived_set_sha256": spec["derived_sha256"],
                    "entry_volume_audit": parse_entry_volume_audit(
                        Path(str(result["report_file"]))
                    ).to_dict(),
                    "target_dd_status": _target_dd_status(
                        result["metrics"]["equity_drawdown_percent"]
                    ),
                    "baseline_deal_sequence_same": (
                        result["deal_audit"]["deal_sequence_sha256"]
                        == baselines[strategy_id]["deal_audit"][
                            "deal_sequence_sha256"
                        ]
                    ),
                }
            )
            result["deal_sequence_same"] = result["baseline_deal_sequence_same"]
            result_path = output / "result.json"
            _write_json(result_path, result)
            results.append(result)
            _write_manifest(manifest_path, manifest)
            progress(
                f"PASS [{case_number}/{total}] {case_id} "
                f"DD={result['metrics']['equity_drawdown_percent']}% "
                f"{result['target_dd_status']}"
            )
        if len(results) != total:
            raise UBSRiskSensitivityCampaignError(
                f"Same-risk annual suite completed {len(results)} of {total} cases",
                run_directory,
            )
        rows = _summary_rows(results, baselines)
        for row, result in zip(rows, results, strict=True):
            row["candidate_lot"] = result["candidate_lot"]
            row["target_dd_status"] = result["target_dd_status"]
        _write_json(
            run_directory / f"{summary_stem}.json",
            {"schema_version": 1, "case_count": len(rows), "results": rows},
        )
        _write_csv(run_directory / f"{summary_stem}.csv", rows)
        manifest["status"] = "success"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        logger.exception("same-risk annual candidate suite failed")
        if isinstance(exc, UBSRiskSensitivityCampaignError):
            raise
        raise UBSRiskSensitivityCampaignError(str(exc), run_directory) from exc
    finally:
        manifest["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        _write_manifest(manifest_path, manifest)
        handler.close()
        logger.removeHandler(handler)
    return run_directory


def run_ubs_same_risk_annual_adjustments(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    model_run: Path,
    initial_candidate_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = _limited_annual_executor_factory,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    resume_directory: Path | None = None,
    progress: Callable[[str], None] = print,
) -> Path:
    """Measure the adjacent lot steps needed to finalize five-percent DD choices."""
    return run_ubs_same_risk_annual_candidates(
        settings,
        faithful_manifest_path,
        faithful_validation_run,
        model_run,
        executor_factory=executor_factory,
        case_runner=case_runner,
        now_factory=now_factory,
        resume_directory=resume_directory,
        progress=progress,
        candidate_lots=ANNUAL_ADJUSTMENT_LOTS,
        output_subdirectory="annual_adjustments",
        suite_kind="adjacent_lot_adjustments",
        summary_stem="annual_adjustment_summary",
        maximum_baseline_multiple=Decimal("3"),
        source_initial_candidate_run=initial_candidate_run,
    )


def build_ubs_same_risk_final_selection(
    initial_candidate_run: Path,
    adjustment_run: Path,
    *,
    output_directory: Path | None = None,
) -> Path:
    """Select conservative fixed lots from the audited annual candidate runs."""
    initial_manifest_path = initial_candidate_run.resolve() / "run_manifest.json"
    adjustment_manifest_path = adjustment_run.resolve() / "run_manifest.json"
    initial = _load_json(initial_manifest_path, label="initial candidate manifest")
    adjustment = _load_json(
        adjustment_manifest_path, label="annual adjustment manifest"
    )
    if initial.get("status") != "success" or adjustment.get("status") != "success":
        raise UBSRiskSensitivityError(
            "Both annual candidate runs must be successful before selection"
        )
    source_path = adjustment.get("source_initial_candidate_manifest")
    source_hash = adjustment.get("source_initial_candidate_manifest_sha256")
    if (
        source_path != str(initial_manifest_path)
        or source_hash != sha256_file(initial_manifest_path)
    ):
        raise UBSRiskSensitivityError(
            "Adjustment run does not reference the supplied initial candidate run"
        )
    raw_initial_results = initial.get("results")
    raw_adjustment_results = adjustment.get("results")
    if not isinstance(raw_initial_results, list) or not isinstance(
        raw_adjustment_results, list
    ):
        raise UBSRiskSensitivityError("Annual candidate results must be lists")
    variation_by_case: dict[str, dict[str, object]] = {}
    for manifest in (initial, adjustment):
        raw_variations = manifest.get("candidate_variations")
        if not isinstance(raw_variations, list):
            raise UBSRiskSensitivityError(
                "Annual candidate manifest is missing candidate variations"
            )
        for raw_variation in raw_variations:
            if not isinstance(raw_variation, dict):
                raise UBSRiskSensitivityError("Candidate variation must be an object")
            case_id = str(raw_variation.get("case_id", ""))
            if not case_id or case_id in variation_by_case:
                raise UBSRiskSensitivityError(
                    f"Duplicate or missing candidate variation: {case_id}"
                )
            variation_by_case[case_id] = raw_variation

    candidates: dict[str, list[dict[str, object]]] = {}
    for raw in [*raw_initial_results, *raw_adjustment_results]:
        if not isinstance(raw, dict) or raw.get("status") != "success":
            raise UBSRiskSensitivityError("All selectable candidates must be successful")
        strategy_id = str(raw.get("strategy_id", ""))
        if strategy_id not in ANNUAL_CANDIDATE_LOTS:
            raise UBSRiskSensitivityError(
                f"Unknown annual candidate strategy: {strategy_id}"
            )
        lot = Decimal(str(raw.get("candidate_lot", "")))
        volumes = raw.get("entry_volume_audit")
        metrics = raw.get("metrics")
        if not isinstance(volumes, dict) or not isinstance(metrics, dict):
            raise UBSRiskSensitivityError(
                f"Candidate audit fields are missing: {strategy_id}"
            )
        if (
            Decimal(str(volumes.get("minimum_entry_volume", ""))) != lot
            or Decimal(str(volumes.get("maximum_entry_volume", ""))) != lot
            or raw.get("baseline_deal_sequence_same") is not True
        ):
            raise UBSRiskSensitivityError(
                f"Candidate failed fixed-lot or deal-sequence audit: {strategy_id}"
            )
        case_id = str(raw.get("case_id", ""))
        variation = variation_by_case.get(case_id)
        if variation is None:
            raise UBSRiskSensitivityError(
                f"Candidate variation is missing for result: {case_id}"
            )
        derived_file = Path(str(variation.get("derived_file", ""))).resolve()
        derived_sha256 = str(variation.get("derived_sha256", ""))
        if (
            not derived_file.is_file()
            or sha256_file(derived_file) != derived_sha256
            or raw.get("derived_set_sha256") != derived_sha256
        ):
            raise UBSRiskSensitivityError(
                f"Selected candidate set provenance failed: {case_id}"
            )
        raw["selected_set_file"] = str(derived_file)
        raw["selected_set_sha256"] = derived_sha256
        candidates.setdefault(strategy_id, []).append(raw)
    if set(candidates) != set(ANNUAL_CANDIDATE_LOTS):
        raise UBSRiskSensitivityError(
            "Final selection requires candidates for all seven strategies"
        )

    selections: list[dict[str, object]] = []
    csv_rows: list[dict[str, object]] = []
    for strategy_id in ANNUAL_CANDIDATE_LOTS:
        strategy_candidates = candidates[strategy_id]

        def drawdown(candidate: dict[str, object]) -> float:
            metrics = candidate["metrics"]
            assert isinstance(metrics, dict)
            return float(metrics["equity_drawdown_percent"])

        within = [
            candidate
            for candidate in strategy_candidates
            if TARGET_DD_MIN <= drawdown(candidate) <= TARGET_DD_MAX
        ]
        under_upper_limit = [
            candidate
            for candidate in strategy_candidates
            if drawdown(candidate) <= TARGET_DD_MAX
        ]
        if within:
            pool = within
            selection_status = "within_target"
            reason = "selected_from_4.5_to_5.5_percent_target_band"
        elif under_upper_limit:
            pool = under_upper_limit
            selection_status = "lot_granularity_limited_below"
            reason = "upper_adjacent_lot_exceeds_5.5_percent_limit"
        else:
            pool = strategy_candidates
            selection_status = "minimum_lot_limited_above"
            reason = "minimum_tested_lot_still_exceeds_5.5_percent_limit"
        selected = min(
            pool,
            key=lambda candidate: (
                abs(drawdown(candidate) - 5.0),
                drawdown(candidate),
                Decimal(str(candidate["candidate_lot"])),
            ),
        )
        metrics = selected["metrics"]
        assert isinstance(metrics, dict)
        tested = sorted(
            (
                {
                    "lot": str(candidate["candidate_lot"]),
                    "equity_drawdown_percent": drawdown(candidate),
                    "net_profit": candidate["metrics"]["net_profit"],
                }
                for candidate in strategy_candidates
            ),
            key=lambda item: Decimal(str(item["lot"])),
        )
        selection = {
            "strategy_id": strategy_id,
            "selected_lot": str(selected["candidate_lot"]),
            "equity_drawdown_percent": drawdown(selected),
            "net_profit": metrics["net_profit"],
            "trades": metrics["trades"],
            "profit_factor": metrics.get("profit_factor"),
            "selection_status": selection_status,
            "selection_reason": reason,
            "candidate_count": len(strategy_candidates),
            "tested_candidates": tested,
            "selected_result_file": str(
                Path(str(selected["report_file"])).with_name("result.json")
            ),
            "selected_set_file": selected["selected_set_file"],
            "selected_set_sha256": selected["selected_set_sha256"],
        }
        selections.append(selection)
        csv_rows.append(
            {
                key: value
                for key, value in selection.items()
                if key != "tested_candidates"
            }
            | {"tested_candidates": json.dumps(tested, ensure_ascii=False)}
        )

    destination = output_directory or adjustment_run.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(
        destination / "final_selection.json",
        {
            "schema_version": 2,
            "target_equity_drawdown_percent": {
                "minimum": TARGET_DD_MIN,
                "target": 5.0,
                "maximum": TARGET_DD_MAX,
            },
            "initial_candidate_manifest": str(initial_manifest_path),
            "initial_candidate_manifest_sha256": sha256_file(initial_manifest_path),
            "adjustment_manifest": str(adjustment_manifest_path),
            "adjustment_manifest_sha256": sha256_file(adjustment_manifest_path),
            "strategy_count": len(selections),
            "selections": selections,
        },
    )
    _write_csv(destination / "final_selection.csv", csv_rows)
    return destination


def _load_same_risk_quarterly_selection(
    settings: UBSSmokeSettings,
    selection_path: Path,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    _, configured_strategies = validate_quarterly_prerequisites(
        settings, faithful_manifest_path, faithful_validation_run
    )
    selection_path = selection_path.resolve()
    selection = _load_json(selection_path, label="same-risk final selection")
    if selection.get("schema_version") != 2:
        raise UBSRiskSensitivityError("Same-risk final selection schema must be 2")
    for path_key, hash_key in (
        ("initial_candidate_manifest", "initial_candidate_manifest_sha256"),
        ("adjustment_manifest", "adjustment_manifest_sha256"),
    ):
        source = Path(str(selection.get(path_key, ""))).resolve()
        if not source.is_file() or sha256_file(source) != selection.get(hash_key):
            raise UBSRiskSensitivityError(
                f"Same-risk selection source changed: {path_key}"
            )
    raw_selections = selection.get("selections")
    if not isinstance(raw_selections, list) or len(raw_selections) != 7:
        raise UBSRiskSensitivityError(
            "Same-risk final selection must contain seven strategies"
        )
    selection_by_id = {
        str(item.get("strategy_id", "")): item
        for item in raw_selections
        if isinstance(item, dict)
    }
    expected_ids = [strategy.strategy_id for strategy in configured_strategies]
    if set(selection_by_id) != set(expected_ids):
        raise UBSRiskSensitivityError(
            "Same-risk final selection and configured strategies differ"
        )
    audited: list[dict[str, object]] = []
    for strategy_id in expected_ids:
        item = selection_by_id[strategy_id]
        lot = str(item.get("selected_lot", ""))
        set_file = Path(str(item.get("selected_set_file", ""))).resolve()
        digest = str(item.get("selected_set_sha256", ""))
        if not set_file.is_file() or sha256_file(set_file) != digest:
            raise UBSRiskSensitivityError(
                f"Selected fixed-lot set changed: {strategy_id}: {set_file}"
            )
        values = parse_set_values(read_set_text(set_file))
        expected_values = {
            "Risk": "0",
            "AdjustLotsizeToVariableValues": "false",
            "StartLots": lot,
            "MaxLots": lot,
        }
        mismatches = {
            name: (values.get(name), expected)
            for name, expected in expected_values.items()
            if values.get(name) != expected
        }
        if mismatches:
            raise UBSRiskSensitivityError(
                f"Selected fixed-lot inputs changed: {strategy_id}: {mismatches}"
            )
        audited.append(
            {
                **item,
                "selected_set_file": str(set_file),
                "selected_set_sha256": digest,
            }
        )
    return selection, audited


def _same_risk_quarterly_rows(
    results: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        metrics = result["metrics"]
        deals = result["deal_audit"]
        volumes = result["entry_volume_audit"]
        assert isinstance(metrics, dict)
        assert isinstance(deals, dict)
        assert isinstance(volumes, dict)
        rows.append(
            {
                "wf": result["wf"],
                "strategy_id": result["strategy_id"],
                "from_date": result["from_date"],
                "to_date": result["to_date"],
                "selected_lot": result["selected_lot"],
                "selection_status": result["selection_status"],
                "net_profit": metrics["net_profit"],
                "trades": metrics["trades"],
                "profit_factor": metrics["profit_factor"],
                "recovery_factor": metrics["recovery_factor"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "equity_drawdown_percent": metrics[
                    "equity_drawdown_percent"
                ],
                "dd_warning_above_7_5_percent": result[
                    "dd_warning_above_7_5_percent"
                ],
                "minimum_entry_volume": volumes["minimum_entry_volume"],
                "maximum_entry_volume": volumes["maximum_entry_volume"],
                "deal_count": deals["deal_count"],
                "deal_sequence_sha256": deals["deal_sequence_sha256"],
                "commission_total": deals["commission_total"],
                "swap_total": deals["swap_total"],
                "deal_profit_total": deals["deal_profit_total"],
            }
        )
    return rows


def run_ubs_same_risk_quarterly_suite(
    settings: UBSSmokeSettings,
    selection_path: Path,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = _limited_annual_executor_factory,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    resume_directory: Path | None = None,
    progress: Callable[[str], None] = print,
) -> Path:
    """Run the selected fixed lots independently across WF1-WF4 with real ticks."""
    selection_path = selection_path.resolve()
    selection, selected_strategies = _load_same_risk_quarterly_selection(
        settings,
        selection_path,
        faithful_manifest_path,
        faithful_validation_run,
    )
    strategy_manifest = [
        {
            "strategy_id": item["strategy_id"],
            "selected_lot": item["selected_lot"],
            "selection_status": item["selection_status"],
            "selected_set_file": item["selected_set_file"],
            "selected_set_sha256": item["selected_set_sha256"],
            "selected_annual_equity_drawdown_percent": item[
                "equity_drawdown_percent"
            ],
        }
        for item in selected_strategies
    ]
    output_root = (
        settings.output_root.parent
        / "risk_sensitivity"
        / "quarterly_real_ticks"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    started = now_factory()
    if resume_directory is None:
        run_directory = output_root / _run_id(started)
        run_directory.mkdir(exist_ok=False)
        manifest: dict[str, object] = {
            "schema_version": 1,
            "run_id": run_directory.name,
            "status": "running",
            "started_at_jst": started.isoformat(timespec="seconds"),
            "finished_at_jst": None,
            "conditions": {
                "symbol": settings.symbol,
                "period": settings.period,
                "model": 4,
                "model_label": "Every tick based on real ticks",
                "execution_mode": 0,
                "deposit": settings.deposit,
                "currency": settings.currency,
                "leverage": settings.leverage,
            },
            "dd_warning_percent": SAME_RISK_QUARTERLY_DD_WARNING,
            "tick_data_quality_limits": {
                "max_generated_fallback_ratio": (
                    SAME_RISK_QUARTERLY_MAX_FALLBACK_RATIO
                ),
                "max_generated_fallback_minutes": (
                    SAME_RISK_QUARTERLY_MAX_FALLBACK_MINUTES
                ),
            },
            "resource_limits": {
                "priority": "BelowNormal",
                "logical_cpu_count": 4,
                "affinity_mask": "0xf",
                "timeout_seconds": 7200,
            },
            "selection_file": str(selection_path),
            "selection_file_sha256": sha256_file(selection_path),
            "selection_source_schema_version": selection["schema_version"],
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "wfs": list(EXPECTED_WFS),
            "periods": {
                wf: {"from_date": WF_PERIODS[wf][0], "to_date": WF_PERIODS[wf][1]}
                for wf in EXPECTED_WFS
            },
            "strategies": strategy_manifest,
            "cases_requested": len(EXPECTED_WFS) * len(selected_strategies),
            "results": [],
            "error": None,
        }
        manifest_path = run_directory / "run_manifest.json"
        _write_manifest(manifest_path, manifest)
    else:
        run_directory = resume_directory.resolve()
        manifest_path = run_directory / "run_manifest.json"
        manifest = _load_json(
            manifest_path, label="same-risk quarterly manifest"
        )
        expected = {
            "selection_file": str(selection_path),
            "selection_file_sha256": sha256_file(selection_path),
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "wfs": list(EXPECTED_WFS),
            "strategies": strategy_manifest,
            "cases_requested": len(EXPECTED_WFS) * len(selected_strategies),
        }
        mismatches = {
            name: (manifest.get(name), value)
            for name, value in expected.items()
            if manifest.get(name) != value
        }
        if mismatches:
            raise UBSRiskSensitivityError(
                f"Resume manifest does not match quarterly suite: {mismatches}"
            )
        raw_results = manifest.get("results")
        if not isinstance(raw_results, list):
            raise UBSRiskSensitivityError("Quarterly results must be a list")
        manifest["results"] = [
            result
            for result in raw_results
            if isinstance(result, dict) and result.get("status") == "success"
        ]
        manifest["status"] = "running"
        manifest["finished_at_jst"] = None
        manifest["error"] = None
        manifest["resource_limits"] = {
            "priority": "BelowNormal",
            "logical_cpu_count": 4,
            "affinity_mask": "0xf",
            "timeout_seconds": 7200,
        }
        manifest["tick_data_quality_limits"] = {
            "max_generated_fallback_ratio": (
                SAME_RISK_QUARTERLY_MAX_FALLBACK_RATIO
            ),
            "max_generated_fallback_minutes": (
                SAME_RISK_QUARTERLY_MAX_FALLBACK_MINUTES
            ),
        }
        _write_manifest(manifest_path, manifest)

    logger = logging.getLogger(
        f"mt5_ea_validator.ubs_same_risk_quarterly.{run_directory.name}"
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(run_directory / "execution.log", encoding="utf-8")
    logger.addHandler(handler)
    results = manifest["results"]
    assert isinstance(results, list)
    completed = {
        (str(result["wf"]), str(result["strategy_id"]))
        for result in results
        if isinstance(result, dict) and result.get("status") == "success"
    }
    total = len(EXPECTED_WFS) * len(selected_strategies)
    try:
        case_number = 0
        for wf in EXPECTED_WFS:
            from_date, to_date = WF_PERIODS[wf]
            for selected in selected_strategies:
                case_number += 1
                strategy_id = str(selected["strategy_id"])
                case_id = f"{wf}_{strategy_id}"
                if (wf, strategy_id) in completed:
                    progress(f"SKIP [{case_number}/{total}] {case_id}")
                    continue
                set_file = Path(str(selected["selected_set_file"]))
                strategy = UBSStrategy(
                    strategy_id=strategy_id,
                    file_name=set_file.name,
                )
                quarterly_settings = replace(
                    settings,
                    scenario_id=f"ubs_same_risk_{wf.lower()}_{strategy_id}",
                    set_directory=set_file.parent,
                    from_date=from_date,
                    to_date=to_date,
                    model=4,
                    execution_mode=0,
                    timeout_seconds=max(settings.timeout_seconds, 7200),
                    strategies=(strategy,),
                )
                scenario = replace(
                    _scenario(quarterly_settings, strategy),
                    scenario_id=f"ubs_same_risk_{wf.lower()}_{strategy_id}",
                    wf=wf,
                    staged_set_name=(
                        f"UBS_SAME_RISK_{wf}_{strategy_id}_"
                        f"{str(selected['selected_set_sha256'])[:8]}.set"
                    ),
                )
                output = run_directory / f"{case_number:02d}_{case_id}"
                _archive_case(output, now_factory())
                progress(f"START [{case_number}/{total}] {case_id}")
                result = case_runner(
                    quarterly_settings,
                    strategy,
                    scenario,
                    output,
                    executor_factory=executor_factory,
                    require_report_covered_inputs=True,
                    max_generated_fallback_ratio=(
                        SAME_RISK_QUARTERLY_MAX_FALLBACK_RATIO
                    ),
                )
                if result.get("status") != "success":
                    raise UBSRiskSensitivityCampaignError(
                        f"Same-risk quarterly case failed: {case_id}", run_directory
                    )
                tick_quality = result.get("tick_data_quality")
                fallback_minute_count: int | None = None
                if (
                    isinstance(tick_quality, dict)
                    and "fallback_minute_count" in tick_quality
                ):
                    raw_fallback_minutes = tick_quality["fallback_minute_count"]
                    fallback_minute_count = (
                        0
                        if raw_fallback_minutes is None
                        else int(raw_fallback_minutes)
                    )
                if (
                    not isinstance(tick_quality, dict)
                    or tick_quality.get("passed") is not True
                    or fallback_minute_count is None
                    or fallback_minute_count
                    > SAME_RISK_QUARTERLY_MAX_FALLBACK_MINUTES
                ):
                    raise UBSRiskSensitivityCampaignError(
                        f"Quarterly tick-data absolute limit failed: {case_id}",
                        run_directory,
                    )
                volume_audit = parse_entry_volume_audit(
                    Path(str(result["report_file"]))
                ).to_dict()
                selected_lot = Decimal(str(selected["selected_lot"]))
                if (
                    Decimal(str(volume_audit["minimum_entry_volume"]))
                    != selected_lot
                    or Decimal(str(volume_audit["maximum_entry_volume"]))
                    != selected_lot
                ):
                    raise UBSRiskSensitivityCampaignError(
                        f"Quarterly entry lot does not match selection: {case_id}",
                        run_directory,
                    )
                metrics = result["metrics"]
                assert isinstance(metrics, dict)
                warning = (
                    float(metrics["equity_drawdown_percent"])
                    > SAME_RISK_QUARTERLY_DD_WARNING
                )
                result.update(
                    {
                        "case_number": case_number,
                        "case_id": case_id,
                        "wf": wf,
                        "from_date": from_date,
                        "to_date": to_date,
                        "model": 4,
                        "model_label": "Every tick based on real ticks",
                        "selected_lot": str(selected["selected_lot"]),
                        "selection_status": selected["selection_status"],
                        "selected_set_sha256": selected[
                            "selected_set_sha256"
                        ],
                        "selected_annual_result_file": selected[
                            "selected_result_file"
                        ],
                        "selected_annual_equity_drawdown_percent": selected[
                            "equity_drawdown_percent"
                        ],
                        "entry_volume_audit": volume_audit,
                        "dd_warning_above_7_5_percent": warning,
                    }
                )
                _write_json(output / "result.json", result)
                results.append(result)
                _write_manifest(manifest_path, manifest)
                progress(
                    f"PASS [{case_number}/{total}] {case_id} "
                    f"DD={metrics['equity_drawdown_percent']}% "
                    f"warning={warning}"
                )
        if len(results) != total:
            raise UBSRiskSensitivityCampaignError(
                f"Same-risk quarterly suite completed {len(results)} of {total} cases",
                run_directory,
            )
        rows = _same_risk_quarterly_rows(results)
        _write_json(
            run_directory / "quarterly_same_risk_summary.json",
            {
                "schema_version": 1,
                "model": 4,
                "model_label": "Every tick based on real ticks",
                "dd_warning_percent": SAME_RISK_QUARTERLY_DD_WARNING,
                "case_count": len(rows),
                "warning_count": sum(
                    bool(row["dd_warning_above_7_5_percent"]) for row in rows
                ),
                "results": rows,
            },
        )
        _write_csv(run_directory / "quarterly_same_risk_summary.csv", rows)
        manifest["status"] = "success"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        logger.exception("same-risk quarterly suite failed")
        if isinstance(exc, UBSRiskSensitivityCampaignError):
            raise
        raise UBSRiskSensitivityCampaignError(str(exc), run_directory) from exc
    finally:
        manifest["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        _write_manifest(manifest_path, manifest)
        handler.close()
        logger.removeHandler(handler)
    return run_directory
