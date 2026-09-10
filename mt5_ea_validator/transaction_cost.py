from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from mt5_ea_validator.configuration import Scenario
from mt5_ea_validator.mt5 import (
    MT5Error,
    MT5Executor,
    TestExecution,
    sha256_file,
    validate_environment,
)
from mt5_ea_validator.report import (
    BenchmarkCheck,
    DealAudit,
    ReportMetrics,
    check_benchmark,
    parse_deal_audit,
    parse_report,
)
from mt5_ea_validator.setfile import (
    parse_set_values,
    read_set_text,
    stage_dedicated_set,
    write_mt5_unicode,
)


JST = timezone(timedelta(hours=9), "JST")
DEFAULT_BUILD_FROM = "2024.01.01"
DEFAULT_BUILD_TO_EXCLUSIVE = "2026.07.01"
DEFAULT_STRESS_POINTS = (0, 2, 5, 10)
SCRIPT_SOURCE = Path("mql5/BuildCostStressSymbol.mq5")
COMMISSION_PROFILE_SOURCE = Path(
    "config/transaction_cost/TitanFX-MT5-Demo_demo.txt"
)
COMMISSION_PROFILE_NAME = "TitanFX-MT5-Demo_demo.txt"
REQUIRED_COMMISSION_VALUES = {
    "CommonUseSettings": "1",
    "CommissionSymbol": "*",
    "CommissionCharge": "2",
    "CommissionRange": "0",
    "CommissionEntry": "1",
    "CommissionValue": "72.0000",
    "CommissionRangeFrom": "0.01",
    "CommissionMode": "0",
    "CommissionType": "1",
}


class TransactionCostError(RuntimeError):
    """Raised when a transaction-cost stress operation is unsafe or invalid."""


@dataclass(frozen=True)
class SymbolBuildManifest:
    schema_version: int
    status: str
    error: str
    source_symbol: str
    custom_symbol: str
    custom_group: str
    from_msc: int
    to_msc_exclusive: int
    digits: int
    point: float
    tick_size: float
    stress_points: int
    stress_price: float
    source_tick_count: int
    written_tick_count: int
    persisted_tick_count: int
    first_time_msc: int
    last_time_msc: int
    source_tick_audit_fnv1a64: str
    output_tick_audit_fnv1a64: str
    persisted_tick_audit_fnv1a64: str
    readback_mismatch_from_msc: int
    readback_expected_day_count: int
    readback_actual_day_count: int
    source_m1_bar_count: int
    written_m1_bar_count: int
    persisted_m1_bar_count: int
    first_m1_time: int
    last_m1_time: int
    source_m1_audit_fnv1a64: str
    output_m1_audit_fnv1a64: str
    persisted_m1_audit_fnv1a64: str
    m1_readback_mismatch_from: int
    m1_readback_expected_day_count: int
    m1_readback_actual_day_count: int
    source_spread_min_points: int
    source_spread_mean_points: float
    source_spread_p50_points: int
    source_spread_p95_points: int
    source_spread_p99_points: int
    source_spread_max_points: int
    artifact_manifest: Path | None = None

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["artifact_manifest"] = (
            str(self.artifact_manifest) if self.artifact_manifest else None
        )
        return value


@dataclass(frozen=True)
class CostStressResult:
    scenario_id: str
    ea_id: str
    wf: str
    stress_points: int
    custom_symbol: str
    deposit: int
    metrics: ReportMetrics
    deal_audit: DealAudit
    zero_stress_gate: BenchmarkCheck | None
    metric_deltas_from_zero: dict[str, float | int] | None
    execution: TestExecution

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "ea_id": self.ea_id,
            "wf": self.wf,
            "stress_points": self.stress_points,
            "custom_symbol": self.custom_symbol,
            "deposit": self.deposit,
            "metrics": self.metrics.to_dict(),
            "deal_audit": self.deal_audit.to_dict(),
            "zero_stress_gate": (
                self.zero_stress_gate.to_dict() if self.zero_stress_gate else None
            ),
            "metric_deltas_from_zero": self.metric_deltas_from_zero,
            "ini_file": str(self.execution.ini_path),
            "report_file": str(self.execution.report_path),
            "mt5_return_code": self.execution.return_code,
        }


def _timestamp_id(now: datetime | None = None) -> str:
    current = now or datetime.now(JST)
    return f"{current.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}"


def validate_stress_points(values: Iterable[int]) -> tuple[int, ...]:
    points = validate_generation_stress_points(values)
    if points[0] != 0:
        raise TransactionCostError("最初のstress pointsはS=0でなければなりません")
    return points


def validate_generation_stress_points(values: Iterable[int]) -> tuple[int, ...]:
    points = tuple(int(value) for value in values)
    if not points:
        raise TransactionCostError("stress pointsを1件以上指定してください")
    if any(value < 0 for value in points):
        raise TransactionCostError("stress pointsは0以上でなければなりません")
    if len(set(points)) != len(points):
        raise TransactionCostError("stress pointsが重複しています")
    if tuple(sorted(points)) != points:
        raise TransactionCostError("stress pointsは小さい順に指定してください")
    return points


def render_symbol_builder_set(
    *,
    source_symbol: str,
    custom_symbol: str,
    from_date: str,
    to_date_exclusive: str,
    stress_points: int,
    output_file: Path,
    reference_tick_size: float | None = None,
    reference_first_m1_time: int | None = None,
) -> str:
    if reference_first_m1_time is not None and (type(reference_first_m1_time) is not int or reference_first_m1_time <= 0):
        raise TransactionCostError('Reference first M1 time must be a positive integer')
    if reference_tick_size is not None and (not math.isfinite(reference_tick_size) or reference_tick_size <= 0):
        raise TransactionCostError('Reference tick size must be finite and positive')
    if stress_points < 0:
        raise TransactionCostError("stress pointsは0以上でなければなりません")
    if not re.fullmatch(r"[A-Za-z0-9._&#]+", custom_symbol):
        raise TransactionCostError(f"カスタムシンボル名が不正です: {custom_symbol}")
    output_value = str(output_file).replace("/", "\\")
    return "\n".join(
        (
            f"InpSourceSymbol={source_symbol}",
            f"InpCustomSymbol={custom_symbol}",
            "InpCustomGroup=MT5EAValidator\\TransactionCost",
            f"InpFromDate={from_date}",
            f"InpToDateExclusive={to_date_exclusive}",
            f"InpStressPoints={stress_points}",
            f"InpOutputFile={output_value}",
            *((f"InpReferenceTickSize={reference_tick_size}",) if reference_tick_size is not None else ()),
            *((f"InpReferenceFirstM1Time={reference_first_m1_time}",) if reference_first_m1_time is not None else ()),
            "",
        )
    )


def render_symbol_builder_ini(
    *, script_name: str, preset_name: str
) -> str:
    return "\n".join(
        (
            "[Experts]",
            "Enabled=1",
            "AllowLiveTrading=0",
            "AllowDllImport=0",
            "",
            "[StartUp]",
            f"Script={script_name}",
            f"ScriptParameters={preset_name}",
            "Period=H1",
            "ShutdownTerminal=1",
            "",
        )
    )


def parse_symbol_build_manifest(path: Path) -> SymbolBuildManifest:
    if not path.is_file():
        raise TransactionCostError(f"シンボル生成マニフェストがありません: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    required = {
        field.name
        for field in SymbolBuildManifest.__dataclass_fields__.values()
        if field.name != "artifact_manifest"
    }
    missing = sorted(required - values.keys())
    if missing:
        raise TransactionCostError(
            "シンボル生成マニフェストに必須項目がありません: "
            + ", ".join(missing)
        )
    integer_fields = {
        "schema_version",
        "from_msc",
        "to_msc_exclusive",
        "digits",
        "stress_points",
        "source_tick_count",
        "written_tick_count",
        "persisted_tick_count",
        "first_time_msc",
        "last_time_msc",
        "readback_mismatch_from_msc",
        "readback_expected_day_count",
        "readback_actual_day_count",
        "source_m1_bar_count",
        "written_m1_bar_count",
        "persisted_m1_bar_count",
        "first_m1_time",
        "last_m1_time",
        "m1_readback_mismatch_from",
        "m1_readback_expected_day_count",
        "m1_readback_actual_day_count",
        "source_spread_min_points",
        "source_spread_p50_points",
        "source_spread_p95_points",
        "source_spread_p99_points",
        "source_spread_max_points",
    }
    float_fields = {
        "point",
        "tick_size",
        "stress_price",
        "source_spread_mean_points",
    }
    converted: dict[str, object] = {}
    try:
        for name in required:
            raw = values[name]
            if name in integer_fields:
                converted[name] = int(raw)
            elif name in float_fields:
                converted[name] = float(raw)
            else:
                converted[name] = raw
    except ValueError as exc:
        raise TransactionCostError(
            f"シンボル生成マニフェストの数値が不正です: {path}: {exc}"
        ) from exc
    return SymbolBuildManifest(**converted, artifact_manifest=path)


def validate_metaeditor_compile(
    *, return_code: int, compiled_path: Path, compile_log_path: Path
) -> tuple[int, int]:
    if not compile_log_path.is_file():
        raise TransactionCostError(
            f"MQL5コンパイルログがありません: return_code={return_code}"
        )
    log_text = compile_log_path.read_text(encoding="utf-16", errors="replace")
    match = re.search(r"Result:\s*(\d+) errors?,\s*(\d+) warnings?", log_text)
    if match is None:
        raise TransactionCostError(
            f"MQL5コンパイルログにResult行がありません: return_code={return_code}"
        )
    errors, warnings = (int(value) for value in match.groups())
    if errors != 0 or not compiled_path.is_file():
        raise TransactionCostError(
            "MQL5コンパイル失敗 "
            f"return_code={return_code} errors={errors} warnings={warnings}: {log_text}"
        )
    return errors, warnings


def validate_symbol_build_manifest(
    manifest: SymbolBuildManifest,
    *,
    source_symbol: str | None = None,
    custom_symbol: str | None = None,
    stress_points: int | None = None,
) -> None:
    failures: list[str] = []
    if manifest.schema_version != 2:
        failures.append(f"schema_version={manifest.schema_version}")
    if manifest.status != "success":
        failures.append(f"status={manifest.status} error={manifest.error}")
    if source_symbol is not None and manifest.source_symbol != source_symbol:
        failures.append(f"source_symbol={manifest.source_symbol}")
    if custom_symbol is not None and manifest.custom_symbol != custom_symbol:
        failures.append(f"custom_symbol={manifest.custom_symbol}")
    if stress_points is not None and manifest.stress_points != stress_points:
        failures.append(f"stress_points={manifest.stress_points}")
    if manifest.point <= 0 or manifest.tick_size <= 0:
        failures.append("Point/Tick Sizeが正ではありません")
    expected_stress_price = manifest.stress_points * manifest.point
    if abs(manifest.stress_price - expected_stress_price) > manifest.point / 100:
        failures.append(
            f"stress price不一致 actual={manifest.stress_price} "
            f"expected={expected_stress_price}"
        )
    if manifest.source_tick_count <= 0:
        failures.append("元ティックが0件です")
    if manifest.source_tick_count != manifest.written_tick_count:
        failures.append(
            "ティック件数不一致 "
            f"source={manifest.source_tick_count} written={manifest.written_tick_count}"
        )
    if manifest.written_tick_count != manifest.persisted_tick_count:
        failures.append(
            "永続化ティック件数不一致 "
            f"written={manifest.written_tick_count} "
            f"persisted={manifest.persisted_tick_count}"
        )
    if not (0 < manifest.first_time_msc <= manifest.last_time_msc):
        failures.append("先頭・末尾ティック時刻が不正です")
    if manifest.from_msc >= manifest.to_msc_exclusive:
        failures.append("生成期間が不正です")
    if not (
        manifest.from_msc
        <= manifest.first_time_msc
        <= manifest.last_time_msc
        < manifest.to_msc_exclusive
    ):
        failures.append("ティック時刻が生成期間外です")
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", manifest.source_tick_audit_fnv1a64):
        failures.append("元ティック監査ハッシュが不正です")
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", manifest.output_tick_audit_fnv1a64):
        failures.append("出力ティック監査ハッシュが不正です")
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", manifest.persisted_tick_audit_fnv1a64):
        failures.append("永続化ティック監査ハッシュが不正です")
    if (
        manifest.output_tick_audit_fnv1a64.casefold()
        != manifest.persisted_tick_audit_fnv1a64.casefold()
    ):
        failures.append("書込み前と永続化後のティック監査ハッシュが一致しません")
    if any(
        value != 0
        for value in (
            manifest.readback_mismatch_from_msc,
            manifest.readback_expected_day_count,
            manifest.readback_actual_day_count,
        )
    ):
        failures.append(
            "read-back不一致が記録されています "
            f"from={manifest.readback_mismatch_from_msc} "
            f"expected={manifest.readback_expected_day_count} "
            f"actual={manifest.readback_actual_day_count}"
        )
    if not (
        manifest.source_m1_bar_count
        == manifest.written_m1_bar_count
        == manifest.persisted_m1_bar_count
        > 0
    ):
        failures.append(
            "M1バー件数不一致 "
            f"source={manifest.source_m1_bar_count} "
            f"written={manifest.written_m1_bar_count} "
            f"persisted={manifest.persisted_m1_bar_count}"
        )
    if not (0 < manifest.first_m1_time <= manifest.last_m1_time):
        failures.append("M1バー先頭・末尾時刻が不正です")
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", manifest.source_m1_audit_fnv1a64):
        failures.append("元M1監査ハッシュが不正です")
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", manifest.output_m1_audit_fnv1a64):
        failures.append("出力M1監査ハッシュが不正です")
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", manifest.persisted_m1_audit_fnv1a64):
        failures.append("永続化M1監査ハッシュが不正です")
    if (
        manifest.output_m1_audit_fnv1a64.casefold()
        != manifest.persisted_m1_audit_fnv1a64.casefold()
    ):
        failures.append("書き込み前と永続化後のM1監査ハッシュが一致しません")
    if any(
        value != 0
        for value in (
            manifest.m1_readback_mismatch_from,
            manifest.m1_readback_expected_day_count,
            manifest.m1_readback_actual_day_count,
        )
    ):
        failures.append(
            "M1 read-back不一致が記録されています "
            f"from={manifest.m1_readback_mismatch_from} "
            f"expected={manifest.m1_readback_expected_day_count} "
            f"actual={manifest.m1_readback_actual_day_count}"
        )
    if (
        manifest.stress_points == 0
        and manifest.source_tick_audit_fnv1a64.casefold()
        != manifest.output_tick_audit_fnv1a64.casefold()
    ):
        failures.append("S=0で元・出力ティック監査ハッシュが一致しません")
    if (
        manifest.stress_points == 0
        and manifest.source_m1_audit_fnv1a64.casefold()
        != manifest.output_m1_audit_fnv1a64.casefold()
    ):
        failures.append("S=0で元・出力M1監査ハッシュが一致しません")
    if failures:
        raise TransactionCostError("シンボル生成ゲート失敗: " + "; ".join(failures))


class CustomSymbolBuilder:
    def __init__(
        self,
        scenario: Scenario,
        *,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        reference_tick_size: float | None = None,
        reference_first_m1_time: int | None = None,
    ) -> None:
        self.scenario = scenario
        self._process_runner = process_runner
        self.reference_tick_size = reference_tick_size
        self.reference_first_m1_time = reference_first_m1_time

    @property
    def metaeditor_path(self) -> Path:
        return self.scenario.terminal_path.parent / "MetaEditor64.exe"

    def _compile_script(self, run_directory: Path) -> tuple[Path, Path]:
        source = self.scenario.project_root / SCRIPT_SOURCE
        if not source.is_file():
            raise TransactionCostError(f"MQL5スクリプトがありません: {source}")
        if not self.metaeditor_path.is_file():
            raise TransactionCostError(f"MetaEditorがありません: {self.metaeditor_path}")
        compile_directory = run_directory / "compiler"
        compile_directory.mkdir(parents=True, exist_ok=False)
        compile_source = compile_directory / source.name
        shutil.copy2(source, compile_source)
        compile_log = compile_directory / "compile.log"
        command = [
            str(self.metaeditor_path),
            f"/compile:{compile_source}",
            f"/log:{compile_log}",
        ]
        try:
            completed = self._process_runner(
                command,
                cwd=self.metaeditor_path.parent,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TransactionCostError(f"MQL5スクリプトをコンパイルできません: {exc}") from exc
        compiled = compile_source.with_suffix(".ex5")
        validate_metaeditor_compile(
            return_code=completed.returncode,
            compiled_path=compiled,
            compile_log_path=compile_log,
        )
        return compiled, compile_log

    def build(
        self,
        *,
        stress_points: Iterable[int] = DEFAULT_STRESS_POINTS,
        from_date: str = DEFAULT_BUILD_FROM,
        to_date_exclusive: str = DEFAULT_BUILD_TO_EXCLUSIVE,
        timeout_seconds: int = 7200,
    ) -> Path:
        points = validate_generation_stress_points(stress_points)
        if timeout_seconds <= 0:
            raise TransactionCostError("timeout_secondsは正でなければなりません")
        validate_environment(self.scenario, require_stopped=True)
        output_root = (
            self.scenario.project_root
            / "data"
            / "transaction_cost_stress"
            / "symbol_builds"
        )
        output_root.mkdir(parents=True, exist_ok=True)
        run_directory = output_root / _timestamp_id()
        run_directory.mkdir(exist_ok=False)
        compiled, compile_log = self._compile_script(run_directory)
        build_token = uuid.uuid4().hex[:8]
        script_stem = f"BuildCostStressSymbol_{build_token}"
        staged_script = (
            self.scenario.data_directory
            / "MQL5"
            / "Scripts"
            / "mt5_ea_validator"
            / f"{script_stem}.ex5"
        )
        staged_script.parent.mkdir(parents=True, exist_ok=True)
        if staged_script.exists():
            raise TransactionCostError(
                f"一意なスクリプト配置先が既に存在します: {staged_script}"
            )
        shutil.copy2(compiled, staged_script)

        index: dict[str, object] = {
            "schema_version": 2,
            "status": "running",
            "source_symbol": self.scenario.symbol,
            "from_date": from_date,
            "to_date_exclusive": to_date_exclusive,
            "requested_stress_points": list(points),
            "script_source": str(self.scenario.project_root / SCRIPT_SOURCE),
            "script_source_sha256": sha256_file(self.scenario.project_root / SCRIPT_SOURCE),
            "compiled_script": str(compiled),
            "compiled_script_sha256": sha256_file(compiled),
            "compile_log": str(compile_log),
            "staged_script": str(staged_script),
            "symbols": [],
        }
        index_path = run_directory / "build_index.json"
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            for stress in points:
                level_directory = run_directory / f"stress_{stress:05d}"
                level_directory.mkdir(exist_ok=False)
                custom_symbol = f"XAUUSD_TCS{stress}_{build_token}"
                staging_relative = (
                    Path("mt5_ea_validator")
                    / "transaction_cost"
                    / run_directory.name
                    / custom_symbol
                    / "manifest.txt"
                )
                staging_manifest = (
                    self.scenario.data_directory / "MQL5" / "Files" / staging_relative
                )
                staging_manifest.parent.mkdir(parents=True, exist_ok=False)
                preset_name = f"{script_stem}_{stress}.set"
                preset_path = (
                    self.scenario.data_directory / "MQL5" / "Presets" / preset_name
                )
                write_mt5_unicode(
                    preset_path,
                    render_symbol_builder_set(
                        source_symbol=self.scenario.symbol,
                        custom_symbol=custom_symbol,
                        from_date=from_date,
                        to_date_exclusive=to_date_exclusive,
                        stress_points=stress,
                        output_file=staging_relative,
                        reference_tick_size=self.reference_tick_size,
                        reference_first_m1_time=self.reference_first_m1_time,
                    ),
                )
                ini_path = level_directory / "builder.ini"
                write_mt5_unicode(
                    ini_path,
                    render_symbol_builder_ini(
                        script_name=f"mt5_ea_validator\\{script_stem}",
                        preset_name=preset_name,
                    ),
                )
                try:
                    completed = self._process_runner(
                        [
                            str(self.scenario.terminal_path),
                            f"/config:{ini_path}",
                        ],
                        cwd=self.scenario.terminal_path.parent,
                        check=False,
                        timeout=timeout_seconds,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise TransactionCostError(
                        f"カスタムシンボル生成を実行できません: S={stress}: {exc}"
                    ) from exc
                if not staging_manifest.is_file():
                    raise TransactionCostError(
                        "MT5終了後に生成マニフェストがありません: "
                        f"S={stress} return_code={completed.returncode} {staging_manifest}"
                    )
                artifact_manifest = level_directory / "symbol_manifest.txt"
                shutil.copy2(staging_manifest, artifact_manifest)
                manifest = replace(
                    parse_symbol_build_manifest(artifact_manifest),
                    artifact_manifest=artifact_manifest,
                )
                validate_symbol_build_manifest(
                    manifest,
                    source_symbol=self.scenario.symbol,
                    custom_symbol=custom_symbol,
                    stress_points=stress,
                )
                symbols = index["symbols"]
                assert isinstance(symbols, list)
                symbols.append(
                    {
                        **manifest.to_dict(),
                        "mt5_return_code": completed.returncode,
                        "builder_ini": str(ini_path),
                        "preset_file": str(preset_path),
                    }
                )
                index_path.write_text(
                    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            index["status"] = "success"
        except Exception as exc:
            index["status"] = "failed"
            index["error"] = str(exc)
            raise
        finally:
            index_path.write_text(
                json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return index_path


def load_build_index(path: Path) -> tuple[SymbolBuildManifest, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransactionCostError(f"build indexを読み込めません: {path}: {exc}") from exc
    if raw.get("schema_version") != 2 or raw.get("status") != "success":
        raise TransactionCostError(
            "成功したschema v2 build indexではありません: "
            f"schema_version={raw.get('schema_version')} status={raw.get('status')}"
        )
    manifests: list[SymbolBuildManifest] = []
    for item in raw.get("symbols", []):
        manifest_values = {
            name: item[name]
            for name in SymbolBuildManifest.__dataclass_fields__
            if name != "artifact_manifest"
        }
        artifact_raw = item.get("artifact_manifest")
        manifest = SymbolBuildManifest(
            **manifest_values,
            artifact_manifest=Path(artifact_raw) if artifact_raw else None,
        )
        validate_symbol_build_manifest(manifest)
        manifests.append(manifest)
    if not manifests:
        raise TransactionCostError("build indexにシンボルがありません")
    validate_generation_stress_points(
        manifest.stress_points for manifest in manifests
    )
    source_hashes = {manifest.source_tick_audit_fnv1a64 for manifest in manifests}
    source_counts = {manifest.source_tick_count for manifest in manifests}
    source_m1_hashes = {
        manifest.source_m1_audit_fnv1a64 for manifest in manifests
    }
    source_m1_counts = {manifest.source_m1_bar_count for manifest in manifests}
    source_m1_periods = {
        (manifest.first_m1_time, manifest.last_m1_time) for manifest in manifests
    }
    periods = {
        (manifest.from_msc, manifest.to_msc_exclusive) for manifest in manifests
    }
    if (
        len(source_hashes) != 1
        or len(source_counts) != 1
        or len(source_m1_hashes) != 1
        or len(source_m1_counts) != 1
        or len(source_m1_periods) != 1
        or len(periods) != 1
    ):
        raise TransactionCostError(
            "ストレス水準間で元ティック/M1の件数・期間・監査ハッシュが一致しません"
        )
    return tuple(manifests)


def metric_deltas(
    baseline: ReportMetrics,
    actual: ReportMetrics,
    baseline_deals: DealAudit,
    actual_deals: DealAudit,
) -> dict[str, float | int]:
    return {
        "net_profit": round(actual.net_profit - baseline.net_profit, 10),
        "trades": actual.trades - baseline.trades,
        "profit_factor": round(actual.profit_factor - baseline.profit_factor, 10),
        "recovery_factor": round(actual.recovery_factor - baseline.recovery_factor, 10),
        "sharpe_ratio": round(actual.sharpe_ratio - baseline.sharpe_ratio, 10),
        "equity_drawdown_percent_points": round(
            actual.equity_drawdown_percent - baseline.equity_drawdown_percent, 10
        ),
        "deal_count": actual_deals.deal_count - baseline_deals.deal_count,
    }


class CostStressExecutor(MT5Executor):
    def __init__(
        self,
        scenario: Scenario,
        *,
        stress_points: int,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        super().__init__(scenario, process_runner=process_runner)
        self.stress_points = stress_points

    @property
    def commission_profile_source(self) -> Path:
        return self.scenario.project_root / COMMISSION_PROFILE_SOURCE

    @property
    def commission_profile_target(self) -> Path:
        return (
            self.scenario.data_directory
            / "MQL5"
            / "Profiles"
            / "Tester"
            / "Groups"
            / COMMISSION_PROFILE_NAME
        )

    def _stage_commission_profile(self) -> None:
        source = self.commission_profile_source
        if not source.is_file():
            raise TransactionCostError(f"Commission設定がありません: {source}")
        values = parse_set_values(read_set_text(source))
        failures = {
            key: (values.get(key), expected)
            for key, expected in REQUIRED_COMMISSION_VALUES.items()
            if values.get(key) != expected
        }
        if failures:
            details = ", ".join(
                f"{key}={actual!r} expected={expected!r}"
                for key, (actual, expected) in failures.items()
            )
            raise TransactionCostError(f"Commission設定の固定値が不正です: {details}")
        if self.commission_profile_target.exists():
            raise TransactionCostError(
                "既存Tester group設定は上書きしません: "
                f"{self.commission_profile_target}"
            )
        write_mt5_unicode(
            self.commission_profile_target,
            read_set_text(source),
        )

    def prepare(self) -> None:
        validate_environment(self.scenario, require_stopped=True)
        stage_dedicated_set(
            self.scenario.set_source,
            self.scenario.staged_set_path,
            self.scenario.required_set_values,
            label=f"{self.scenario.ea_id}/{self.scenario.wf}",
        )

    def execute(self, deposit: int, output_directory: Path) -> TestExecution:
        self._stage_commission_profile()
        try:
            execution = super().execute(deposit, output_directory)
            expected_capital = f"_{self.scenario.wf}_CAPITAL_{deposit}.htm"
            desired = execution.report_path.with_name(
                f"{self.scenario.artifact_prefix}_{self.scenario.wf}"
                f"_TCS_{self.stress_points}_{deposit}.htm"
            )
            if execution.report_path.name.endswith(expected_capital):
                execution.report_path.replace(desired)
                execution = replace(execution, report_path=desired)
            return execution
        finally:
            if output_directory.is_dir() and self.commission_profile_source.is_file():
                audit_path = output_directory / "tester_group_settings.txt"
                if not audit_path.exists():
                    shutil.copy2(self.commission_profile_source, audit_path)
            target = self.commission_profile_target
            if target.is_file():
                target_values = parse_set_values(read_set_text(target))
                source_values = parse_set_values(
                    read_set_text(self.commission_profile_source)
                )
                if target_values != source_values:
                    raise TransactionCostError(
                        "一時Commission設定が実行中に変更されたため削除しません: "
                        f"{target}"
                    )
                target.unlink()


def _replace_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _manifest_covers_scenario(
    build: SymbolBuildManifest, scenario: Scenario
) -> bool:
    start = datetime.strptime(scenario.from_date, "%Y.%m.%d").replace(
        tzinfo=timezone.utc
    )
    end_exclusive = datetime.strptime(scenario.to_date, "%Y.%m.%d").replace(
        tzinfo=timezone.utc
    ) + timedelta(days=1)
    return (
        build.from_msc <= int(start.timestamp() * 1000)
        and build.to_msc_exclusive >= int(end_exclusive.timestamp() * 1000)
    )


def _suite_summary(results: list[CostStressResult]) -> str:
    lines = [
        "# Transaction Cost Stress 第1層 結果",
        "",
        "不利約定価格・取引コストストレス。純粋な約定スリッページではない。",
        "",
        "| EA | WF | S(points/side) | Net Profit | Trades | PF | RF | Sharpe | Equity DD % | S=0 Gate |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        gate = "-"
        if result.zero_stress_gate is not None:
            gate = "PASS" if result.zero_stress_gate.passed else "FAIL"
        metrics = result.metrics
        lines.append(
            f"| {result.ea_id} | {result.wf} | {result.stress_points} | "
            f"{metrics.net_profit:.2f} | {metrics.trades} | "
            f"{metrics.profit_factor:.2f} | {metrics.recovery_factor:.2f} | "
            f"{metrics.sharpe_ratio:.2f} | {metrics.equity_drawdown_percent:.2f} | "
            f"{gate} |"
        )
    lines.append("")
    return "\n".join(lines)


def _load_completed_cost_result(
    result_path: Path,
    *,
    scenario: Scenario,
    build: SymbolBuildManifest,
) -> tuple[CostStressResult, dict[str, object]]:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransactionCostError(
            f"完了結果を読み込めません: {result_path}: {exc}"
        ) from exc
    expected_values = {
        "scenario_id": scenario.scenario_id,
        "ea_id": scenario.ea_id,
        "wf": scenario.wf,
        "stress_points": build.stress_points,
        "custom_symbol": build.custom_symbol,
        "deposit": scenario.benchmark.deposit,
    }
    mismatches = {
        key: (payload.get(key), expected)
        for key, expected in expected_values.items()
        if payload.get(key) != expected
    }
    if mismatches:
        raise TransactionCostError(
            f"再開結果の識別情報が一致しません: {result_path}: {mismatches}"
        )
    try:
        report_path = Path(payload["report_file"])
        ini_path = Path(payload["ini_file"])
        return_code = int(payload["mt5_return_code"])
        stored_metrics = ReportMetrics(**payload["metrics"])
        stored_deals = DealAudit(**payload["deal_audit"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TransactionCostError(
            f"再開結果の必須項目が不正です: {result_path}: {exc}"
        ) from exc
    if not report_path.is_file() or not ini_path.is_file():
        raise TransactionCostError(
            f"再開結果の成果物がありません: report={report_path} ini={ini_path}"
        )
    actual_metrics = parse_report(report_path)
    actual_deals = parse_deal_audit(report_path)
    if actual_metrics != stored_metrics or actual_deals != stored_deals:
        raise TransactionCostError(
            f"再開結果JSONとHTMLレポートが一致しません: {result_path}"
        )
    zero_gate = None
    if build.stress_points == 0:
        zero_gate = check_benchmark(actual_metrics, scenario.benchmark, actual_deals)
        if not zero_gate.passed:
            raise TransactionCostError(
                f"再開するS=0結果がSafety Gate不一致です: {scenario.scenario_id}"
            )
    execution = TestExecution(
        deposit=scenario.benchmark.deposit,
        ini_path=ini_path,
        report_path=report_path,
        return_code=return_code,
    )
    result = CostStressResult(
        scenario_id=scenario.scenario_id,
        ea_id=scenario.ea_id,
        wf=scenario.wf,
        stress_points=build.stress_points,
        custom_symbol=build.custom_symbol,
        deposit=scenario.benchmark.deposit,
        metrics=actual_metrics,
        deal_audit=actual_deals,
        zero_stress_gate=zero_gate,
        metric_deltas_from_zero=payload.get("metric_deltas_from_zero"),
        execution=execution,
    )
    return result, payload


def run_cost_stress_suite(
    scenarios: Iterable[Scenario],
    builds: Iterable[SymbolBuildManifest],
    *,
    executor_factory: Callable[[Scenario, int], CostStressExecutor] | None = None,
    now_factory: Callable[[], datetime] = lambda: datetime.now(JST),
    resume_directory: Path | None = None,
) -> Path:
    scenario_list = tuple(scenarios)
    build_list = tuple(builds)
    if not scenario_list:
        raise TransactionCostError("試験シナリオがありません")
    if not build_list:
        raise TransactionCostError("カスタムシンボルbuildがありません")
    validate_stress_points(build.stress_points for build in build_list)
    if len({scenario.scenario_id for scenario in scenario_list}) != len(scenario_list):
        raise TransactionCostError("scenario_idが重複しています")
    source_hashes = {build.source_tick_audit_fnv1a64 for build in build_list}
    source_counts = {build.source_tick_count for build in build_list}
    source_m1_hashes = {build.source_m1_audit_fnv1a64 for build in build_list}
    source_m1_counts = {build.source_m1_bar_count for build in build_list}
    source_m1_periods = {
        (build.first_m1_time, build.last_m1_time) for build in build_list
    }
    periods = {(build.from_msc, build.to_msc_exclusive) for build in build_list}
    if (
        len(source_hashes) != 1
        or len(source_counts) != 1
        or len(source_m1_hashes) != 1
        or len(source_m1_counts) != 1
        or len(source_m1_periods) != 1
        or len(periods) != 1
    ):
        raise TransactionCostError(
            "全ストレス水準で元ティック/M1の件数・期間・監査ハッシュが一致しません"
        )
    terminal_keys = {
        (
            str(scenario.terminal_path.resolve()).casefold(),
            str(scenario.data_directory.resolve()).casefold(),
        )
        for scenario in scenario_list
    }
    if len(terminal_keys) != 1:
        raise TransactionCostError("全シナリオは同じTitanFX MT5を使用してください")
    for build in build_list:
        validate_symbol_build_manifest(build, source_symbol="XAUUSD")
        uncovered = [
            scenario.scenario_id
            for scenario in scenario_list
            if not _manifest_covers_scenario(build, scenario)
        ]
        if uncovered:
            raise TransactionCostError(
                f"{build.custom_symbol}のティック期間がシナリオを包含しません: "
                + ", ".join(uncovered)
            )

    project_root = scenario_list[0].project_root
    output_root = project_root / "data" / "transaction_cost_stress" / "results"
    output_root.mkdir(parents=True, exist_ok=True)
    started = now_factory()
    baselines: dict[str, tuple[ReportMetrics, DealAudit]] = {}
    completed_results: list[CostStressResult] = []
    completed_keys: set[tuple[str, int]] = set()
    expected_scenario_ids = [scenario.scenario_id for scenario in scenario_list]
    expected_stress_points = [build.stress_points for build in build_list]
    if resume_directory is None:
        run_directory = output_root / _timestamp_id(started)
        run_directory.mkdir(exist_ok=False)
        suite: dict[str, object] = {
            "schema_version": 1,
            "status": "running",
            "method": "adverse execution-price proxy",
            "pure_slippage_test": False,
            "started_at_jst": started.isoformat(timespec="seconds"),
            "finished_at_jst": None,
            "scenario_ids": expected_scenario_ids,
            "stress_points": expected_stress_points,
            "deposit": 3000,
            "results": [],
            "error": None,
        }
    else:
        resolved_root = output_root.resolve()
        run_directory = resume_directory.resolve()
        if run_directory.parent != resolved_root or not run_directory.is_dir():
            raise TransactionCostError(
                f"再開先はresults直下の既存フォルダでなければなりません: {run_directory}"
            )
        manifest_path = run_directory / "suite_manifest.json"
        try:
            suite = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TransactionCostError(
                f"再開manifestを読み込めません: {manifest_path}: {exc}"
            ) from exc
        expected_manifest = {
            "schema_version": 1,
            "method": "adverse execution-price proxy",
            "pure_slippage_test": False,
            "scenario_ids": expected_scenario_ids,
            "stress_points": expected_stress_points,
            "deposit": 3000,
        }
        manifest_mismatches = {
            key: (suite.get(key), expected)
            for key, expected in expected_manifest.items()
            if suite.get(key) != expected
        }
        if manifest_mismatches:
            raise TransactionCostError(
                f"再開manifestと指定条件が一致しません: {manifest_mismatches}"
            )
        if suite.get("status") == "success":
            raise TransactionCostError("成功済みキャンペーンは再開できません")
        loaded_payloads: list[dict[str, object]] = []
        expected_result_paths: set[Path] = set()
        for build in build_list:
            for scenario in scenario_list:
                result_path = (
                    run_directory
                    / scenario.scenario_id
                    / f"stress_{build.stress_points:05d}"
                    / "result.json"
                )
                if not result_path.is_file():
                    continue
                result, payload = _load_completed_cost_result(
                    result_path, scenario=scenario, build=build
                )
                key = (scenario.scenario_id, build.stress_points)
                completed_keys.add(key)
                completed_results.append(result)
                loaded_payloads.append(payload)
                expected_result_paths.add(result_path.resolve())
                if build.stress_points == 0:
                    baselines[scenario.scenario_id] = (
                        result.metrics,
                        result.deal_audit,
                    )
        actual_result_paths = {
            path.resolve() for path in run_directory.rglob("result.json")
        }
        if actual_result_paths != expected_result_paths:
            unexpected = sorted(str(path) for path in actual_result_paths - expected_result_paths)
            raise TransactionCostError(
                "再開フォルダに指定条件外のresult.jsonがあります: " + ", ".join(unexpected)
            )
        suite["status"] = "running"
        suite["finished_at_jst"] = None
        suite["results"] = loaded_payloads
        suite["error"] = None
    suite_manifest_path = run_directory / "suite_manifest.json"
    _replace_json(suite_manifest_path, suite)
    factory = executor_factory or (
        lambda scenario, stress: CostStressExecutor(
            scenario, stress_points=stress
        )
    )
    try:
        for build in build_list:
            for base_scenario in scenario_list:
                key = (base_scenario.scenario_id, build.stress_points)
                if key in completed_keys:
                    continue
                deposit = base_scenario.benchmark.deposit
                if deposit != 3000:
                    raise TransactionCostError(
                        f"比較Depositは3000 USDでなければなりません: {base_scenario.scenario_id}"
                    )
                custom_scenario = replace(
                    base_scenario,
                    symbol=build.custom_symbol,
                    deposits=(deposit,),
                )
                output_directory = (
                    run_directory
                    / base_scenario.scenario_id
                    / f"stress_{build.stress_points:05d}"
                )
                if output_directory.exists():
                    interrupted_directory = output_directory.with_name(
                        output_directory.name
                        + "_interrupted_"
                        + _timestamp_id(now_factory())
                    )
                    output_directory.replace(interrupted_directory)
                staging_directory = (
                    custom_scenario.data_directory
                    / "MQL5"
                    / "Files"
                    / "mt5_ea_validator"
                    / output_directory.parent.name
                    / output_directory.name
                )
                if staging_directory.exists():
                    interrupted_staging = staging_directory.with_name(
                        staging_directory.name
                        + "_interrupted_"
                        + _timestamp_id(now_factory())
                    )
                    staging_directory.replace(interrupted_staging)
                executor = factory(custom_scenario, build.stress_points)
                executor.prepare()
                execution = executor.execute(deposit, output_directory)
                metrics = parse_report(execution.report_path)
                deals = parse_deal_audit(execution.report_path)
                zero_gate: BenchmarkCheck | None = None
                deltas: dict[str, float | int] | None = None
                if build.stress_points == 0:
                    zero_gate = check_benchmark(metrics, base_scenario.benchmark, deals)
                    baselines[base_scenario.scenario_id] = (metrics, deals)
                else:
                    baseline = baselines.get(base_scenario.scenario_id)
                    if baseline is None:
                        raise TransactionCostError(
                            f"S=0基準がありません: {base_scenario.scenario_id}"
                        )
                    deltas = metric_deltas(baseline[0], metrics, baseline[1], deals)
                result = CostStressResult(
                    scenario_id=base_scenario.scenario_id,
                    ea_id=base_scenario.ea_id,
                    wf=base_scenario.wf,
                    stress_points=build.stress_points,
                    custom_symbol=build.custom_symbol,
                    deposit=deposit,
                    metrics=metrics,
                    deal_audit=deals,
                    zero_stress_gate=zero_gate,
                    metric_deltas_from_zero=deltas,
                    execution=execution,
                )
                completed_results.append(result)
                result_payload = {
                    **result.to_dict(),
                    "build_manifest": build.to_dict(),
                    "set_source": str(base_scenario.set_source),
                    "set_sha256": sha256_file(base_scenario.set_source),
                    "ea_sha256": sha256_file(base_scenario.expert_binary),
                    "tester_group_settings": str(
                        output_directory / "tester_group_settings.txt"
                    ),
                }
                result_path = output_directory / "result.json"
                result_path.write_text(
                    json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                suite_results = suite["results"]
                assert isinstance(suite_results, list)
                suite_results.append(result_payload)
                _replace_json(suite_manifest_path, suite)
                if zero_gate is not None and not zero_gate.passed:
                    raise TransactionCostError(
                        "S=0 Safety Gateが不一致のため正のストレスを停止しました: "
                        f"{base_scenario.scenario_id}"
                    )
        suite["status"] = "success"
    except Exception as exc:
        suite["status"] = "failed"
        suite["error"] = str(exc)
        raise TransactionCostError(f"Transaction Cost Stress suite失敗: {exc}") from exc
    finally:
        suite["finished_at_jst"] = now_factory().isoformat(timespec="seconds")
        _replace_json(suite_manifest_path, suite)
        (run_directory / "summary.md").write_text(
            _suite_summary(completed_results), encoding="utf-8"
        )
    return run_directory
