from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from mt5_ea_validator.configuration import WF_PERIODS, Scenario
from mt5_ea_validator.mt5 import MT5Executor, sha256_file
from mt5_ea_validator.ubs_smoke import (
    JST,
    REQUIRED_COMPATIBILITY_VALUES,
    Executor,
    UBSSmokeSettings,
    UBSStrategy,
    _scenario,
    _write_json,
    execute_ubs_case,
    validate_ubs_smoke_settings,
)


QUARTERLY_MODEL = 1
EXPECTED_WFS = ("WF1", "WF2", "WF3", "WF4")


class UBSQuarterlyError(ValueError):
    """Raised when quarterly comparison inputs are unsafe or inconsistent."""


class UBSQuarterlyCampaignError(RuntimeError):
    def __init__(self, message: str, run_directory: Path) -> None:
        super().__init__(message)
        self.run_directory = run_directory


def _load_json(path: Path, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UBSQuarterlyError(f"Cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UBSQuarterlyError(f"{label} must be a JSON object: {path}")
    return value


def _same_path(left: object, right: Path) -> bool:
    return str(Path(str(left)).resolve()).casefold() == str(right.resolve()).casefold()


def validate_quarterly_prerequisites(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
) -> tuple[dict[str, object], tuple[UBSStrategy, ...]]:
    validate_ubs_smoke_settings(settings)
    faithful = _load_json(faithful_manifest_path, label="faithful-set manifest")
    if faithful.get("status") != "success":
        raise UBSQuarterlyError("Faithful-set manifest is not successful")
    if faithful.get("original_sets_modified") is not False:
        raise UBSQuarterlyError("Faithful-set manifest does not preserve original sets")
    if faithful.get("ea_sha256") != sha256_file(settings.expert_binary):
        raise UBSQuarterlyError("EA hash does not match faithful-set manifest")

    validation_path = faithful_validation_run / "run_manifest.json"
    validation = _load_json(validation_path, label="faithful validation manifest")
    gate = validation.get("source_reproduction_gate")
    if validation.get("status") != "success" or not isinstance(gate, dict):
        raise UBSQuarterlyError("Faithful validation is not successful")
    if gate.get("passed") is not True:
        raise UBSQuarterlyError("Faithful validation reproduction gate did not pass")
    if not _same_path(validation.get("faithful_sets_manifest"), faithful_manifest_path):
        raise UBSQuarterlyError("Faithful validation used a different set manifest")

    source_smoke = Path(str(faithful.get("source_smoke_run")))
    tick_summary_path = source_smoke / "tick_data_quality_summary.json"
    tick_summary = _load_json(tick_summary_path, label="tick-data audit summary")
    if tick_summary.get("status") != "pass":
        raise UBSQuarterlyError("Source smoke tick-data quality gate did not pass")
    if int(tick_summary.get("audited_result_count", 0)) != 7:
        raise UBSQuarterlyError("Source smoke tick audit did not cover seven strategies")

    entries = faithful.get("sets")
    if not isinstance(entries, list) or len(entries) != 7:
        raise UBSQuarterlyError("Faithful-set manifest must contain seven sets")
    entry_by_id = {str(entry["strategy_id"]): entry for entry in entries}
    configured_ids = [strategy.strategy_id for strategy in settings.strategies]
    if set(entry_by_id) != set(configured_ids):
        raise UBSQuarterlyError("Configured strategies and faithful sets differ")
    set_directory = faithful_manifest_path.parent
    strategies: list[UBSStrategy] = []
    for strategy_id in configured_ids:
        entry = entry_by_id[strategy_id]
        file_name = str(entry["derived_file_name"])
        source = set_directory / file_name
        if not source.is_file():
            raise UBSQuarterlyError(f"Faithful set does not exist: {source}")
        if sha256_file(source) != entry.get("derived_set_sha256"):
            raise UBSQuarterlyError(f"Faithful set hash changed: {strategy_id}")
        strategies.append(UBSStrategy(strategy_id=strategy_id, file_name=file_name))
    return faithful, tuple(strategies)


def _run_id(value: datetime) -> str:
    return f"{value.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"


def _archive_interrupted(path: Path, value: datetime) -> None:
    if not path.exists():
        return
    destination = path.with_name(
        f"{path.name}_interrupted_{value.strftime('%Y%m%dT%H%M%S%f')}"
    )
    path.replace(destination)


def _summary_rows(results: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        metrics = result["metrics"]
        deals = result["deal_audit"]
        rows.append(
            {
                "wf": result["wf"],
                "strategy_id": result["strategy_id"],
                "from_date": result["from_date"],
                "to_date": result["to_date"],
                "net_profit": metrics["net_profit"],
                "trades": metrics["trades"],
                "profit_factor": metrics["profit_factor"],
                "recovery_factor": metrics["recovery_factor"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "equity_drawdown_percent": metrics["equity_drawdown_percent"],
                "deal_count": deals["deal_count"],
                "deal_sequence_sha256": deals["deal_sequence_sha256"],
                "commission_total": deals["commission_total"],
                "swap_total": deals["swap_total"],
                "deal_profit_total": deals["deal_profit_total"],
            }
        )
    return rows


def _write_summary(run_directory: Path, results: list[dict[str, object]]) -> None:
    rows = _summary_rows(results)
    _write_json(
        run_directory / "quarterly_summary.json",
        {
            "schema_version": 1,
            "model": QUARTERLY_MODEL,
            "model_label": "1 minute OHLC",
            "case_count": len(rows),
            "results": rows,
        },
    )
    csv_path = run_directory / "quarterly_summary.csv"
    if csv_path.exists():
        raise FileExistsError(f"Existing summary is not overwritten: {csv_path}")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


CaseRunner = Callable[..., dict[str, object]]


def run_ubs_quarterly_suite(
    settings: UBSSmokeSettings,
    faithful_manifest_path: Path,
    faithful_validation_run: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = MT5Executor,
    case_runner: CaseRunner = execute_ubs_case,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    resume_directory: Path | None = None,
    progress: Callable[[str], None] = print,
) -> Path:
    faithful, strategies = validate_quarterly_prerequisites(
        settings,
        faithful_manifest_path,
        faithful_validation_run,
    )
    output_root = settings.output_root.parent / "quarterly_1m_ohlc"
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
            "model": QUARTERLY_MODEL,
            "model_label": "1 minute OHLC",
            "execution_mode": 0,
            "deposit": settings.deposit,
            "currency": settings.currency,
            "leverage": settings.leverage,
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "source_smoke_run": faithful["source_smoke_run"],
            "tick_data_preflight": str(
                Path(str(faithful["source_smoke_run"]))
                / "tick_data_quality_summary.json"
            ),
            "wfs": list(EXPECTED_WFS),
            "periods": {
                wf: {"from_date": WF_PERIODS[wf][0], "to_date": WF_PERIODS[wf][1]}
                for wf in EXPECTED_WFS
            },
            "strategies": [asdict(strategy) for strategy in strategies],
            "cases_requested": len(EXPECTED_WFS) * len(strategies),
            "results": [],
            "error": None,
        }
        manifest_path = run_directory / "suite_manifest.json"
        _write_json(manifest_path, manifest)
    else:
        run_directory = resume_directory.resolve()
        manifest_path = run_directory / "suite_manifest.json"
        manifest = _load_json(manifest_path, label="quarterly suite manifest")
        expected = {
            "model": QUARTERLY_MODEL,
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "wfs": list(EXPECTED_WFS),
            "strategies": [asdict(strategy) for strategy in strategies],
        }
        mismatches = {
            name: (manifest.get(name), value)
            for name, value in expected.items()
            if manifest.get(name) != value
        }
        if mismatches:
            raise UBSQuarterlyError(
                f"Resume manifest does not match requested suite: {mismatches}"
            )
        raw_results = manifest.get("results")
        if not isinstance(raw_results, list):
            raise UBSQuarterlyError("Quarterly results must be a list")
        manifest["results"] = [
            result for result in raw_results if result.get("status") == "success"
        ]
        manifest["status"] = "running"
        manifest["finished_at_jst"] = None
        manifest["error"] = None
        _write_json(manifest_path, manifest, replace=True)

    logger = logging.getLogger(f"mt5_ea_validator.ubs_quarterly.{run_directory.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(run_directory / "execution.log", encoding="utf-8")
    logger.addHandler(handler)
    results = manifest["results"]
    assert isinstance(results, list)
    completed = {
        (str(result["wf"]), str(result["strategy_id"]))
        for result in results
        if result.get("status") == "success"
    }
    total = len(EXPECTED_WFS) * len(strategies)
    try:
        case_number = 0
        for wf in EXPECTED_WFS:
            from_date, to_date = WF_PERIODS[wf]
            wf_settings = replace(
                settings,
                scenario_id=f"ubs_gold_{wf.lower()}_1m_ohlc",
                set_directory=faithful_manifest_path.parent,
                from_date=from_date,
                to_date=to_date,
                model=QUARTERLY_MODEL,
                strategies=strategies,
            )
            for strategy in strategies:
                case_number += 1
                key = (wf, strategy.strategy_id)
                if key in completed:
                    progress(f"SKIP [{case_number}/{total}] {wf} {strategy.strategy_id}")
                    continue
                output = run_directory / f"{case_number:02d}_{wf}_{strategy.strategy_id}"
                _archive_interrupted(output, now_factory())
                scenario = replace(
                    _scenario(wf_settings, strategy),
                    scenario_id=f"ubs_{wf.lower()}_{strategy.strategy_id}_1m_ohlc",
                    wf=wf,
                    staged_set_name=(
                        f"UBS_{wf}_{strategy.strategy_id}_FAITHFUL.set"
                    ),
                )
                progress(f"START [{case_number}/{total}] {wf} {strategy.strategy_id}")
                try:
                    result = case_runner(
                        wf_settings,
                        strategy,
                        scenario,
                        output,
                        executor_factory=executor_factory,
                        require_report_covered_inputs=True,
                    )
                    result.update(
                        {
                            "case_number": case_number,
                            "wf": wf,
                            "from_date": from_date,
                            "to_date": to_date,
                            "model": QUARTERLY_MODEL,
                        }
                    )
                    _write_json(output / "result.json", result)
                    results.append(result)
                    _write_json(manifest_path, manifest, replace=True)
                    if result.get("status") != "success":
                        raise UBSQuarterlyCampaignError(
                            f"Quarterly case failed: {wf}/{strategy.strategy_id}",
                            run_directory,
                        )
                    progress(f"PASS [{case_number}/{total}] {wf} {strategy.strategy_id}")
                except UBSQuarterlyCampaignError:
                    raise
                except Exception as exc:
                    output.mkdir(parents=True, exist_ok=True)
                    failed = {
                        "status": "failed",
                        "case_number": case_number,
                        "wf": wf,
                        "strategy_id": strategy.strategy_id,
                        "from_date": from_date,
                        "to_date": to_date,
                        "model": QUARTERLY_MODEL,
                        "error": str(exc),
                    }
                    result_path = output / "result.json"
                    if not result_path.exists():
                        _write_json(result_path, failed)
                        results.append(failed)
                    _write_json(manifest_path, manifest, replace=True)
                    raise UBSQuarterlyCampaignError(
                        f"Quarterly case failed: {wf}/{strategy.strategy_id}: {exc}",
                        run_directory,
                    ) from exc
        if len(results) != total:
            raise UBSQuarterlyCampaignError(
                f"Quarterly suite completed {len(results)} of {total} cases",
                run_directory,
            )
        _write_summary(run_directory, results)
        manifest["status"] = "success"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        logger.exception("quarterly suite failed")
        if isinstance(exc, UBSQuarterlyCampaignError):
            raise
        raise UBSQuarterlyCampaignError(str(exc), run_directory) from exc
    finally:
        manifest["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        _write_json(manifest_path, manifest, replace=True)
        handler.close()
        logger.removeHandler(handler)
    return run_directory
