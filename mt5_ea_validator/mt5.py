from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mt5_ea_validator.configuration import Scenario
from mt5_ea_validator.setfile import stage_dedicated_set, write_mt5_unicode


class MT5Error(RuntimeError):
    """Raised when MT5 cannot be executed safely or successfully."""


@dataclass(frozen=True)
class TestExecution:
    deposit: int
    ini_path: Path
    report_path: Path
    return_code: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def running_terminal_paths() -> tuple[Path, ...]:
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
        "$items=Get-Process -Name terminal64 -ErrorAction SilentlyContinue;"
        "foreach($item in $items){if($item.Path){Write-Output $item.Path}};"
        "exit 0"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MT5Error(f"MT5プロセスを確認できません: {exc}") from exc
    if result.returncode != 0:
        raise MT5Error(
            "MT5プロセス確認コマンドが失敗しました: " + result.stderr.strip()
        )
    return tuple(Path(line.strip()) for line in result.stdout.splitlines() if line.strip())


def target_terminal_is_running(scenario: Scenario) -> bool:
    target = str(scenario.terminal_path.resolve()).casefold()
    return any(str(path.resolve()).casefold() == target for path in running_terminal_paths())


def validate_environment(scenario: Scenario, *, require_stopped: bool = True) -> None:
    checks = {
        "terminal64.exe": scenario.terminal_path,
        "MT5データフォルダ": scenario.data_directory,
        "EA": scenario.expert_binary,
        f"QQ/{scenario.wf}専用set": scenario.set_source,
        "Testerプロファイルフォルダ": scenario.tester_profile_directory,
    }
    missing = [f"{name}: {path}" for name, path in checks.items() if not path.exists()]
    if missing:
        raise MT5Error("必要なMT5環境が見つかりません:\n" + "\n".join(missing))
    if require_stopped and target_terminal_is_running(scenario):
        raise MT5Error(
            "対象のTitanFX MT5が起動中です。保存して終了してから再実行してください: "
            f"{scenario.terminal_path}"
        )


def render_tester_ini(
    scenario: Scenario,
    *,
    deposit: int,
    report_path: Path,
) -> str:
    values = [
        "[Tester]",
        f"Expert={scenario.expert}",
        f"ExpertParameters={scenario.staged_set_name}",
        f"Symbol={scenario.symbol}",
        f"Period={scenario.period}",
        f"Optimization={scenario.optimization}",
        f"Model={scenario.model}",
        f"FromDate={scenario.from_date}",
        f"ToDate={scenario.to_date}",
        f"ForwardMode={scenario.forward_mode}",
        f"Deposit={deposit}",
        f"Currency={scenario.currency}",
        "ProfitInPips=0",
        f"Leverage={scenario.leverage}",
        f"ExecutionMode={scenario.execution_mode}",
        "OptimizationCriterion=7",
        "Visual=0",
        f"Report={report_path}",
        "ReplaceReport=0",
        "ShutdownTerminal=1",
        "",
    ]
    return "\n".join(values)


class MT5Executor:
    def __init__(
        self,
        scenario: Scenario,
        *,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.scenario = scenario
        self._process_runner = process_runner

    def prepare(self) -> None:
        validate_environment(self.scenario, require_stopped=True)
        stage_dedicated_set(
            self.scenario.set_source,
            self.scenario.staged_set_path,
            self.scenario.required_set_values,
            label=f"QQ/{self.scenario.wf}",
        )

    def execute(self, deposit: int, output_directory: Path) -> TestExecution:
        output_directory.mkdir(parents=True, exist_ok=False)
        report_path = output_directory / f"QQ_{self.scenario.wf}_CAPITAL_{deposit}.htm"
        ini_path = output_directory / "tester.ini"
        if report_path.exists():
            raise MT5Error(f"既存レポートは上書きしません: {report_path}")

        staging_relative_directory = (
            Path("MQL5")
            / "Files"
            / "mt5_ea_validator"
            / output_directory.parent.name
            / output_directory.name
        )
        staging_directory = self.scenario.data_directory / staging_relative_directory
        staging_directory.mkdir(parents=True, exist_ok=False)
        staging_report_path = staging_directory / report_path.name
        report_setting = staging_relative_directory / report_path.name
        write_mt5_unicode(
            ini_path,
            render_tester_ini(
                self.scenario,
                deposit=deposit,
                report_path=report_setting,
            ),
        )

        command = [
            str(self.scenario.terminal_path),
            f"/config:{ini_path}",
        ]
        try:
            completed = self._process_runner(
                command,
                cwd=self.scenario.terminal_path.parent,
                check=False,
                timeout=self.scenario.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise MT5Error(
                f"MT5テストが{self.scenario.timeout_seconds}秒でタイムアウトしました: Deposit={deposit}"
            ) from exc
        except OSError as exc:
            raise MT5Error(f"MT5を起動できません: {exc}") from exc

        if not staging_report_path.is_file():
            raise MT5Error(
                f"MT5終了後にHTMLレポートが生成されませんでした: {staging_report_path} "
                f"(return_code={completed.returncode})"
            )
        for generated in staging_directory.iterdir():
            destination = output_directory / generated.name
            if destination.exists():
                raise MT5Error(f"既存成果物は上書きしません: {destination}")
            shutil.move(str(generated), str(destination))
        staging_directory.rmdir()
        return TestExecution(
            deposit=deposit,
            ini_path=ini_path,
            report_path=report_path,
            return_code=completed.returncode,
        )
