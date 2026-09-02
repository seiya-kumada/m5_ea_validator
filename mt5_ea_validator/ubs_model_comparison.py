from __future__ import annotations

import csv
import json
import logging
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from mt5_ea_validator.configuration import Scenario
from mt5_ea_validator.mt5 import MT5Executor
from mt5_ea_validator.ubs_quarterly import validate_quarterly_prerequisites
from mt5_ea_validator.ubs_smoke import (
    JST,
    Executor,
    UBSSmokeSettings,
    UBSStrategy,
    _scenario,
    _write_json,
    execute_ubs_case,
)


MODEL_FROM_DATE = "2025.07.01"
MODEL_TO_DATE = "2026.06.30"
MAX_GENERATED_FALLBACK_RATIO = 0.0001
MODELS = (
    (1, "1 minute OHLC", "1m_ohlc"),
    (4, "Every tick based on real ticks", "real_ticks"),
)


class UBSModelComparisonError(ValueError):
    """Raised when the one-year model comparison is unsafe or inconsistent."""


class UBSModelComparisonCampaignError(RuntimeError):
    def __init__(self, message: str, run_directory: Path) -> None:
        super().__init__(message)
        self.run_directory = run_directory


def _load_json(path: Path, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UBSModelComparisonError(f"Cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UBSModelComparisonError(f"{label} must be a JSON object: {path}")
    return value


def _run_id(value: datetime) -> str:
    return f"{value.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"


def _archive_interrupted(path: Path, value: datetime) -> None:
    if not path.exists():
        return
    destination = path.with_name(
        f"{path.name}_interrupted_{value.strftime('%Y%m%dT%H%M%S%f')}"
    )
    path.replace(destination)


def _raw_rows(results: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        metrics = result["metrics"]
        deals = result["deal_audit"]
        tick_quality = result.get("tick_data_quality")
        rows.append(
            {
                "model": result["model"],
                "model_label": result["model_label"],
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
                "tick_data_quality_passed": (
                    tick_quality.get("passed")
                    if isinstance(tick_quality, dict)
                    else None
                ),
            }
        )
    return rows


def _numeric_delta(real: dict[str, object], ohlc: dict[str, object], key: str) -> float:
    return round(float(real[key]) - float(ohlc[key]), 10)


def _paired_rows(
    results: list[dict[str, object]], strategies: tuple[UBSStrategy, ...]
) -> list[dict[str, object]]:
    by_key = {
        (int(result["model"]), str(result["strategy_id"])): result
        for result in results
    }
    rows: list[dict[str, object]] = []
    for strategy in strategies:
        ohlc = by_key[(1, strategy.strategy_id)]
        real = by_key[(4, strategy.strategy_id)]
        ohlc_metrics = ohlc["metrics"]
        real_metrics = real["metrics"]
        ohlc_deals = ohlc["deal_audit"]
        real_deals = real["deal_audit"]
        rows.append(
            {
                "strategy_id": strategy.strategy_id,
                "ohlc_net_profit": ohlc_metrics["net_profit"],
                "real_ticks_net_profit": real_metrics["net_profit"],
                "net_profit_delta_real_minus_ohlc": _numeric_delta(
                    real_metrics, ohlc_metrics, "net_profit"
                ),
                "ohlc_trades": ohlc_metrics["trades"],
                "real_ticks_trades": real_metrics["trades"],
                "trades_delta_real_minus_ohlc": _numeric_delta(
                    real_metrics, ohlc_metrics, "trades"
                ),
                "profit_factor_delta_real_minus_ohlc": _numeric_delta(
                    real_metrics, ohlc_metrics, "profit_factor"
                ),
                "recovery_factor_delta_real_minus_ohlc": _numeric_delta(
                    real_metrics, ohlc_metrics, "recovery_factor"
                ),
                "sharpe_ratio_delta_real_minus_ohlc": _numeric_delta(
                    real_metrics, ohlc_metrics, "sharpe_ratio"
                ),
                "equity_drawdown_percent_delta_real_minus_ohlc": _numeric_delta(
                    real_metrics, ohlc_metrics, "equity_drawdown_percent"
                ),
                "ohlc_deal_count": ohlc_deals["deal_count"],
                "real_ticks_deal_count": real_deals["deal_count"],
                "deal_sequence_same": (
                    ohlc_deals["deal_sequence_sha256"]
                    == real_deals["deal_sequence_sha256"]
                ),
                "commission_delta_real_minus_ohlc": _numeric_delta(
                    real_deals, ohlc_deals, "commission_total"
                ),
                "swap_delta_real_minus_ohlc": _numeric_delta(
                    real_deals, ohlc_deals, "swap_total"
                ),
                "deal_profit_delta_real_minus_ohlc": _numeric_delta(
                    real_deals, ohlc_deals, "deal_profit_total"
                ),
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if path.exists():
        raise FileExistsError(f"Existing summary is not overwritten: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summaries(
    run_directory: Path,
    results: list[dict[str, object]],
    strategies: tuple[UBSStrategy, ...],
) -> None:
    raw_rows = _raw_rows(results)
    paired_rows = _paired_rows(results, strategies)
    _write_json(
        run_directory / "model_comparison_summary.json",
        {
            "schema_version": 1,
            "from_date": MODEL_FROM_DATE,
            "to_date": MODEL_TO_DATE,
            "case_count": len(raw_rows),
            "results": raw_rows,
        },
    )
    _write_csv(run_directory / "model_comparison_summary.csv", raw_rows)
    _write_json(
        run_directory / "model_comparison_pairs.json",
        {
            "schema_version": 1,
            "delta_definition": "real_ticks_minus_1_minute_ohlc",
            "strategy_count": len(paired_rows),
            "results": paired_rows,
        },
    )
    _write_csv(run_directory / "model_comparison_pairs.csv", paired_rows)


CaseRunner = Callable[..., dict[str, object]]


def run_ubs_model_comparison_suite(
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
        settings, faithful_manifest_path, faithful_validation_run
    )
    output_root = settings.output_root.parent / "model_comparison_1y"
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
            "from_date": MODEL_FROM_DATE,
            "to_date": MODEL_TO_DATE,
            "execution_mode": 0,
            "deposit": settings.deposit,
            "currency": settings.currency,
            "leverage": settings.leverage,
            "models": [
                {"model": model, "model_label": label, "slug": slug}
                for model, label, slug in MODELS
            ],
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "source_smoke_run": faithful["source_smoke_run"],
            "tick_data_preflight": str(
                Path(str(faithful["source_smoke_run"]))
                / "tick_data_quality_summary.json"
            ),
            "strategies": [asdict(strategy) for strategy in strategies],
            "cases_requested": len(MODELS) * len(strategies),
            "results": [],
            "error": None,
        }
        manifest_path = run_directory / "suite_manifest.json"
        _write_json(manifest_path, manifest)
    else:
        run_directory = resume_directory.resolve()
        manifest_path = run_directory / "suite_manifest.json"
        manifest = _load_json(manifest_path, label="model-comparison manifest")
        expected = {
            "from_date": MODEL_FROM_DATE,
            "to_date": MODEL_TO_DATE,
            "faithful_sets_manifest": str(faithful_manifest_path.resolve()),
            "faithful_validation_run": str(faithful_validation_run.resolve()),
            "models": [
                {"model": model, "model_label": label, "slug": slug}
                for model, label, slug in MODELS
            ],
            "strategies": [asdict(strategy) for strategy in strategies],
        }
        mismatches = {
            name: (manifest.get(name), value)
            for name, value in expected.items()
            if manifest.get(name) != value
        }
        if mismatches:
            raise UBSModelComparisonError(
                f"Resume manifest does not match requested suite: {mismatches}"
            )
        raw_results = manifest.get("results")
        if not isinstance(raw_results, list):
            raise UBSModelComparisonError("Model-comparison results must be a list")
        manifest["results"] = [
            result for result in raw_results if result.get("status") == "success"
        ]
        manifest["status"] = "running"
        manifest["finished_at_jst"] = None
        manifest["error"] = None
        _write_json(manifest_path, manifest, replace=True)

    logger = logging.getLogger(
        f"mt5_ea_validator.ubs_model_comparison.{run_directory.name}"
    )
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(run_directory / "execution.log", encoding="utf-8")
    logger.addHandler(handler)
    results = manifest["results"]
    assert isinstance(results, list)
    completed = {
        (int(result["model"]), str(result["strategy_id"]))
        for result in results
        if result.get("status") == "success"
    }
    total = len(MODELS) * len(strategies)
    try:
        case_number = 0
        for model, model_label, slug in MODELS:
            model_settings = replace(
                settings,
                scenario_id=f"ubs_gold_1y_{slug}",
                set_directory=faithful_manifest_path.parent,
                from_date=MODEL_FROM_DATE,
                to_date=MODEL_TO_DATE,
                model=model,
                strategies=strategies,
            )
            for strategy in strategies:
                case_number += 1
                key = (model, strategy.strategy_id)
                if key in completed:
                    progress(
                        f"SKIP [{case_number}/{total}] {model_label} "
                        f"{strategy.strategy_id}"
                    )
                    continue
                output = run_directory / f"{case_number:02d}_{slug}_{strategy.strategy_id}"
                _archive_interrupted(output, now_factory())
                scenario = replace(
                    _scenario(model_settings, strategy),
                    scenario_id=f"ubs_1y_{slug}_{strategy.strategy_id}",
                    wf="MODEL_1Y",
                    staged_set_name=(
                        f"UBS_MODEL_1Y_{slug.upper()}_{strategy.strategy_id}_FAITHFUL.set"
                    ),
                )
                progress(
                    f"START [{case_number}/{total}] {model_label} "
                    f"{strategy.strategy_id}"
                )
                try:
                    result = case_runner(
                        model_settings,
                        strategy,
                        scenario,
                        output,
                        executor_factory=executor_factory,
                        require_report_covered_inputs=True,
                        max_generated_fallback_ratio=(
                            MAX_GENERATED_FALLBACK_RATIO if model == 4 else 0.0
                        ),
                    )
                    result.update(
                        {
                            "case_number": case_number,
                            "from_date": MODEL_FROM_DATE,
                            "to_date": MODEL_TO_DATE,
                            "model": model,
                            "model_label": model_label,
                        }
                    )
                    _write_json(output / "result.json", result)
                    results.append(result)
                    _write_json(manifest_path, manifest, replace=True)
                    if result.get("status") != "success":
                        raise UBSModelComparisonCampaignError(
                            f"Model-comparison case failed: "
                            f"{model_label}/{strategy.strategy_id}",
                            run_directory,
                        )
                    progress(
                        f"PASS [{case_number}/{total}] {model_label} "
                        f"{strategy.strategy_id}"
                    )
                except UBSModelComparisonCampaignError:
                    raise
                except Exception as exc:
                    output.mkdir(parents=True, exist_ok=True)
                    failed = {
                        "status": "failed",
                        "case_number": case_number,
                        "strategy_id": strategy.strategy_id,
                        "from_date": MODEL_FROM_DATE,
                        "to_date": MODEL_TO_DATE,
                        "model": model,
                        "model_label": model_label,
                        "error": str(exc),
                    }
                    result_path = output / "result.json"
                    if not result_path.exists():
                        _write_json(result_path, failed)
                        results.append(failed)
                    _write_json(manifest_path, manifest, replace=True)
                    raise UBSModelComparisonCampaignError(
                        f"Model-comparison case failed: "
                        f"{model_label}/{strategy.strategy_id}: {exc}",
                        run_directory,
                    ) from exc
        if len(results) != total:
            raise UBSModelComparisonCampaignError(
                f"Model-comparison suite completed {len(results)} of {total} cases",
                run_directory,
            )
        _write_summaries(run_directory, results, strategies)
        manifest["status"] = "success"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        logger.exception("model-comparison suite failed")
        if isinstance(exc, UBSModelComparisonCampaignError):
            raise
        raise UBSModelComparisonCampaignError(str(exc), run_directory) from exc
    finally:
        manifest["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        _write_json(manifest_path, manifest, replace=True)
        handler.close()
        logger.removeHandler(handler)
    return run_directory
