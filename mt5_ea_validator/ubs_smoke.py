from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Protocol

from mt5_ea_validator.configuration import Benchmark, Scenario
from mt5_ea_validator.mt5 import MT5Executor, TestExecution, sha256_file
from mt5_ea_validator.report import (
    parse_deal_audit,
    parse_report,
    parse_report_inputs,
)
from mt5_ea_validator.setfile import (
    parse_set_values,
    read_set_text,
    write_mt5_unicode,
)
from mt5_ea_validator.tick_history import (
    RealTickDataAudit,
    audit_latest_real_tick_test,
)


JST = timezone(timedelta(hours=9), "JST")
REQUIRED_COMPATIBILITY_VALUES = {
    "ForceSymbol": "XAUUSD",
    "UseAutoLoader": "false",
    "Run_Strategy": "1",
}


class UBSSmokeError(ValueError):
    """Raised when UBS smoke settings or results are invalid."""


class UBSSmokeCampaignError(RuntimeError):
    def __init__(self, message: str, run_directory: Path) -> None:
        super().__init__(message)
        self.run_directory = run_directory


class Executor(Protocol):
    def prepare(self) -> None: ...

    def execute(self, deposit: int, output_directory: Path) -> TestExecution: ...


@dataclass(frozen=True)
class UBSStrategy:
    strategy_id: str
    file_name: str


@dataclass(frozen=True)
class UBSSmokeSettings:
    scenario_id: str
    project_root: Path
    terminal_path: Path
    data_directory: Path
    expert: str
    expert_binary: Path
    ea_version_label: str
    set_directory: Path
    output_root: Path
    symbol: str
    period: str
    from_date: str
    to_date: str
    model: int
    execution_mode: int
    leverage: int
    currency: str
    deposit: int
    timeout_seconds: int
    strategies: tuple[UBSStrategy, ...]


def _project_root(config_path: Path) -> Path:
    for candidate in (config_path.parent, *config_path.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise UBSSmokeError(f"プロジェクトルートを特定できません: {config_path}")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_ubs_smoke_settings(path: Path) -> UBSSmokeSettings:
    config_path = path.resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UBSSmokeError(f"UBSスモーク設定を読み込めません: {exc}") from exc
    root = _project_root(config_path)
    try:
        strategies = tuple(UBSStrategy(**item) for item in raw["strategies"])
        settings = UBSSmokeSettings(
            scenario_id=str(raw["scenario_id"]),
            project_root=root,
            terminal_path=Path(raw["terminal_path"]),
            data_directory=Path(raw["data_directory"]),
            expert=str(raw["expert"]),
            expert_binary=Path(raw["data_directory"]) / raw["expert_binary"],
            ea_version_label=str(raw["ea_version_label"]),
            set_directory=_resolve(root, raw["set_directory"]),
            output_root=_resolve(root, raw["output_root"]),
            symbol=str(raw["symbol"]),
            period=str(raw["period"]),
            from_date=str(raw["from_date"]),
            to_date=str(raw["to_date"]),
            model=int(raw["model"]),
            execution_mode=int(raw["execution_mode"]),
            leverage=int(raw["leverage"]),
            currency=str(raw["currency"]),
            deposit=int(raw["deposit"]),
            timeout_seconds=int(raw["timeout_seconds"]),
            strategies=strategies,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UBSSmokeError(f"UBSスモーク設定の項目が不正です: {exc}") from exc
    validate_ubs_smoke_settings(settings)
    return settings


def validate_ubs_smoke_settings(settings: UBSSmokeSettings) -> None:
    if settings.symbol != "XAUUSD" or settings.period != "H1":
        raise UBSSmokeError("UBS GoldスモークはXAUUSD/H1に固定されています")
    if settings.model != 4 or settings.execution_mode != 0:
        raise UBSSmokeError("UBS Goldスモークはreal ticks/No Delayに固定されています")
    if settings.deposit <= 0 or settings.leverage <= 0 or settings.timeout_seconds <= 0:
        raise UBSSmokeError("Deposit、Leverage、Timeoutは正の値が必要です")
    if len(settings.strategies) != 7:
        raise UBSSmokeError("UBS Goldスモークには7戦略が必要です")
    ids = [item.strategy_id for item in settings.strategies]
    names = [item.file_name for item in settings.strategies]
    if len(set(ids)) != len(ids) or len(set(names)) != len(names):
        raise UBSSmokeError("strategy_idまたはsetファイル名が重複しています")
    if any(not value.replace("_", "").isalnum() for value in ids):
        raise UBSSmokeError("strategy_idには英数字とアンダースコアだけを使用します")


def _equivalent(left: str, right: str) -> bool:
    if left.strip().casefold() in {"true", "false"}:
        return left.strip().casefold() == right.strip().casefold()
    try:
        return Decimal(left.strip()) == Decimal(right.strip())
    except InvalidOperation:
        return left.strip() == right.strip()


def compare_ubs_inputs(
    set_values: dict[str, str],
    report_values: dict[str, str],
    *,
    require_exact: bool = False,
    require_report_covered: bool = False,
) -> dict[str, object]:
    common = sorted(set(set_values) & set(report_values))
    mismatches = {
        name: {"set": set_values[name], "report": report_values[name]}
        for name in common
        if not _equivalent(set_values[name], report_values[name])
    }
    required_checks = {
        name: {
            "expected": expected,
            "set": set_values.get(name),
            "report": report_values.get(name),
            "passed": set_values.get(name) is not None
            and report_values.get(name) is not None
            and _equivalent(set_values[name], expected)
            and _equivalent(report_values[name], expected),
        }
        for name, expected in REQUIRED_COMPATIBILITY_VALUES.items()
    }
    set_only = sorted(set(set_values) - set(report_values))
    report_only = sorted(set(report_values) - set(set_values))
    required_passed = all(bool(item["passed"]) for item in required_checks.values())
    exact_passed = not mismatches and not set_only and not report_only
    report_covered_passed = not mismatches and not report_only
    if require_exact and require_report_covered:
        raise UBSSmokeError("Exact and report-covered modes are mutually exclusive")
    mode = (
        "exact"
        if require_exact
        else "report_covered"
        if require_report_covered
        else "operational"
    )
    return {
        "passed": required_passed
        and (exact_passed if require_exact else True)
        and (report_covered_passed if require_report_covered else True),
        "validation_mode": mode,
        "exact_match": exact_passed,
        "report_covered_match": report_covered_passed,
        "set_input_count": len(set_values),
        "report_input_count": len(report_values),
        "common_input_count": len(common),
        "required_checks": required_checks,
        "common_value_mismatches": mismatches,
        "set_inputs_not_observed_in_report": set_only,
        "report_inputs_not_present_in_set": report_only,
        "note": (
            "Display-name inputs and decorative section fields may appear under different "
            "names in the report; only required_checks determine the compatibility gate."
        ),
    }


def compare_default_to_smoke(
    default_values: dict[str, str],
    settings: UBSSmokeSettings,
    smoke_run_directory: Path,
) -> dict[str, object]:
    manifest_path = smoke_run_directory / "run_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UBSSmokeError(f"スモークmanifestを読み込めません: {exc}") from exc
    results = manifest.get("results")
    if manifest.get("status") != "success" or not isinstance(results, list):
        raise UBSSmokeError("成功済みスモークmanifestが必要です")
    comparisons: list[dict[str, object]] = []
    for result in results:
        strategy_id = str(result["strategy_id"])
        source = settings.set_directory / str(result["file_name"])
        set_values = parse_set_values(read_set_text(source))
        report_path = Path(str(result["report_file"]))
        applied_path = report_path.parent / "applied_inputs.json"
        try:
            applied = json.loads(applied_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UBSSmokeError(
                f"適用入力を読み込めません: {strategy_id}: {exc}"
            ) from exc
        filled_names = sorted(set(applied) - set(set_values))
        missing_from_default = [name for name in filled_names if name not in default_values]
        mismatches = {
            name: {"smoke": applied[name], "default_control": default_values[name]}
            for name in filled_names
            if name in default_values
            and not _equivalent(str(applied[name]), str(default_values[name]))
        }
        comparisons.append(
            {
                "strategy_id": strategy_id,
                "filled_input_count": len(filled_names),
                "filled_inputs": filled_names,
                "missing_from_default_control": missing_from_default,
                "value_mismatches": mismatches,
                "passed": not missing_from_default and not mismatches,
            }
        )
    return {
        "smoke_run_id": manifest.get("run_id"),
        "default_input_count": len(default_values),
        "passed": all(bool(item["passed"]) for item in comparisons),
        "strategies": comparisons,
        "interpretation": (
            "PASS means every input absent from an original set matched the no-"
            "ExpertParameters control value. It establishes the Tester baseline "
            "used in this environment, not source-code-level compiled defaults."
        ),
    }


def _scenario(settings: UBSSmokeSettings, strategy: UBSStrategy) -> Scenario:
    source = settings.set_directory / strategy.file_name
    set_hash = sha256_file(source)[:8] if source.is_file() else "missing"
    return Scenario(
        scenario_id=f"{settings.scenario_id}_{strategy.strategy_id}",
        ea_id="ultimate_breakout_system",
        artifact_prefix=f"UBS_{strategy.strategy_id.upper()}",
        wf="SMOKE",
        project_root=settings.project_root,
        terminal_path=settings.terminal_path,
        data_directory=settings.data_directory,
        expert=settings.expert,
        expert_binary=settings.expert_binary,
        set_source=source,
        staged_set_name=f"UBS_SMOKE_{strategy.strategy_id}_{set_hash}.set",
        output_root=settings.output_root,
        symbol=settings.symbol,
        period=settings.period,
        from_date=settings.from_date,
        to_date=settings.to_date,
        model=settings.model,
        execution_mode=settings.execution_mode,
        leverage=settings.leverage,
        currency=settings.currency,
        optimization=0,
        forward_mode=0,
        deposits=(settings.deposit,),
        timeout_seconds=settings.timeout_seconds,
        required_set_values=REQUIRED_COMPATIBILITY_VALUES,
        benchmark=Benchmark(
            deposit=settings.deposit,
            net_profit=0,
            net_profit_tolerance=0,
            net_profit_tolerance_percent=0,
            trades=0,
            deal_count=0,
            deal_sequence_sha256="",
        ),
    )


def _write_json(path: Path, value: object, *, replace: bool = False) -> None:
    if path.exists() and not replace:
        raise FileExistsError(f"既存ファイルは上書きしません: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp") if replace else path
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if replace:
        temporary.replace(path)


def _copy_log_snapshots(settings: UBSSmokeSettings, output: Path) -> list[str]:
    stamp = datetime.now(JST).strftime("%Y%m%d")
    sources = {
        "terminal": settings.data_directory / "logs" / f"{stamp}.log",
        "tester": settings.data_directory / "Tester" / "logs" / f"{stamp}.log",
    }
    copied: list[str] = []
    target = output / "log_snapshots"
    for label, source in sources.items():
        if source.is_file():
            target.mkdir(exist_ok=True)
            destination = target / f"{label}_{source.name}"
            shutil.copy2(source, destination)
            copied.append(str(destination))
    return copied


def _audit_tick_data(
    settings: UBSSmokeSettings,
    output: Path,
    log_snapshots: list[str],
    *,
    max_generated_fallback_ratio: float = 0.0,
) -> RealTickDataAudit:
    tester_logs = [
        Path(path)
        for path in log_snapshots
        if Path(path).name.startswith("tester_")
    ]
    if len(tester_logs) != 1:
        raise UBSSmokeError(
            f"Expected one Tester log snapshot, found {len(tester_logs)}"
        )
    audit = audit_latest_real_tick_test(
        tester_logs[0],
        symbol=settings.symbol,
        period=settings.period,
        from_date=settings.from_date,
        to_date=settings.to_date,
        max_generated_fallback_ratio=max_generated_fallback_ratio,
    )
    _write_json(output / "tick_data_quality.json", audit.to_dict())
    return audit


def execute_ubs_case(
    settings: UBSSmokeSettings,
    strategy: UBSStrategy,
    scenario: Scenario,
    output: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = MT5Executor,
    require_exact_inputs: bool = False,
    require_report_covered_inputs: bool = False,
    max_generated_fallback_ratio: float = 0.0,
) -> dict[str, object]:
    """Execute and persist one UBS case using the shared MT5/report pipeline."""
    source = scenario.set_source
    executor = executor_factory(scenario)
    executor.prepare()
    execution = executor.execute(settings.deposit, output)
    shutil.copy2(source, output / "input_set.set")
    metrics = parse_report(execution.report_path)
    deal_audit = parse_deal_audit(execution.report_path)
    report_inputs = parse_report_inputs(execution.report_path).values
    set_values = parse_set_values(read_set_text(source))
    compatibility = compare_ubs_inputs(
        set_values,
        report_inputs,
        require_exact=require_exact_inputs,
        require_report_covered=require_report_covered_inputs,
    )
    log_snapshots = _copy_log_snapshots(settings, output)
    tick_audit: RealTickDataAudit | None = None
    tick_data_passed = True
    if scenario.model == 4:
        tick_audit = _audit_tick_data(
            settings,
            output,
            log_snapshots,
            max_generated_fallback_ratio=max_generated_fallback_ratio,
        )
        tick_data_passed = tick_audit.passed
    passed = bool(compatibility["passed"]) and tick_data_passed
    _write_json(output / "applied_inputs.json", report_inputs)
    _write_json(output / "compatibility.json", compatibility)
    return {
        "status": "success" if passed else "failed",
        "strategy_id": strategy.strategy_id,
        "file_name": strategy.file_name,
        "set_sha256": sha256_file(source),
        "report_file": str(execution.report_path),
        "ini_file": str(execution.ini_path),
        "mt5_return_code": execution.return_code,
        "metrics": metrics.to_dict(),
        "deal_audit": deal_audit.to_dict(),
        "compatibility": compatibility,
        "tick_data_quality": tick_audit.to_dict() if tick_audit else None,
        "log_snapshots": log_snapshots,
    }


def run_ubs_smoke_suite(
    settings: UBSSmokeSettings,
    *,
    executor_factory: Callable[[Scenario], Executor] = MT5Executor,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    progress: Callable[[str], None] = print,
    require_exact_inputs: bool = False,
    require_report_covered_inputs: bool = False,
    campaign_label: str = "smoke",
    strategy_ids: tuple[str, ...] | None = None,
) -> Path:
    validate_ubs_smoke_settings(settings)
    selected = settings.strategies
    if strategy_ids is not None:
        requested = set(strategy_ids)
        selected = tuple(
            strategy for strategy in settings.strategies if strategy.strategy_id in requested
        )
        missing = sorted(requested - {strategy.strategy_id for strategy in selected})
        if not selected or missing:
            raise UBSSmokeError(f"Unknown or empty UBS strategy selection: {missing}")
    started = now_factory()
    settings.output_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{started.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
    run_directory = settings.output_root / run_id
    run_directory.mkdir(exist_ok=False)
    logger = logging.getLogger(f"mt5_ea_validator.ubs_smoke.{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(run_directory / "execution.log", encoding="utf-8")
    logger.addHandler(handler)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at_jst": started.isoformat(timespec="seconds"),
        "finished_at_jst": None,
        "ea_version_label": settings.ea_version_label,
        "ea_sha256": sha256_file(settings.expert_binary),
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
        "strategies_requested": [asdict(item) for item in selected],
        "results": [],
    }
    _write_json(run_directory / "run_manifest.json", manifest)
    failures = 0
    try:
        total = len(selected)
        for index, strategy in enumerate(selected, start=1):
            output = run_directory / f"{index:02d}_{strategy.strategy_id}"
            source = settings.set_directory / strategy.file_name
            progress(
                f"[{index}/{total}] UBS {campaign_label} start: {strategy.strategy_id}"
            )
            result: dict[str, object] = {
                "strategy_id": strategy.strategy_id,
                "file_name": strategy.file_name,
                "status": "running",
                "set_sha256": sha256_file(source) if source.is_file() else None,
            }
            try:
                scenario = _scenario(settings, strategy)
                result.update(
                    execute_ubs_case(
                        settings,
                        strategy,
                        scenario,
                        output,
                        executor_factory=executor_factory,
                        require_exact_inputs=require_exact_inputs,
                        require_report_covered_inputs=require_report_covered_inputs,
                    )
                )
                if result["status"] != "success":
                    failures += 1
            except Exception as exc:
                failures += 1
                output.mkdir(parents=True, exist_ok=True)
                result.update({"status": "failed", "error": str(exc)})
                logger.exception("strategy failed: %s", strategy.strategy_id)
            _write_json(output / "result.json", result)
            cast_results = manifest["results"]
            assert isinstance(cast_results, list)
            cast_results.append(result)
            _write_json(run_directory / "run_manifest.json", manifest, replace=True)
            progress(
                f"[{index}/{total}] UBS {campaign_label} "
                f"{result['status']}: {strategy.strategy_id}"
            )
            tick_quality = result.get("tick_data_quality")
            if isinstance(tick_quality, dict) and not tick_quality.get("passed"):
                logger.error(
                    "stopping after tick data quality failure: %s",
                    strategy.strategy_id,
                )
                break
        manifest["status"] = "success" if failures == 0 else "failed"
    finally:
        manifest["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        _write_json(run_directory / "run_manifest.json", manifest, replace=True)
        handler.close()
        logger.removeHandler(handler)
    if failures:
        raise UBSSmokeCampaignError(
            f"UBSスモークで{failures}戦略が失敗しました", run_directory
        )
    return run_directory


def _load_success_manifest(path: Path, *, label: str) -> dict[str, object]:
    manifest_path = path if path.is_file() else path / "run_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UBSSmokeError(f"Cannot read {label} manifest: {exc}") from exc
    if manifest.get("status") != "success":
        raise UBSSmokeError(f"{label} manifest is not successful: {manifest_path}")
    return manifest


def _render_effective_set(
    values: dict[str, str],
    *,
    strategy_id: str,
    source_set_sha256: str,
    smoke_run_id: str,
) -> str:
    if len(values) != 219:
        raise UBSSmokeError(
            f"Expected 219 effective inputs for {strategy_id}, got {len(values)}"
        )
    invalid_names = [name for name in values if not name or "=" in name or "\n" in name]
    invalid_values = [name for name, value in values.items() if "\n" in str(value)]
    if invalid_names or invalid_values:
        raise UBSSmokeError(
            f"Cannot safely render effective set for {strategy_id}: "
            f"invalid_names={invalid_names}, invalid_values={invalid_values}"
        )
    lines = [
        "; UBS v7.5 fully specified effective-input set",
        f"; Strategy: {strategy_id}",
        f"; Source set SHA-256: {source_set_sha256}",
        f"; Source smoke run: {smoke_run_id}",
        "; Original set is not modified.",
    ]
    lines.extend(f"{name}={value}" for name, value in values.items())
    return "\n".join(lines) + "\n"


def build_ubs_effective_sets(
    settings: UBSSmokeSettings,
    smoke_run_directory: Path,
    default_control_directory: Path,
) -> Path:
    """Build immutable derived sets from the 219 inputs applied by MT5."""
    validate_ubs_smoke_settings(settings)
    smoke = _load_success_manifest(smoke_run_directory, label="smoke")
    control = _load_success_manifest(default_control_directory, label="default control")
    comparison = control.get("comparison")
    if not isinstance(comparison, dict) or not comparison.get("passed"):
        raise UBSSmokeError("Default-control comparison did not pass")
    if str(comparison.get("smoke_run_id")) != str(smoke.get("run_id")):
        raise UBSSmokeError("Default control and smoke run IDs do not match")
    results = smoke.get("results")
    if not isinstance(results, list) or len(results) != len(settings.strategies):
        raise UBSSmokeError("Smoke manifest does not contain all seven strategies")

    output = settings.output_root.parent / "derived_sets" / str(smoke["run_id"])
    output.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, object]] = []
    by_strategy = {str(item["strategy_id"]): item for item in results}
    for strategy in settings.strategies:
        result = by_strategy.get(strategy.strategy_id)
        if result is None:
            raise UBSSmokeError(f"Missing smoke result: {strategy.strategy_id}")
        source_set = settings.set_directory / strategy.file_name
        source_hash = sha256_file(source_set)
        if source_hash != result.get("set_sha256"):
            raise UBSSmokeError(f"Original set hash changed: {strategy.strategy_id}")
        report_path = Path(str(result["report_file"]))
        applied_path = report_path.parent / "applied_inputs.json"
        try:
            raw_values = json.loads(applied_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UBSSmokeError(
                f"Cannot read applied inputs for {strategy.strategy_id}: {exc}"
            ) from exc
        if not isinstance(raw_values, dict):
            raise UBSSmokeError(f"Applied inputs are invalid: {strategy.strategy_id}")
        values = {str(name): str(value) for name, value in raw_values.items()}
        file_name = f"{strategy.strategy_id}_ubs_v75_effective_219.set"
        destination = output / file_name
        write_mt5_unicode(
            destination,
            _render_effective_set(
                values,
                strategy_id=strategy.strategy_id,
                source_set_sha256=source_hash,
                smoke_run_id=str(smoke["run_id"]),
            ),
        )
        reread = parse_set_values(read_set_text(destination))
        if reread != values:
            raise UBSSmokeError(f"Derived set reread failed: {strategy.strategy_id}")
        entries.append(
            {
                "strategy_id": strategy.strategy_id,
                "original_file_name": strategy.file_name,
                "original_set_sha256": source_hash,
                "source_report_file": str(report_path),
                "source_applied_inputs_file": str(applied_path),
                "derived_file_name": file_name,
                "derived_set_sha256": sha256_file(destination),
                "input_count": len(values),
                "local_reread_exact": True,
            }
        )
    manifest = {
        "schema_version": 1,
        "status": "success",
        "created_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "source_smoke_run": str(smoke_run_directory.resolve()),
        "source_smoke_run_id": smoke["run_id"],
        "default_control_run": str(default_control_directory.resolve()),
        "default_control_run_id": control["run_id"],
        "ea_sha256": smoke.get("ea_sha256"),
        "input_source": "MT5 smoke report effective inputs",
        "input_count_per_set": 219,
        "original_sets_modified": False,
        "sets": entries,
    }
    manifest_path = output / "derived_sets_manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def build_ubs_faithful_augmented_sets(
    settings: UBSSmokeSettings,
    smoke_run_directory: Path,
    default_control_directory: Path,
) -> Path:
    """Preserve every original set line and append missing v7.5 report inputs."""
    validate_ubs_smoke_settings(settings)
    smoke = _load_success_manifest(smoke_run_directory, label="smoke")
    control = _load_success_manifest(default_control_directory, label="default control")
    comparison = control.get("comparison")
    if not isinstance(comparison, dict) or not comparison.get("passed"):
        raise UBSSmokeError("Default-control comparison did not pass")
    if str(comparison.get("smoke_run_id")) != str(smoke.get("run_id")):
        raise UBSSmokeError("Default control and smoke run IDs do not match")
    results = smoke.get("results")
    if not isinstance(results, list) or len(results) != len(settings.strategies):
        raise UBSSmokeError("Smoke manifest does not contain all seven strategies")

    output = (
        settings.output_root.parent
        / "faithful_augmented_sets"
        / str(smoke["run_id"])
    )
    output.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, object]] = []
    by_strategy = {str(item["strategy_id"]): item for item in results}
    for strategy in settings.strategies:
        result = by_strategy.get(strategy.strategy_id)
        if result is None:
            raise UBSSmokeError(f"Missing smoke result: {strategy.strategy_id}")
        source_set = settings.set_directory / strategy.file_name
        source_hash = sha256_file(source_set)
        if source_hash != result.get("set_sha256"):
            raise UBSSmokeError(f"Original set hash changed: {strategy.strategy_id}")
        original_text = read_set_text(source_set)
        original_values = parse_set_values(original_text)
        report_path = Path(str(result["report_file"]))
        applied_path = report_path.parent / "applied_inputs.json"
        try:
            raw_values = json.loads(applied_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UBSSmokeError(
                f"Cannot read applied inputs for {strategy.strategy_id}: {exc}"
            ) from exc
        if not isinstance(raw_values, dict):
            raise UBSSmokeError(f"Applied inputs are invalid: {strategy.strategy_id}")
        applied = {str(name): str(value) for name, value in raw_values.items()}
        if len(applied) != 219:
            raise UBSSmokeError(
                f"Expected 219 applied inputs for {strategy.strategy_id}, got {len(applied)}"
            )
        additions = {
            name: value for name, value in applied.items() if name not in original_values
        }
        base = original_text.rstrip("\r\n")
        appended = [
            "",
            "; Added by mt5_ea_validator from UBS v7.5 smoke report.",
            f"; Source smoke run: {smoke['run_id']}",
        ]
        appended.extend(f"{name}={value}" for name, value in additions.items())
        rendered = base + "\n" + "\n".join(appended) + "\n"
        file_name = f"{strategy.strategy_id}_ubs_v75_faithful_augmented.set"
        destination = output / file_name
        write_mt5_unicode(destination, rendered)
        reread = parse_set_values(read_set_text(destination))
        expected = {**original_values, **additions}
        if reread != expected:
            raise UBSSmokeError(
                f"Faithful augmented set reread failed: {strategy.strategy_id}"
            )
        entries.append(
            {
                "strategy_id": strategy.strategy_id,
                "original_file_name": strategy.file_name,
                "original_set_sha256": source_hash,
                "source_report_file": str(report_path),
                "source_applied_inputs_file": str(applied_path),
                "derived_file_name": file_name,
                "derived_set_sha256": sha256_file(destination),
                "original_input_count": len(original_values),
                "appended_v75_input_count": len(additions),
                "derived_input_count": len(reread),
                "visible_input_count_expected": len(applied),
                "local_reread_exact": True,
            }
        )
    manifest = {
        "schema_version": 1,
        "status": "success",
        "created_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "source_smoke_run": str(smoke_run_directory.resolve()),
        "source_smoke_run_id": smoke["run_id"],
        "default_control_run": str(default_control_directory.resolve()),
        "default_control_run_id": control["run_id"],
        "ea_sha256": smoke.get("ea_sha256"),
        "construction": "original set plus v7.5 report inputs absent from original",
        "visible_input_count_expected": 219,
        "original_sets_modified": False,
        "sets": entries,
    }
    manifest_path = output / "faithful_augmented_sets_manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def run_ubs_effective_validation(
    settings: UBSSmokeSettings,
    derived_manifest_path: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = MT5Executor,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    progress: Callable[[str], None] = print,
) -> Path:
    """Reload all derived sets in MT5 and require an exact 219-input match."""
    derived = _load_success_manifest(derived_manifest_path, label="derived sets")
    entries = derived.get("sets")
    if not isinstance(entries, list) or len(entries) != len(settings.strategies):
        raise UBSSmokeError("Derived-set manifest does not contain seven sets")
    directory = (
        derived_manifest_path.parent
        if derived_manifest_path.is_file()
        else derived_manifest_path
    )
    strategies = tuple(
        UBSStrategy(
            strategy_id=str(entry["strategy_id"]),
            file_name=str(entry["derived_file_name"]),
        )
        for entry in entries
    )
    validation_settings = replace(
        settings,
        scenario_id="ubs_v75_effective_input_validation",
        set_directory=directory,
        output_root=settings.output_root.parent / "effective_validation",
        strategies=strategies,
    )
    run_directory = run_ubs_smoke_suite(
        validation_settings,
        executor_factory=executor_factory,
        now_factory=now_factory,
        progress=progress,
        require_exact_inputs=True,
        campaign_label="effective-input validation",
    )
    manifest_path = run_directory / "run_manifest.json"
    manifest = _load_success_manifest(manifest_path, label="effective validation")
    manifest["derived_sets_manifest"] = str(derived_manifest_path.resolve())
    manifest["source_smoke_run"] = derived["source_smoke_run"]
    manifest["exact_input_gate"] = {
        "expected_input_count": 219,
        "passed": all(
            result.get("compatibility", {}).get("exact_match") is True
            and result.get("compatibility", {}).get("set_input_count") == 219
            and result.get("compatibility", {}).get("report_input_count") == 219
            for result in manifest["results"]
        ),
    }
    _write_json(manifest_path, manifest, replace=True)
    if not manifest["exact_input_gate"]["passed"]:
        raise UBSSmokeCampaignError(
            "Derived sets did not reload as exactly 219 inputs", run_directory
        )
    return run_directory


def run_ubs_faithful_validation(
    settings: UBSSmokeSettings,
    derived_manifest_path: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] = MT5Executor,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    progress: Callable[[str], None] = print,
) -> Path:
    """Validate visible inputs and behavioral reproduction of faithful sets."""
    derived = _load_success_manifest(derived_manifest_path, label="faithful sets")
    entries = derived.get("sets")
    if not isinstance(entries, list) or len(entries) != len(settings.strategies):
        raise UBSSmokeError("Faithful-set manifest does not contain seven sets")
    directory = derived_manifest_path.parent
    strategies = tuple(
        UBSStrategy(
            strategy_id=str(entry["strategy_id"]),
            file_name=str(entry["derived_file_name"]),
        )
        for entry in entries
    )
    validation_settings = replace(
        settings,
        scenario_id="ubs_v75_faithful_augmented_validation",
        set_directory=directory,
        output_root=settings.output_root.parent / "faithful_validation",
        strategies=strategies,
    )
    run_directory = run_ubs_smoke_suite(
        validation_settings,
        executor_factory=executor_factory,
        now_factory=now_factory,
        progress=progress,
        require_report_covered_inputs=True,
        campaign_label="faithful-set validation",
    )
    manifest_path = run_directory / "run_manifest.json"
    manifest = _load_success_manifest(manifest_path, label="faithful validation")
    source_run = Path(str(derived["source_smoke_run"]))
    source = _load_success_manifest(source_run, label="source smoke")
    source_by_strategy = {
        str(result["strategy_id"]): result for result in source["results"]
    }
    comparisons: list[dict[str, object]] = []
    for result in manifest["results"]:
        strategy_id = str(result["strategy_id"])
        baseline = source_by_strategy[strategy_id]
        metrics_match = result["metrics"] == baseline["metrics"]
        deal_sequence_match = (
            result["deal_audit"]["deal_sequence_sha256"]
            == baseline["deal_audit"]["deal_sequence_sha256"]
        )
        deal_totals_match = all(
            result["deal_audit"][name] == baseline["deal_audit"][name]
            for name in ("deal_count", "commission_total", "swap_total", "deal_profit_total")
        )
        visible_inputs_match = (
            result["compatibility"]["report_covered_match"] is True
            and result["compatibility"]["report_input_count"] == 219
        )
        comparisons.append(
            {
                "strategy_id": strategy_id,
                "visible_inputs_match": visible_inputs_match,
                "metrics_match": metrics_match,
                "deal_sequence_match": deal_sequence_match,
                "deal_totals_match": deal_totals_match,
                "passed": visible_inputs_match
                and metrics_match
                and deal_sequence_match
                and deal_totals_match,
            }
        )
    gate = {
        "passed": all(item["passed"] for item in comparisons),
        "source_smoke_run": str(source_run),
        "strategies": comparisons,
    }
    manifest["faithful_sets_manifest"] = str(derived_manifest_path.resolve())
    manifest["source_reproduction_gate"] = gate
    manifest["status"] = "success" if gate["passed"] else "failed"
    _write_json(manifest_path, manifest, replace=True)
    if not gate["passed"]:
        raise UBSSmokeCampaignError(
            "Faithful augmented sets did not reproduce the source smoke run",
            run_directory,
        )
    return run_directory


def run_ubs_default_control(
    settings: UBSSmokeSettings,
    smoke_run_directory: Path,
    *,
    executor_factory: Callable[[Scenario], Executor] | None = None,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
) -> Path:
    validate_ubs_smoke_settings(settings)
    started = now_factory()
    output_root = settings.output_root.parent / "default_control"
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{started.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
    run_directory = output_root / run_id
    run_directory.mkdir(exist_ok=False)
    scenario = replace(
        _scenario(settings, settings.strategies[0]),
        scenario_id="ubs_v75_default_control",
        artifact_prefix="UBS_DEFAULT",
        wf="CONTROL",
    )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "started_at_jst": started.isoformat(timespec="seconds"),
        "finished_at_jst": None,
        "source_smoke_run": str(smoke_run_directory.resolve()),
        "expert_parameters_included": False,
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
    }
    _write_json(run_directory / "run_manifest.json", manifest)
    try:
        if executor_factory is None:
            executor: Executor = MT5Executor(
                scenario,
                stage_set=False,
                include_expert_parameters=False,
            )
        else:
            executor = executor_factory(scenario)
        executor.prepare()
        output = run_directory / "default"
        execution = executor.execute(settings.deposit, output)
        inputs = parse_report_inputs(execution.report_path).values
        metrics = parse_report(execution.report_path)
        deal_audit = parse_deal_audit(execution.report_path)
        comparison = compare_default_to_smoke(
            inputs, settings, smoke_run_directory
        )
        log_snapshots = _copy_log_snapshots(settings, output)
        tick_audit = _audit_tick_data(settings, output, log_snapshots)
        passed = bool(comparison["passed"]) and tick_audit.passed
        _write_json(output / "default_inputs.json", inputs)
        _write_json(output / "default_to_smoke_comparison.json", comparison)
        manifest.update(
            {
                "status": "success" if passed else "failed",
                "ea_sha256": sha256_file(settings.expert_binary),
                "report_file": str(execution.report_path),
                "ini_file": str(execution.ini_path),
                "mt5_return_code": execution.return_code,
                "default_input_count": len(inputs),
                "metrics": metrics.to_dict(),
                "deal_audit": deal_audit.to_dict(),
                "comparison": comparison,
                "tick_data_quality": tick_audit.to_dict(),
                "log_snapshots": log_snapshots,
            }
        )
        if not passed:
            raise UBSSmokeCampaignError(
                "Default-control input or tick-data gate failed", run_directory
            )
    except UBSSmokeCampaignError:
        raise
    except Exception as exc:
        manifest.update({"status": "failed", "error": str(exc)})
        raise UBSSmokeCampaignError(str(exc), run_directory) from exc
    finally:
        manifest["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        _write_json(run_directory / "run_manifest.json", manifest, replace=True)
    return run_directory
