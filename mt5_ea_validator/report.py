from __future__ import annotations

import re
import hashlib
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

from mt5_ea_validator.configuration import Benchmark


class ReportError(ValueError):
    """Raised when an MT5 report cannot be read or validated."""


@dataclass(frozen=True)
class ReportMetrics:
    net_profit: float
    trades: int
    profit_factor: float
    recovery_factor: float
    sharpe_ratio: float
    equity_drawdown_percent: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkCheck:
    passed: bool
    checks: dict[str, dict[str, float | int | bool | str]]

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "checks": self.checks}


@dataclass(frozen=True)
class DealAudit:
    deal_count: int
    deal_sequence_sha256: str
    commission_total: float
    swap_total: float
    deal_profit_total: float

    def to_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


class _TableCellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self.rows: list[list[str]] = []
        self._cell_depth = 0
        self._parts: list[str] = []
        self._current_row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"}:
            if self._cell_depth == 0:
                self._parts = []
            self._cell_depth += 1
        elif self._cell_depth and tag.lower() in {"br", "p", "div"}:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell_depth:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "tr":
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None
            return
        if tag.lower() not in {"td", "th"} or not self._cell_depth:
            return
        self._cell_depth -= 1
        if self._cell_depth == 0:
            text = " ".join("".join(self._parts).split())
            self.cells.append(text)
            if self._current_row is not None:
                self._current_row.append(text)
            self._parts = []


ALIASES = {
    "net_profit": (
        "total net profit",
        "総損益",
        "総純益",
        "総純利益",
        "純益",
    ),
    "trades": (
        "total trades",
        "総取引数",
        "取引数",
        "取引合計",
    ),
    "profit_factor": (
        "profit factor",
        "プロフィットファクター",
        "プロフィットファクタ",
    ),
    "recovery_factor": (
        "recovery factor",
        "リカバリーファクター",
        "リカバリーファクタ",
        "リカバリファクター",
        "リカバリファクタ",
    ),
    "sharpe_ratio": (
        "sharpe ratio",
        "シャープレシオ",
    ),
    "equity_drawdown_percent": (
        "equity drawdown relative",
        "relative equity drawdown",
        "エクイティドローダウン相対値",
        "相対エクイティドローダウン",
        "証拠金相対ドローダウン",
    ),
}


def _normalize_label(value: str) -> str:
    normalized = value.casefold().strip().rstrip(":：")
    return re.sub(r"\s+", " ", normalized)


def _decode_report(raw: bytes) -> str:
    encodings = []
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        encodings.append("utf-8-sig")
    encodings.extend(("utf-8", "utf-16-le", "cp932", "cp1252"))
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ReportError("HTMLレポートの文字コードを判定できません")


def _find_value(cells: list[str], aliases: tuple[str, ...]) -> str:
    normalized_aliases = {_normalize_label(alias) for alias in aliases}
    for index, cell in enumerate(cells):
        if _normalize_label(cell) not in normalized_aliases:
            continue
        for candidate in cells[index + 1 : index + 4]:
            if candidate.strip():
                return candidate.strip()
    raise ReportError(f"レポート指標が見つかりません: {aliases[0]}")


def _parse_number(value: str) -> float:
    match = re.search(r"[-+−]?\d[\d\s,]*(?:\.\d+)?", value)
    if not match:
        raise ReportError(f"数値を解析できません: {value!r}")
    normalized = match.group(0).replace("−", "-").replace(" ", "").replace(",", "")
    return float(normalized)


def _parse_percent(value: str) -> float:
    match = re.search(r"([-+−]?\d[\d\s,]*(?:\.\d+)?)\s*%", value)
    if not match:
        raise ReportError(f"パーセント値を解析できません: {value!r}")
    normalized = match.group(1).replace("−", "-").replace(" ", "").replace(",", "")
    return float(normalized)


def parse_report(path: Path) -> ReportMetrics:
    if not path.is_file():
        raise ReportError(f"HTMLレポートがありません: {path}")
    parser = _TableCellParser()
    parser.feed(_decode_report(path.read_bytes()))
    if not parser.cells:
        raise ReportError(f"HTMLレポートから表セルを取得できません: {path}")

    raw = {
        name: _find_value(parser.cells, aliases)
        for name, aliases in ALIASES.items()
    }
    return ReportMetrics(
        net_profit=_parse_number(raw["net_profit"]),
        trades=int(_parse_number(raw["trades"])),
        profit_factor=_parse_number(raw["profit_factor"]),
        recovery_factor=_parse_number(raw["recovery_factor"]),
        sharpe_ratio=_parse_number(raw["sharpe_ratio"]),
        equity_drawdown_percent=_parse_percent(raw["equity_drawdown_percent"]),
    )


def _decimal(value: str) -> Decimal:
    normalized = value.replace(" ", "").replace(",", "").strip()
    if not normalized:
        return Decimal("0")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ReportError(f"会計数値を解析できません: {value!r}") from exc


def deal_identity_sha256(identities: tuple[str, ...] | list[str]) -> str:
    """Hash every deal identity and duplicate count, independent of source row order."""
    payload = ("\n".join(sorted(identities)) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_deal_audit(path: Path) -> DealAudit:
    if not path.is_file():
        raise ReportError(f"HTMLレポートがありません: {path}")
    parser = _TableCellParser()
    parser.feed(_decode_report(path.read_bytes()))
    canonical: list[str] = []
    commission = Decimal("0")
    swap = Decimal("0")
    profit = Decimal("0")
    for row in parser.rows:
        if (
            len(row) < 13
            or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}", row[0])
            or row[3].casefold() not in {"buy", "sell"}
            or row[4].casefold() not in {"in", "out", "in/out"}
        ):
            continue
        direction = row[3].casefold()
        price = format(_decimal(row[6]), "f")
        canonical.append(f"{row[0]}|{direction}|{price}")
        commission += _decimal(row[8])
        swap += _decimal(row[9])
        profit += _decimal(row[10])
    return DealAudit(
        deal_count=len(canonical),
        deal_sequence_sha256=deal_identity_sha256(canonical),
        commission_total=float(commission),
        swap_total=float(swap),
        deal_profit_total=float(profit),
    )


def check_benchmark(
    metrics: ReportMetrics,
    benchmark: Benchmark,
    deal_audit: DealAudit,
) -> BenchmarkCheck:
    net_profit_tolerance = max(
        benchmark.net_profit_tolerance,
        abs(benchmark.net_profit) * benchmark.net_profit_tolerance_percent / 100,
    )
    specifications: dict[str, tuple[float | int, float | int, float]] = {
        "net_profit": (
            metrics.net_profit,
            benchmark.net_profit,
            net_profit_tolerance,
        ),
        "trades": (metrics.trades, benchmark.trades, 0),
    }
    optional_metrics = {
        "profit_factor": metrics.profit_factor,
        "recovery_factor": metrics.recovery_factor,
        "sharpe_ratio": metrics.sharpe_ratio,
        "equity_drawdown_percent": metrics.equity_drawdown_percent,
    }
    for name, actual in optional_metrics.items():
        expected = getattr(benchmark, name)
        tolerance = getattr(benchmark, f"{name}_tolerance")
        if expected is not None and tolerance is not None:
            specifications[name] = (actual, expected, tolerance)
    checks: dict[str, dict[str, float | int | bool | str]] = {}
    for name, (actual, expected, tolerance) in specifications.items():
        passed = abs(float(actual) - float(expected)) <= tolerance + 1e-12
        checks[name] = {
            "actual": actual,
            "expected": expected,
            "tolerance": tolerance,
            "passed": passed,
        }
    deal_count_passed = deal_audit.deal_count == benchmark.deal_count
    checks["deal_count"] = {
        "actual": deal_audit.deal_count,
        "expected": benchmark.deal_count,
        "tolerance": 0,
        "passed": deal_count_passed,
    }
    deal_sequence_passed = (
        deal_audit.deal_sequence_sha256.casefold()
        == benchmark.deal_sequence_sha256.casefold()
    )
    checks["deal_sequence_sha256"] = {
        "actual": deal_audit.deal_sequence_sha256,
        "expected": benchmark.deal_sequence_sha256,
        "passed": deal_sequence_passed,
    }
    return BenchmarkCheck(
        passed=all(bool(item["passed"]) for item in checks.values()),
        checks=checks,
    )
