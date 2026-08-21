from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised when a scenario configuration is invalid."""


@dataclass(frozen=True)
class Benchmark:
    deposit: int
    net_profit: float
    net_profit_tolerance: float
    net_profit_tolerance_percent: float
    trades: int
    deal_count: int
    deal_sequence_sha256: str
    profit_factor: float | None = None
    profit_factor_tolerance: float | None = None
    recovery_factor: float | None = None
    recovery_factor_tolerance: float | None = None
    sharpe_ratio: float | None = None
    sharpe_ratio_tolerance: float | None = None
    equity_drawdown_percent: float | None = None
    equity_drawdown_percent_tolerance: float | None = None


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    ea_id: str
    artifact_prefix: str
    wf: str
    project_root: Path
    terminal_path: Path
    data_directory: Path
    expert: str
    expert_binary: Path
    set_source: Path
    staged_set_name: str
    output_root: Path
    symbol: str
    period: str
    from_date: str
    to_date: str
    model: int
    execution_mode: int
    leverage: int
    currency: str
    optimization: int
    forward_mode: int
    deposits: tuple[int, ...]
    timeout_seconds: int
    required_set_values: dict[str, str]
    benchmark: Benchmark

    @property
    def tester_profile_directory(self) -> Path:
        return self.data_directory / "MQL5" / "Profiles" / "Tester"

    @property
    def staged_set_path(self) -> Path:
        return self.tester_profile_directory / self.staged_set_name


WF_PERIODS = {
    "WF1": ("2025.07.01", "2025.09.30"),
    "WF2": ("2025.10.01", "2025.12.31"),
    "WF3": ("2026.01.01", "2026.03.31"),
    "WF4": ("2026.04.01", "2026.06.30"),
}

def _find_project_root(config_path: Path) -> Path:
    for candidate in (config_path.parent, *config_path.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise ConfigurationError(
        f"pyproject.tomlが見つからず、プロジェクトルートを特定できません: {config_path}"
    )


def _resolve_project_path(project_root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else project_root / path


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ConfigurationError(f"必須設定がありません: {key}")
    return data[key]


def load_scenario(path: str | Path) -> Scenario:
    config_path = Path(path).resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"設定ファイルを読み込めません: {config_path}: {exc}"
        ) from exc

    project_root = _find_project_root(config_path)
    benchmark = Benchmark(**_require(raw, "benchmark"))
    required_set_values_raw = _require(raw, "required_set_values")
    if not isinstance(required_set_values_raw, dict):
        raise ConfigurationError("required_set_valuesはオブジェクトで指定してください")

    scenario = Scenario(
        scenario_id=str(_require(raw, "scenario_id")),
        ea_id=str(_require(raw, "ea_id")),
        artifact_prefix=str(_require(raw, "artifact_prefix")),
        wf=str(_require(raw, "wf")).upper(),
        project_root=project_root,
        terminal_path=Path(_require(raw, "terminal_path")),
        data_directory=Path(_require(raw, "data_directory")),
        expert=str(_require(raw, "expert")),
        expert_binary=Path(_require(raw, "data_directory"))
        / Path(_require(raw, "expert_binary")),
        set_source=_resolve_project_path(project_root, _require(raw, "set_source")),
        staged_set_name=str(_require(raw, "staged_set_name")),
        output_root=_resolve_project_path(project_root, _require(raw, "output_root")),
        symbol=str(_require(raw, "symbol")),
        period=str(_require(raw, "period")),
        from_date=str(_require(raw, "from_date")),
        to_date=str(_require(raw, "to_date")),
        model=int(_require(raw, "model")),
        execution_mode=int(_require(raw, "execution_mode")),
        leverage=int(_require(raw, "leverage")),
        currency=str(_require(raw, "currency")),
        optimization=int(_require(raw, "optimization")),
        forward_mode=int(_require(raw, "forward_mode")),
        deposits=tuple(int(value) for value in _require(raw, "deposits")),
        timeout_seconds=int(_require(raw, "timeout_seconds")),
        required_set_values={
            str(name): str(value)
            for name, value in required_set_values_raw.items()
        },
        benchmark=benchmark,
    )
    validate_scenario(scenario)
    return scenario


def select_target_deposit(scenario: Scenario, target_deposit: int) -> Scenario:
    """Return a run-only scenario for benchmark gate followed by one target."""
    if target_deposit <= 0:
        raise ConfigurationError("target deposit must be a positive integer")
    if target_deposit == scenario.benchmark.deposit:
        raise ConfigurationError("target deposit must differ from benchmark deposit")
    selected = replace(
        scenario,
        deposits=(scenario.benchmark.deposit, target_deposit),
    )
    validate_scenario(selected)
    return selected


def validate_scenario(scenario: Scenario) -> None:
    expected = {
        "model": (scenario.model, 4),
        "execution_mode": (scenario.execution_mode, 0),
        "optimization": (scenario.optimization, 0),
        "forward_mode": (scenario.forward_mode, 0),
        "leverage": (scenario.leverage, 500),
    }
    invalid = [name for name, (actual, wanted) in expected.items() if actual != wanted]
    if invalid:
        details = ", ".join(f"{name}={getattr(scenario, name)}" for name in invalid)
        raise ConfigurationError(
            f"{scenario.ea_id}/{scenario.wf}固定条件に適合していません: {details}"
        )
    if scenario.symbol != "XAUUSD" or scenario.period != "H1":
        raise ConfigurationError(
            f"{scenario.ea_id}/{scenario.wf}はXAUUSD/H1に固定されています"
        )
    if scenario.wf not in WF_PERIODS:
        raise ConfigurationError(f"未対応のWFです: {scenario.wf}")
    if (scenario.from_date, scenario.to_date) != WF_PERIODS[scenario.wf]:
        raise ConfigurationError(
            f"{scenario.ea_id}/{scenario.wf}期間が計画値と一致しません"
        )
    if not scenario.deposits or scenario.deposits[0] != scenario.benchmark.deposit:
        raise ConfigurationError(
            "最初のDepositはベンチマークDepositでなければなりません"
        )
    if len(set(scenario.deposits)) != len(scenario.deposits):
        raise ConfigurationError("Depositが重複しています")
    if any(deposit <= 0 for deposit in scenario.deposits):
        raise ConfigurationError("Depositは正の整数でなければなりません")
    if scenario.timeout_seconds <= 0:
        raise ConfigurationError("timeout_secondsは正の整数でなければなりません")
    if Path(scenario.staged_set_name).name != scenario.staged_set_name:
        raise ConfigurationError("staged_set_nameにはファイル名だけを指定してください")

    if not scenario.ea_id.strip():
        raise ConfigurationError("ea_idを空にはできません")
    if (
        not scenario.artifact_prefix
        or not scenario.artifact_prefix.replace("_", "").isalnum()
    ):
        raise ConfigurationError(
            "artifact_prefixには英数字とアンダースコアだけを指定してください"
        )
    if not scenario.required_set_values:
        raise ConfigurationError("required_set_valuesを空にはできません")

    optional_pairs = (
        ("profit_factor", "profit_factor_tolerance"),
        ("recovery_factor", "recovery_factor_tolerance"),
        ("sharpe_ratio", "sharpe_ratio_tolerance"),
        ("equity_drawdown_percent", "equity_drawdown_percent_tolerance"),
    )
    for expected_name, tolerance_name in optional_pairs:
        expected_value = getattr(scenario.benchmark, expected_name)
        tolerance_value = getattr(scenario.benchmark, tolerance_name)
        if (expected_value is None) != (tolerance_value is None):
            raise ConfigurationError(
                f"{expected_name}と{tolerance_name}は両方指定するか両方省略してください"
            )
