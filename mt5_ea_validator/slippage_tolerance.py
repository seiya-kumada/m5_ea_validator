from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from mt5_ea_validator.configuration import Benchmark, Scenario
from mt5_ea_validator.mt5 import MT5Executor, TestExecution, sha256_file
from mt5_ea_validator.report import (
    DealAudit,
    ReportMetrics,
    check_benchmark,
    parse_deal_audit,
    parse_report,
)
from mt5_ea_validator.setfile import (
    override_set_values,
    parse_set_values,
    read_set_text,
    write_mt5_unicode,
)
from mt5_ea_validator.transaction_cost import metric_deltas


JST = timezone(timedelta(hours=9), "JST")
BASELINE_SLIPPAGE_POINTS = 100
DEFAULT_SLIPPAGE_POINTS = (0, 2, 5, 10, BASELINE_SLIPPAGE_POINTS)
EXPECTED_WFS = ("WF1", "WF2", "WF3", "WF4")
SLIPPAGE_INPUT = "InpSlippage"


class SlippageToleranceError(RuntimeError):
    """Raised when the Layer-2 slippage-tolerance campaign is invalid."""


class SlippageToleranceCampaignError(SlippageToleranceError):
    """Raised after preserving artifacts from an incomplete campaign."""

    def __init__(self, message: str, run_directory: Path) -> None:
        super().__init__(message)
        self.run_directory = run_directory


class Executor(Protocol):
    def prepare(self) -> None: ...

    def execute(self, deposit: int, output_directory: Path) -> TestExecution: ...


def execution_delay_label(execution_mode: int) -> str:
    if execution_mode == 0:
        return "No Delay"
    if execution_mode == -1:
        return "Random Delay"
    if 0 < execution_mode <= 600_000:
        return f"Fixed Delay {execution_mode} ms"
    raise SlippageToleranceError(
        "ExecutionMode must be -1, 0, or a fixed delay from 1 to 600000 ms"
    )


def _timestamp_id(value: datetime) -> str:
    return f"{value.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _write_json(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"existing artifact will not be overwritten: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _replace_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_slippage_points(points: Iterable[int]) -> tuple[int, ...]:
    values = tuple(points)
    if not values:
        raise SlippageToleranceError("InpSlippage levels are empty")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise SlippageToleranceError("InpSlippage levels must be integers")
    if any(value < 0 for value in values):
        raise SlippageToleranceError("InpSlippage levels must be non-negative")
    if len(set(values)) != len(values):
        raise SlippageToleranceError("InpSlippage levels must not contain duplicates")
    if BASELINE_SLIPPAGE_POINTS not in values:
        raise SlippageToleranceError(
            f"baseline InpSlippage={BASELINE_SLIPPAGE_POINTS} is required"
        )
    return (BASELINE_SLIPPAGE_POINTS,) + tuple(
        sorted(value for value in values if value != BASELINE_SLIPPAGE_POINTS)
    )


def evaluate_baseline_gate(
    metrics: ReportMetrics,
    deals: DealAudit,
    benchmark: Benchmark,
) -> dict[str, object]:
    standard = check_benchmark(metrics, benchmark, deals)
    standard_payload = standard.to_dict()
    if standard.passed:
        return {
            "passed": True,
            "status": "PASS",
            "standard_gate": standard_payload,
            "accounting_drift": None,
        }

    identity_checks = standard.checks
    identity_passed = all(
        bool(identity_checks[name]["passed"])
        for name in ("trades", "deal_count", "deal_sequence_sha256")
    )
    references = (
        benchmark.accounting_net_profit,
        benchmark.commission_total,
        benchmark.swap_total,
        benchmark.deal_profit_total,
    )
    references_available = all(value is not None for value in references)
    if references_available:
        reference_net, reference_commission, reference_swap, reference_profit = (
            float(value) for value in references
        )
        actual_total = round(
            deals.commission_total + deals.swap_total + deals.deal_profit_total,
            10,
        )
        reference_total = round(
            reference_commission + reference_swap + reference_profit,
            10,
        )
        component_delta = round(
            (deals.commission_total - reference_commission)
            + (deals.swap_total - reference_swap)
            + (deals.deal_profit_total - reference_profit),
            10,
        )
        net_delta = round(metrics.net_profit - reference_net, 10)
        accounting_checks = {
            "reference_components_reconcile": abs(reference_total - reference_net)
            <= 0.01,
            "actual_components_reconcile": abs(actual_total - metrics.net_profit) <= 0.01,
            "net_difference_explained": abs(component_delta - net_delta) <= 0.01,
            "commission_matches": abs(deals.commission_total - reference_commission)
            <= 0.01,
            "deal_profit_matches": abs(deals.deal_profit_total - reference_profit)
            <= 0.01,
        }
        accounting_passed = all(accounting_checks.values())
        accounting_payload: dict[str, object] = {
            "reference": {
                "net_profit": reference_net,
                "commission_total": reference_commission,
                "swap_total": reference_swap,
                "deal_profit_total": reference_profit,
            },
            "actual": {
                "net_profit": metrics.net_profit,
                "commission_total": deals.commission_total,
                "swap_total": deals.swap_total,
                "deal_profit_total": deals.deal_profit_total,
            },
            "difference": {
                "net_profit": net_delta,
                "commission_total": round(
                    deals.commission_total - reference_commission, 10
                ),
                "swap_total": round(deals.swap_total - reference_swap, 10),
                "deal_profit_total": round(
                    deals.deal_profit_total - reference_profit, 10
                ),
            },
            "checks": accounting_checks,
        }
    else:
        accounting_passed = False
        accounting_payload = {
            "error": "reference accounting totals are not configured"
        }
    drift_passed = identity_passed and accounting_passed
    return {
        "passed": drift_passed,
        "status": (
            "PASS_WITH_ACCOUNTING_DRIFT"
            if drift_passed
            else "FAIL"
        ),
        "standard_gate": standard_payload,
        "identity_checks_passed": identity_passed,
        "accounting_drift": accounting_payload,
    }


def _validate_scenarios(scenarios: tuple[Scenario, ...]) -> tuple[Scenario, ...]:
    if not scenarios:
        raise SlippageToleranceError("QQ scenarios are empty")
    if any(scenario.ea_id != "quantum_queen" for scenario in scenarios):
        raise SlippageToleranceError("Layer 2 only supports Quantum Queen")
    by_wf = {scenario.wf: scenario for scenario in scenarios}
    if tuple(sorted(by_wf)) != EXPECTED_WFS or len(by_wf) != len(scenarios):
        raise SlippageToleranceError("Layer 2 requires exactly QQ WF1 through WF4")
    terminal_keys = {
        (
            str(scenario.terminal_path.resolve()).casefold(),
            str(scenario.data_directory.resolve()).casefold(),
        )
        for scenario in scenarios
    }
    if len(terminal_keys) != 1:
        raise SlippageToleranceError("all Layer-2 scenarios must use the same MT5")
    for scenario in scenarios:
        if scenario.symbol != "XAUUSD" or scenario.execution_mode != 0:
            raise SlippageToleranceError(
                f"{scenario.scenario_id} must use XAUUSD and No Delay"
            )
        if scenario.benchmark.deposit != 3000:
            raise SlippageToleranceError(
                f"{scenario.scenario_id} baseline deposit must be 3000 USD"
            )
        values = parse_set_values(read_set_text(scenario.set_source))
        if values.get(SLIPPAGE_INPUT) != str(BASELINE_SLIPPAGE_POINTS):
            raise SlippageToleranceError(
                f"{scenario.scenario_id} source set must have "
                f"{SLIPPAGE_INPUT}={BASELINE_SLIPPAGE_POINTS}"
            )
    return tuple(by_wf[wf] for wf in EXPECTED_WFS)


def _derived_scenario(
    scenario: Scenario,
    *,
    slippage_points: int,
    execution_mode: int,
    run_directory: Path,
) -> Scenario:
    source_text = read_set_text(scenario.set_source)
    source_values = parse_set_values(source_text)
    rendered = override_set_values(
        source_text,
        {SLIPPAGE_INPUT: str(slippage_points)},
    )
    rendered_values = parse_set_values(rendered)
    changed = {
        name
        for name in set(source_values) | set(rendered_values)
        if source_values.get(name) != rendered_values.get(name)
    }
    expected_changed = (
        set()
        if slippage_points == BASELINE_SLIPPAGE_POINTS
        else {SLIPPAGE_INPUT}
    )
    if changed != expected_changed:
        raise SlippageToleranceError(
            f"derived set changed unexpected values for {scenario.scenario_id}: "
            f"{sorted(changed)}"
        )
    set_name = (
        f"{scenario.artifact_prefix}_{scenario.wf}_SLIPPAGE_"
        f"{slippage_points:04d}.set"
    )
    set_path = run_directory / "sets" / set_name
    if set_path.exists():
        if parse_set_values(read_set_text(set_path)) != rendered_values:
            raise SlippageToleranceError(
                f"existing derived set has different values: {set_path}"
            )
    else:
        write_mt5_unicode(set_path, rendered)
    required = dict(scenario.required_set_values)
    required[SLIPPAGE_INPUT] = str(slippage_points)
    return replace(
        scenario,
        scenario_id=f"{scenario.scenario_id}_slippage_{slippage_points}",
        set_source=set_path,
        staged_set_name=set_name,
        execution_mode=execution_mode,
        deposits=(scenario.benchmark.deposit,),
        required_set_values=required,
    )


def _metrics_from_dict(value: dict[str, object]) -> ReportMetrics:
    return ReportMetrics(
        net_profit=float(value["net_profit"]),
        trades=int(value["trades"]),
        profit_factor=float(value["profit_factor"]),
        recovery_factor=float(value["recovery_factor"]),
        sharpe_ratio=float(value["sharpe_ratio"]),
        equity_drawdown_percent=float(value["equity_drawdown_percent"]),
    )


def _deals_from_dict(value: dict[str, object]) -> DealAudit:
    return DealAudit(
        deal_count=int(value["deal_count"]),
        deal_sequence_sha256=str(value["deal_sequence_sha256"]),
        commission_total=float(value["commission_total"]),
        swap_total=float(value["swap_total"]),
        deal_profit_total=float(value["deal_profit_total"]),
    )


def _archive_interrupted_directory(path: Path, now: datetime) -> None:
    if not path.exists():
        return
    archived = path.with_name(path.name + "_interrupted_" + _timestamp_id(now))
    path.replace(archived)


def run_slippage_tolerance_suite(
    scenarios: Iterable[Scenario],
    *,
    slippage_points: Iterable[int] = DEFAULT_SLIPPAGE_POINTS,
    execution_mode: int = 0,
    executor_factory: Callable[[Scenario, int], Executor] | None = None,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    resume_directory: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    scenario_list = _validate_scenarios(tuple(scenarios))
    ordered_points = validate_slippage_points(slippage_points)
    delay_label = execution_delay_label(execution_mode)
    output_root = (
        scenario_list[0].project_root
        / "data"
        / "slippage_tolerance"
        / "results"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    started = now_factory()
    if resume_directory is None:
        run_directory = output_root / _timestamp_id(started)
        run_directory.mkdir(exist_ok=False)
        manifest: dict[str, object] = {
            "schema_version": 1,
            "method": "EA allowable-deviation sensitivity",
            "layer": 2,
            "status": "running",
            "started_at_jst": _timestamp(started),
            "finished_at_jst": None,
            "ea_id": "quantum_queen",
            "scenario_ids": [scenario.scenario_id for scenario in scenario_list],
            "wfs": list(EXPECTED_WFS),
            "symbol": "XAUUSD",
            "deposit": 3000,
            "execution_mode": execution_mode,
            "execution_delay": delay_label,
            "baseline_slippage_points": BASELINE_SLIPPAGE_POINTS,
            "slippage_points": list(ordered_points),
            "results": [],
            "error": None,
        }
        manifest_path = run_directory / "suite_manifest.json"
        _write_json(manifest_path, manifest)
    else:
        run_directory = resume_directory.resolve()
        manifest_path = run_directory / "suite_manifest.json"
        if not manifest_path.is_file():
            raise SlippageToleranceError(
                f"resume manifest does not exist: {manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {
            "scenario_ids": [scenario.scenario_id for scenario in scenario_list],
            "slippage_points": list(ordered_points),
            "baseline_slippage_points": BASELINE_SLIPPAGE_POINTS,
            "execution_mode": execution_mode,
        }
        mismatches = {
            name: (manifest.get(name), value)
            for name, value in expected.items()
            if manifest.get(name) != value
        }
        if mismatches:
            raise SlippageToleranceError(
                f"resume manifest does not match requested suite: {mismatches}"
            )
        manifest["status"] = "running"
        manifest["finished_at_jst"] = None
        manifest["error"] = None
        _replace_json(manifest_path, manifest)

    raw_results = manifest.get("results")
    if not isinstance(raw_results, list):
        raise SlippageToleranceError("suite results must be a list")
    completed: dict[tuple[str, int], dict[str, object]] = {}
    baselines: dict[str, tuple[ReportMetrics, DealAudit]] = {}
    for payload in raw_results:
        if not isinstance(payload, dict) or payload.get("status") != "success":
            continue
        key = (str(payload["scenario_id"]), int(payload["slippage_points"]))
        if key in completed:
            raise SlippageToleranceError(f"duplicate completed result: {key}")
        completed[key] = payload
        if key[1] == BASELINE_SLIPPAGE_POINTS:
            gate = payload.get("baseline_gate")
            if not isinstance(gate, dict) or not gate.get("passed"):
                raise SlippageToleranceError(
                    f"completed baseline does not pass Safety Gate: {key[0]}"
                )
            baselines[key[0]] = (
                _metrics_from_dict(payload["metrics"]),
                _deals_from_dict(payload["deal_audit"]),
            )

    factory = executor_factory or (
        lambda scenario, _points: MT5Executor(scenario)
    )
    try:
        for points in ordered_points:
            for base_scenario in scenario_list:
                key = (base_scenario.scenario_id, points)
                if key in completed:
                    if progress:
                        progress(f"SKIP {base_scenario.wf} InpSlippage={points}")
                    continue
                if points != BASELINE_SLIPPAGE_POINTS and len(baselines) != len(
                    scenario_list
                ):
                    raise SlippageToleranceError(
                        "all four InpSlippage=100 baselines must pass before other levels"
                    )
                derived = _derived_scenario(
                    base_scenario,
                    slippage_points=points,
                    execution_mode=execution_mode,
                    run_directory=run_directory,
                )
                output_directory = (
                    run_directory
                    / base_scenario.scenario_id
                    / f"slippage_{points:04d}"
                )
                _archive_interrupted_directory(output_directory, now_factory())
                staging_directory = (
                    derived.data_directory
                    / "MQL5"
                    / "Files"
                    / "mt5_ea_validator"
                    / output_directory.parent.name
                    / output_directory.name
                )
                _archive_interrupted_directory(staging_directory, now_factory())
                if progress:
                    progress(
                        f"START {base_scenario.wf} InpSlippage={points} "
                        f"Execution={delay_label}"
                    )
                executor = factory(derived, points)
                executor.prepare()
                execution = executor.execute(
                    base_scenario.benchmark.deposit,
                    output_directory,
                )
                metrics = parse_report(execution.report_path)
                deals = parse_deal_audit(execution.report_path)
                gate = None
                deltas = None
                sequence_matches = None
                if points == BASELINE_SLIPPAGE_POINTS:
                    if execution_mode == 0:
                        gate = evaluate_baseline_gate(
                            metrics,
                            deals,
                            base_scenario.benchmark,
                        )
                    else:
                        gate = {
                            "passed": True,
                            "status": "DELAY_MODE_REFERENCE",
                            "standard_gate": None,
                            "accounting_drift": None,
                        }
                    if not gate["passed"]:
                        raise SlippageToleranceError(
                            f"{base_scenario.wf} InpSlippage=100 failed Safety Gate: "
                            f"{gate}"
                        )
                    baselines[base_scenario.scenario_id] = (metrics, deals)
                else:
                    baseline_metrics, baseline_deals = baselines[
                        base_scenario.scenario_id
                    ]
                    deltas = metric_deltas(
                        baseline_metrics,
                        metrics,
                        baseline_deals,
                        deals,
                    )
                    sequence_matches = (
                        deals.deal_sequence_sha256
                        == baseline_deals.deal_sequence_sha256
                    )
                payload = {
                    "status": "success",
                    "scenario_id": base_scenario.scenario_id,
                    "ea_id": base_scenario.ea_id,
                    "wf": base_scenario.wf,
                    "slippage_points": points,
                    "deposit": base_scenario.benchmark.deposit,
                    "symbol": derived.symbol,
                    "execution_mode": execution_mode,
                    "execution_delay": delay_label,
                    "set_source": str(base_scenario.set_source),
                    "derived_set": str(derived.set_source),
                    "derived_set_sha256": sha256_file(derived.set_source),
                    "ea_sha256": sha256_file(base_scenario.expert_binary),
                    "ini_file": str(execution.ini_path),
                    "report_file": str(execution.report_path),
                    "mt5_return_code": execution.return_code,
                    "metrics": metrics.to_dict(),
                    "deal_audit": deals.to_dict(),
                    "baseline_gate": gate,
                    "metric_deltas_from_100": deltas,
                    "deal_sequence_matches_100": sequence_matches,
                }
                _write_json(output_directory / "result.json", payload)
                raw_results.append(payload)
                completed[key] = payload
                _replace_json(manifest_path, manifest)
                if progress:
                    progress(
                        f"DONE {base_scenario.wf} InpSlippage={points} "
                        f"NetProfit={metrics.net_profit:.2f} Trades={metrics.trades}"
                    )
        manifest["status"] = "success"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        raise SlippageToleranceCampaignError(str(exc), run_directory) from exc
    finally:
        manifest["finished_at_jst"] = _timestamp(now_factory())
        _replace_json(manifest_path, manifest)
    return run_directory
