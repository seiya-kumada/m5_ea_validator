from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from mt5_ea_validator.configuration import Scenario
from mt5_ea_validator.mt5 import MT5Executor, TestExecution, sha256_file
from mt5_ea_validator.report import (
    BenchmarkCheck,
    check_benchmark,
    parse_deal_audit,
    parse_report,
)


JST = timezone(timedelta(hours=9), "JST")


class CampaignError(RuntimeError):
    """Raised when the campaign must stop before completing every deposit."""

    def __init__(self, message: str, run_directory: Path) -> None:
        super().__init__(message)
        self.run_directory = run_directory


class Executor(Protocol):
    def prepare(self) -> None: ...

    def execute(self, deposit: int, output_directory: Path) -> TestExecution: ...


def _now() -> datetime:
    return datetime.now(JST)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _run_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"


def _write_json(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"既存ファイルは上書きしません: {path}")
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


def _logger_for(run_directory: Path) -> logging.Logger:
    logger = logging.getLogger(f"mt5_ea_validator.{run_directory.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(run_directory / "execution.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def run_campaign(
    scenario: Scenario,
    *,
    executor: Executor | None = None,
    now_factory=_now,
) -> Path:
    started = now_factory()
    scenario.output_root.mkdir(parents=True, exist_ok=True)
    run_directory = scenario.output_root / _run_id(started)
    run_directory.mkdir(exist_ok=False)
    manifest_path = run_directory / "run_manifest.json"
    logger = _logger_for(run_directory)
    active_executor = executor or MT5Executor(scenario)

    manifest: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_directory.name,
        "scenario_id": scenario.scenario_id,
        "status": "running",
        "started_at_jst": _timestamp(started),
        "finished_at_jst": None,
        "ea": scenario.expert,
        "wf": scenario.wf,
        "symbol": scenario.symbol,
        "period": scenario.period,
        "from_date": scenario.from_date,
        "to_date": scenario.to_date,
        "deposits_requested": list(scenario.deposits),
        "deposits_completed": [],
        "set_source": str(scenario.set_source),
        "staged_set": str(scenario.staged_set_path),
        "results": [],
        "error": None,
    }
    _write_json(manifest_path, manifest)

    try:
        logger.info("campaign started scenario=%s", scenario.scenario_id)
        active_executor.prepare()
        manifest["set_sha256"] = sha256_file(scenario.set_source)
        manifest["ea_sha256"] = sha256_file(scenario.expert_binary)
        _replace_json(manifest_path, manifest)

        for deposit in scenario.deposits:
            deposit_started = now_factory()
            output_directory = run_directory / f"deposit_{deposit}"
            logger.info(
                "test started ea=%s wf=%s from=%s to=%s deposit=%s set=%s",
                scenario.expert,
                scenario.wf,
                scenario.from_date,
                scenario.to_date,
                deposit,
                scenario.staged_set_path,
            )
            try:
                execution = active_executor.execute(deposit, output_directory)
                metrics = parse_report(execution.report_path)
                deal_audit = parse_deal_audit(execution.report_path)
                benchmark_check: BenchmarkCheck | None = None
                if deposit == scenario.benchmark.deposit:
                    benchmark_check = check_benchmark(
                        metrics, scenario.benchmark, deal_audit
                    )

                accounting_note: dict[str, object] | None = None
                if (
                    benchmark_check is not None
                    and benchmark_check.passed
                    and metrics.net_profit != scenario.benchmark.net_profit
                ):
                    net_profit_check = benchmark_check.checks["net_profit"]
                    accounting_note = {
                        "classification": "minor accounting difference",
                        "actual_net_profit": metrics.net_profit,
                        "benchmark_net_profit": scenario.benchmark.net_profit,
                        "difference": round(
                            metrics.net_profit - scenario.benchmark.net_profit, 10
                        ),
                        "effective_tolerance": net_profit_check["tolerance"],
                        "commission_total": deal_audit.commission_total,
                        "swap_total": deal_audit.swap_total,
                        "deal_profit_total": deal_audit.deal_profit_total,
                        "manual_accounting_detail_available": False,
                    }
                    logger.warning(
                        "minor accounting difference deposit=%s actual_net_profit=%s "
                        "benchmark_net_profit=%s difference=%s commission_total=%s "
                        "swap_total=%s deal_profit_total=%s",
                        deposit,
                        metrics.net_profit,
                        scenario.benchmark.net_profit,
                        accounting_note["difference"],
                        deal_audit.commission_total,
                        deal_audit.swap_total,
                        deal_audit.deal_profit_total,
                    )

                deposit_finished = now_factory()
                result: dict[str, object] = {
                    "status": "success",
                    "deposit": deposit,
                    "started_at_jst": _timestamp(deposit_started),
                    "finished_at_jst": _timestamp(deposit_finished),
                    "ini_file": str(execution.ini_path),
                    "set_file": str(scenario.staged_set_path),
                    "report_file": str(execution.report_path),
                    "mt5_return_code": execution.return_code,
                    "metrics": metrics.to_dict(),
                    "deal_audit": deal_audit.to_dict(),
                    "benchmark": benchmark_check.to_dict() if benchmark_check else None,
                    "accounting_note": accounting_note,
                }
                _write_json(output_directory / "result.json", result)
                cast_results = manifest["results"]
                assert isinstance(cast_results, list)
                cast_results.append(result)
                completed = manifest["deposits_completed"]
                assert isinstance(completed, list)
                completed.append(deposit)
                _replace_json(manifest_path, manifest)
                logger.info(
                    "test finished deposit=%s report=%s metrics=%s",
                    deposit,
                    execution.report_path,
                    json.dumps(metrics.to_dict(), ensure_ascii=False),
                )

                if benchmark_check is not None and not benchmark_check.passed:
                    raise CampaignError(
                        f"Deposit {scenario.benchmark.deposit}の結果が手動ベンチマークと一致しないため、後続Depositを停止しました",
                        run_directory,
                    )
            except CampaignError:
                raise
            except Exception as exc:
                output_directory.mkdir(parents=True, exist_ok=True)
                failed_result = {
                    "status": "failed",
                    "deposit": deposit,
                    "started_at_jst": _timestamp(deposit_started),
                    "finished_at_jst": _timestamp(now_factory()),
                    "error": str(exc),
                }
                result_path = output_directory / "result.json"
                if not result_path.exists():
                    _write_json(result_path, failed_result)
                raise CampaignError(
                    f"Deposit {deposit}の実行に失敗しました: {exc}", run_directory
                ) from exc

        manifest["status"] = "success"
        logger.info("campaign completed")
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        logger.error("campaign failed: %s", exc)
        if isinstance(exc, CampaignError):
            campaign_error = exc
        else:
            campaign_error = CampaignError(str(exc), run_directory)
        raise campaign_error
    finally:
        manifest["finished_at_jst"] = _timestamp(now_factory())
        _replace_json(manifest_path, manifest)
        for handler in tuple(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    return run_directory
