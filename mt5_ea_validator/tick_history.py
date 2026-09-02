from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path


class TickHistoryError(ValueError):
    """Raised when a Tester log cannot be audited safely."""


class TickHistoryGateError(RuntimeError):
    def __init__(self, message: str, summary_path: Path) -> None:
        super().__init__(message)
        self.summary_path = summary_path


@dataclass(frozen=True)
class RealTickDataAudit:
    passed: bool
    symbol: str
    period: str
    from_date: str
    to_date: str
    required_coverage_end: str
    synchronized_from: str | None
    synchronized_to: str | None
    test_block_start_line: int | None
    test_block_end_line: int | None
    test_completed: bool
    fallback_warning_count: int
    fallback_warnings: tuple[str, ...]
    fallback_minute_count: int | None
    total_minute_bars: int | None
    fallback_ratio: float | None
    max_fallback_ratio: float
    sparse_fallback_tolerance_applied: bool
    reasons: tuple[str, ...]
    source_log: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["fallback_warnings"] = list(self.fallback_warnings)
        value["reasons"] = list(self.reasons)
        return value


def _parse_mt5_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y.%m.%d")
    except ValueError as exc:
        raise TickHistoryError(f"Invalid MT5 date: {value}") from exc


def required_coverage_end(to_date: str) -> str:
    """Return the final weekday strictly before MT5's ToDate boundary."""
    candidate = _parse_mt5_date(to_date) - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.strftime("%Y.%m.%d")


def _decode_log(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("utf-16-le")


def audit_latest_real_tick_test(
    log_path: Path,
    *,
    symbol: str,
    period: str,
    from_date: str,
    to_date: str,
    max_generated_fallback_ratio: float = 0.0,
) -> RealTickDataAudit:
    """Audit the latest completed Tester block matching one real-tick test."""
    if not 0.0 <= max_generated_fallback_ratio <= 1.0:
        raise TickHistoryError("max_generated_fallback_ratio must be between 0 and 1")
    if not log_path.is_file():
        raise TickHistoryError(f"Tester log does not exist: {log_path}")
    _parse_mt5_date(from_date)
    required_end = required_coverage_end(to_date)
    lines = _decode_log(log_path.read_bytes()).splitlines()
    start_pattern = re.compile(
        rf"{re.escape(symbol)},{re.escape(period)} \([^)]*\): testing of .+ "
        rf"from {re.escape(from_date)} 00:00 to {re.escape(to_date)} 00:00\s*$"
    )
    starts = [index for index, line in enumerate(lines) if start_pattern.search(line)]
    if not starts:
        return RealTickDataAudit(
            passed=False,
            symbol=symbol,
            period=period,
            from_date=from_date,
            to_date=to_date,
            required_coverage_end=required_end,
            synchronized_from=None,
            synchronized_to=None,
            test_block_start_line=None,
            test_block_end_line=None,
            test_completed=False,
            fallback_warning_count=0,
            fallback_warnings=(),
            fallback_minute_count=None,
            total_minute_bars=None,
            fallback_ratio=None,
            max_fallback_ratio=max_generated_fallback_ratio,
            sparse_fallback_tolerance_applied=False,
            reasons=("matching_test_block_not_found",),
            source_log=str(log_path.resolve()),
        )

    start = starts[-1]
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if "\tTester\tautomatic testing finished" in lines[index]
        ),
        None,
    )
    block = lines[start : (end + 1 if end is not None else len(lines))]
    coverage_pattern = re.compile(
        rf"{re.escape(symbol)}:\s+history ticks synchronized from "
        r"(\d{4}\.\d{2}\.\d{2}) to (\d{4}\.\d{2}\.\d{2})"
    )
    coverage_matches = [
        match
        for line in block
        if (match := coverage_pattern.search(line)) is not None
    ]
    synchronized_from = coverage_matches[-1].group(1) if coverage_matches else None
    synchronized_to = coverage_matches[-1].group(2) if coverage_matches else None
    warning_tokens = (
        "real ticks absent",
        "no real ticks within a day",
        "every tick generation used",
    )
    warnings = tuple(
        line.split("\t", 4)[-1].strip()
        for line in block
        if symbol.casefold() in line.casefold()
        and any(token in line.casefold() for token in warning_tokens)
    )
    fallback_pattern = re.compile(
        r"real ticks absent for (\d+) minutes of (\d+) total minute bars, "
        r"every tick generation used",
        re.IGNORECASE,
    )
    fallback_matches = [
        match
        for warning in warnings
        if (match := fallback_pattern.search(warning)) is not None
    ]
    fallback_minute_count = (
        int(fallback_matches[-1].group(1)) if fallback_matches else None
    )
    total_minute_bars = (
        int(fallback_matches[-1].group(2)) if fallback_matches else None
    )
    fallback_ratio = (
        fallback_minute_count / total_minute_bars
        if fallback_minute_count is not None and total_minute_bars
        else None
    )
    has_full_day_fallback = any(
        "no real ticks within a day" in warning.casefold() for warning in warnings
    )
    sparse_tolerance_applied = bool(
        warnings
        and not has_full_day_fallback
        and fallback_ratio is not None
        and max_generated_fallback_ratio > 0
        and fallback_ratio <= max_generated_fallback_ratio
    )
    reasons: list[str] = []
    if end is None:
        reasons.append("test_block_not_completed")
    if synchronized_from is None or synchronized_to is None:
        reasons.append("tick_synchronization_range_not_found")
    else:
        if _parse_mt5_date(synchronized_from) > _parse_mt5_date(from_date):
            reasons.append("tick_coverage_starts_after_from_date")
        if _parse_mt5_date(synchronized_to) < _parse_mt5_date(required_end):
            reasons.append("tick_coverage_ends_before_required_date")
    if warnings and not sparse_tolerance_applied:
        reasons.append("generated_tick_fallback_detected")

    return RealTickDataAudit(
        passed=not reasons,
        symbol=symbol,
        period=period,
        from_date=from_date,
        to_date=to_date,
        required_coverage_end=required_end,
        synchronized_from=synchronized_from,
        synchronized_to=synchronized_to,
        test_block_start_line=start + 1,
        test_block_end_line=end + 1 if end is not None else None,
        test_completed=end is not None,
        fallback_warning_count=len(warnings),
        fallback_warnings=warnings,
        fallback_minute_count=fallback_minute_count,
        total_minute_bars=total_minute_bars,
        fallback_ratio=fallback_ratio,
        max_fallback_ratio=max_generated_fallback_ratio,
        sparse_fallback_tolerance_applied=sparse_tolerance_applied,
        reasons=tuple(reasons),
        source_log=str(log_path.resolve()),
    )


def audit_saved_ubs_run(run_directory: Path) -> Path:
    """Audit all completed results in a saved UBS run without changing its manifest."""
    manifest_path = run_directory / "run_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TickHistoryError(f"Cannot read UBS run manifest: {exc}") from exc
    conditions = manifest.get("conditions")
    results = manifest.get("results")
    if not isinstance(conditions, dict) or not isinstance(results, list):
        raise TickHistoryError(f"Invalid UBS run manifest: {manifest_path}")
    if int(conditions.get("model", -1)) != 4:
        raise TickHistoryError("Real-tick audit requires MT5 model=4")

    audits: list[dict[str, object]] = []
    for result in results:
        snapshots = result.get("log_snapshots", [])
        tester_logs = [
            Path(str(path))
            for path in snapshots
            if Path(str(path)).name.startswith("tester_")
        ]
        if len(tester_logs) != 1:
            raise TickHistoryError(
                f"Expected one Tester log for {result.get('strategy_id')}, "
                f"found {len(tester_logs)}"
            )
        audit = audit_latest_real_tick_test(
            tester_logs[0],
            symbol=str(conditions["symbol"]),
            period=str(conditions["period"]),
            from_date=str(conditions["from_date"]),
            to_date=str(conditions["to_date"]),
        )
        audits.append(
            {
                "strategy_id": str(result["strategy_id"]),
                "audit": audit.to_dict(),
            }
        )
    summary = {
        "schema_version": 1,
        "run_id": manifest.get("run_id"),
        "source_manifest": str(manifest_path.resolve()),
        "status": "pass" if audits and all(item["audit"]["passed"] for item in audits) else "fail",
        "audited_result_count": len(audits),
        "results": audits,
    }
    output = run_directory / "tick_data_quality_summary.json"
    if output.exists():
        raise TickHistoryError(f"Tick audit already exists: {output}")
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if summary["status"] != "pass":
        raise TickHistoryGateError("UBS tick-data quality gate failed", output)
    return output
