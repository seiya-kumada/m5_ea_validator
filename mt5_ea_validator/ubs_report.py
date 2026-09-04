from __future__ import annotations

import csv
import html
import json
from dataclasses import dataclass
from pathlib import Path


STRATEGIES = (
    "xau_sr_scalp_h1",
    "xau_h1_c5",
    "daily_l",
    "e",
    "goldtradepro_a",
    "mt5_longterm_e",
    "mt5_longterm_j",
)
WFS = ("WF1", "WF2", "WF3", "WF4")
WF_PERIODS = {
    "WF1": "2025-07-01～2025-09-30",
    "WF2": "2025-10-01～2025-12-31",
    "WF3": "2026-01-01～2026-03-31",
    "WF4": "2026-04-01～2026-06-30",
}
CHART_FILES = (
    "annual-net-profit.svg",
    "model-profit-delta.svg",
    "quarterly-net-profit.svg",
    "real-tick-risk-return.svg",
)
SAME_RISK_CHART_FILES = (
    "annual-equity-dd.svg",
    "annual-risk-return.svg",
    "quarterly-net-profit.svg",
    "quarterly-equity-dd.svg",
)


class UBSReportError(ValueError):
    """Raised when saved UBS evidence cannot produce a trustworthy report."""


@dataclass(frozen=True)
class Metrics:
    net_profit: float
    trades: int
    profit_factor: float
    recovery_factor: float
    sharpe_ratio: float
    equity_drawdown_percent: float


@dataclass(frozen=True)
class QuarterlyResult:
    wf: str
    strategy_id: str
    metrics: Metrics


@dataclass(frozen=True)
class ModelResult:
    model: int
    model_label: str
    strategy_id: str
    metrics: Metrics


@dataclass(frozen=True)
class ModelPair:
    strategy_id: str
    ohlc_net_profit: float
    real_ticks_net_profit: float
    net_profit_delta: float
    ohlc_trades: int
    real_ticks_trades: int
    profit_factor_delta: float
    recovery_factor_delta: float
    sharpe_ratio_delta: float
    equity_drawdown_delta: float
    deal_sequence_same: bool


@dataclass(frozen=True)
class SameRiskAnnualResult:
    strategy_id: str
    selected_lot: float
    selection_status: str
    selection_reason: str
    metrics: Metrics


@dataclass(frozen=True)
class SameRiskQuarterlyResult:
    wf: str
    strategy_id: str
    selected_lot: float
    selection_status: str
    metrics: Metrics
    dd_warning: bool


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UBSReportError(f"Cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UBSReportError(f"{label} must be a JSON object: {path}")
    return value


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    except OSError as exc:
        raise UBSReportError(f"Cannot read {label}: {path}: {exc}") from exc


def _metrics(row: dict[str, str]) -> Metrics:
    try:
        return Metrics(
            net_profit=float(row["net_profit"]),
            trades=int(float(row["trades"])),
            profit_factor=float(row["profit_factor"]),
            recovery_factor=float(row["recovery_factor"]),
            sharpe_ratio=float(row["sharpe_ratio"]),
            equity_drawdown_percent=float(row["equity_drawdown_percent"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UBSReportError(f"Invalid metrics row: {row}: {exc}") from exc


def _load_quarterly(run: Path) -> list[QuarterlyResult]:
    manifest = _load_json(run / "suite_manifest.json", "quarterly manifest")
    if manifest.get("status") != "success":
        raise UBSReportError("Quarterly manifest is not successful")
    rows = _read_csv(run / "quarterly_summary.csv", "quarterly summary")
    results = [
        QuarterlyResult(
            wf=row.get("wf", ""),
            strategy_id=row.get("strategy_id", ""),
            metrics=_metrics(row),
        )
        for row in rows
    ]
    expected = {(wf, strategy) for wf in WFS for strategy in STRATEGIES}
    actual = {(row.wf, row.strategy_id) for row in results}
    if len(results) != 28 or actual != expected:
        raise UBSReportError(
            f"Quarterly summary must contain 28 unique WF/strategy rows: "
            f"rows={len(results)}, missing={sorted(expected - actual)}"
        )
    return results


def _load_models(run: Path) -> tuple[list[ModelResult], list[ModelPair], dict[str, object]]:
    manifest = _load_json(run / "suite_manifest.json", "model manifest")
    if manifest.get("status") != "success":
        raise UBSReportError("Model-comparison manifest is not successful")
    rows = _read_csv(run / "model_comparison_summary.csv", "model summary")
    results: list[ModelResult] = []
    for row in rows:
        try:
            model = int(row["model"])
        except (KeyError, ValueError) as exc:
            raise UBSReportError(f"Invalid model row: {row}") from exc
        results.append(
            ModelResult(
                model=model,
                model_label=row.get("model_label", ""),
                strategy_id=row.get("strategy_id", ""),
                metrics=_metrics(row),
            )
        )
    expected = {(model, strategy) for model in (1, 4) for strategy in STRATEGIES}
    actual = {(row.model, row.strategy_id) for row in results}
    if len(results) != 14 or actual != expected:
        raise UBSReportError(
            f"Model summary must contain 14 unique model/strategy rows: "
            f"rows={len(results)}, missing={sorted(expected - actual)}"
        )

    pair_rows = _read_csv(run / "model_comparison_pairs.csv", "model pairs")
    pairs: list[ModelPair] = []
    for row in pair_rows:
        try:
            pairs.append(
                ModelPair(
                    strategy_id=row["strategy_id"],
                    ohlc_net_profit=float(row["ohlc_net_profit"]),
                    real_ticks_net_profit=float(row["real_ticks_net_profit"]),
                    net_profit_delta=float(
                        row["net_profit_delta_real_minus_ohlc"]
                    ),
                    ohlc_trades=int(float(row["ohlc_trades"])),
                    real_ticks_trades=int(float(row["real_ticks_trades"])),
                    profit_factor_delta=float(
                        row["profit_factor_delta_real_minus_ohlc"]
                    ),
                    recovery_factor_delta=float(
                        row["recovery_factor_delta_real_minus_ohlc"]
                    ),
                    sharpe_ratio_delta=float(
                        row["sharpe_ratio_delta_real_minus_ohlc"]
                    ),
                    equity_drawdown_delta=float(
                        row["equity_drawdown_percent_delta_real_minus_ohlc"]
                    ),
                    deal_sequence_same=row["deal_sequence_same"].casefold() == "true",
                )
            )
        except (KeyError, ValueError) as exc:
            raise UBSReportError(f"Invalid model pair row: {row}: {exc}") from exc
    if len(pairs) != 7 or {pair.strategy_id for pair in pairs} != set(STRATEGIES):
        raise UBSReportError("Model-pair summary must contain all seven strategies")

    manifest_results = manifest.get("results")
    if not isinstance(manifest_results, list) or len(manifest_results) != 14:
        raise UBSReportError("Model manifest must contain 14 results")
    failed = [item for item in manifest_results if item.get("status") != "success"]
    tick_failed = [
        item
        for item in manifest_results
        if item.get("model") == 4
        and (
            not isinstance(item.get("tick_data_quality"), dict)
            or item["tick_data_quality"].get("passed") is not True
        )
    ]
    if failed or tick_failed:
        raise UBSReportError(
            f"Model manifest contains failed results: cases={len(failed)}, "
            f"tick_gates={len(tick_failed)}"
        )
    return results, pairs, manifest


def _metrics_from_object(value: object, label: str) -> Metrics:
    if not isinstance(value, dict):
        raise UBSReportError(f"{label} metrics must be an object")
    try:
        return Metrics(
            net_profit=float(value["net_profit"]),
            trades=int(value["trades"]),
            profit_factor=float(value["profit_factor"]),
            recovery_factor=float(value["recovery_factor"]),
            sharpe_ratio=float(value["sharpe_ratio"]),
            equity_drawdown_percent=float(value["equity_drawdown_percent"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UBSReportError(f"Invalid {label} metrics: {value}: {exc}") from exc


def _load_same_risk_selection(
    selection_path: Path,
) -> tuple[list[SameRiskAnnualResult], dict[str, object]]:
    selection = _load_json(selection_path, "same-risk selection")
    if selection.get("schema_version") != 2:
        raise UBSReportError("Same-risk selection schema must be version 2")
    raw_selections = selection.get("selections")
    if not isinstance(raw_selections, list):
        raise UBSReportError("Same-risk selections must be a list")
    strategy_ids = {
        item.get("strategy_id")
        for item in raw_selections
        if isinstance(item, dict)
    }
    if len(raw_selections) != 7 or strategy_ids != set(STRATEGIES):
        raise UBSReportError("Same-risk selection must contain all seven strategies")

    results: list[SameRiskAnnualResult] = []
    by_strategy = {
        str(item["strategy_id"]): item
        for item in raw_selections
        if isinstance(item, dict)
    }
    for strategy_id in STRATEGIES:
        item = by_strategy[strategy_id]
        try:
            selected_lot = float(item["selected_lot"])
            result_path = Path(str(item["selected_result_file"]))
            selection_status = str(item["selection_status"])
            selection_reason = str(item["selection_reason"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UBSReportError(
                f"Invalid same-risk selection for {strategy_id}: {item}"
            ) from exc
        saved = _load_json(result_path, f"selected annual result {strategy_id}")
        if saved.get("status") != "success" or saved.get("strategy_id") != strategy_id:
            raise UBSReportError(
                f"Selected annual result is not successful: {strategy_id}"
            )
        for gate in ("compatibility", "tick_data_quality"):
            audit = saved.get(gate)
            if not isinstance(audit, dict) or audit.get("passed") is not True:
                raise UBSReportError(
                    f"Selected annual {gate} did not pass: {strategy_id}"
                )
        volume = saved.get("entry_volume_audit")
        if not isinstance(volume, dict):
            raise UBSReportError(
                f"Selected annual entry-volume audit is missing: {strategy_id}"
            )
        try:
            minimum_volume = float(volume["minimum_entry_volume"])
            maximum_volume = float(volume["maximum_entry_volume"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UBSReportError(
                f"Invalid selected annual entry volumes: {strategy_id}"
            ) from exc
        if minimum_volume != selected_lot or maximum_volume != selected_lot:
            raise UBSReportError(
                f"Selected annual entry lot does not match: {strategy_id}"
            )
        metrics = _metrics_from_object(saved.get("metrics"), strategy_id)
        snapshot_fields = {
            "net_profit": metrics.net_profit,
            "trades": metrics.trades,
            "profit_factor": metrics.profit_factor,
            "equity_drawdown_percent": metrics.equity_drawdown_percent,
        }
        for name, expected in snapshot_fields.items():
            try:
                actual = float(item[name])
            except (KeyError, TypeError, ValueError) as exc:
                raise UBSReportError(
                    f"Selection snapshot is invalid: {strategy_id}.{name}"
                ) from exc
            if abs(actual - float(expected)) > 1e-9:
                raise UBSReportError(
                    f"Selection snapshot does not match result: "
                    f"{strategy_id}.{name}"
                )
        results.append(
            SameRiskAnnualResult(
                strategy_id=strategy_id,
                selected_lot=selected_lot,
                selection_status=selection_status,
                selection_reason=selection_reason,
                metrics=metrics,
            )
        )
    return results, selection


def _load_same_risk_quarterly(
    run: Path,
    annual: list[SameRiskAnnualResult],
) -> tuple[list[SameRiskQuarterlyResult], dict[str, object]]:
    manifest = _load_json(run / "run_manifest.json", "same-risk quarterly manifest")
    if manifest.get("status") != "success":
        raise UBSReportError("Same-risk quarterly manifest is not successful")
    raw_results = manifest.get("results")
    if not isinstance(raw_results, list) or len(raw_results) != 28:
        raise UBSReportError("Same-risk quarterly manifest must contain 28 results")
    expected = {(wf, strategy) for wf in WFS for strategy in STRATEGIES}
    actual = {
        (item.get("wf"), item.get("strategy_id"))
        for item in raw_results
        if isinstance(item, dict)
    }
    if actual != expected:
        raise UBSReportError("Same-risk quarterly manifest is incomplete")
    selected_lots = {row.strategy_id: row.selected_lot for row in annual}
    for item in raw_results:
        if not isinstance(item, dict) or item.get("status") != "success":
            raise UBSReportError("Same-risk quarterly result is not successful")
        strategy_id = str(item.get("strategy_id"))
        for gate in ("compatibility", "tick_data_quality"):
            audit = item.get(gate)
            if not isinstance(audit, dict) or audit.get("passed") is not True:
                raise UBSReportError(
                    f"Quarterly {gate} did not pass: {item.get('case_id')}"
                )
        volume = item.get("entry_volume_audit")
        if not isinstance(volume, dict):
            raise UBSReportError(
                f"Quarterly entry-volume audit is missing: {item.get('case_id')}"
            )
        try:
            minimum_volume = float(volume["minimum_entry_volume"])
            maximum_volume = float(volume["maximum_entry_volume"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UBSReportError(
                f"Invalid quarterly entry volumes: {item.get('case_id')}"
            ) from exc
        if (
            minimum_volume != selected_lots[strategy_id]
            or maximum_volume != selected_lots[strategy_id]
        ):
            raise UBSReportError(
                f"Quarterly entry lot does not match: {item.get('case_id')}"
            )

    rows = _read_csv(
        run / "quarterly_same_risk_summary.csv", "same-risk quarterly summary"
    )
    quarterly: list[SameRiskQuarterlyResult] = []
    for row in rows:
        try:
            wf = row["wf"]
            strategy_id = row["strategy_id"]
            selected_lot = float(row["selected_lot"])
            warning = row["dd_warning_above_7_5_percent"].casefold() == "true"
        except (KeyError, TypeError, ValueError) as exc:
            raise UBSReportError(f"Invalid same-risk quarterly row: {row}") from exc
        metrics = _metrics(row)
        if selected_lot != selected_lots.get(strategy_id):
            raise UBSReportError(
                f"Quarterly summary lot does not match: {wf}_{strategy_id}"
            )
        if warning != (metrics.equity_drawdown_percent > 7.5):
            raise UBSReportError(
                f"Quarterly DD warning does not match: {wf}_{strategy_id}"
            )
        quarterly.append(
            SameRiskQuarterlyResult(
                wf=wf,
                strategy_id=strategy_id,
                selected_lot=selected_lot,
                selection_status=row.get("selection_status", ""),
                metrics=metrics,
                dd_warning=warning,
            )
        )
    summary_keys = {(row.wf, row.strategy_id) for row in quarterly}
    if len(quarterly) != 28 or summary_keys != expected:
        raise UBSReportError(
            "Same-risk quarterly summary must contain 28 unique rows"
        )
    summary = _load_json(
        run / "quarterly_same_risk_summary.json",
        "same-risk quarterly JSON summary",
    )
    if summary.get("case_count") != 28 or summary.get("warning_count") != 1:
        raise UBSReportError("Same-risk quarterly JSON summary is inconsistent")
    return quarterly, manifest


def _svg_style() -> str:
    return """<style>
    .bg { fill: #ffffff; }
    text { font-family: 'Segoe UI', 'Yu Gothic UI', sans-serif; fill: #111827; font-weight: 400; }
    .title { font-size: 28px; font-weight: 500; }
    .subtitle { font-size: 18px; fill: #374151; }
    .label { font-size: 18px; }
    .axis-label { font-size: 16px; fill: #374151; }
    .value { font-size: 17px; font-weight: 500; paint-order: stroke; stroke: #ffffff; stroke-width: 4px; stroke-linejoin: round; }
    .grid { stroke: #d1d5db; stroke-width: 1; }
    .axis { stroke: #4b5563; stroke-width: 1.5; }
    .ohlc { fill: #2563eb; }
    .real { fill: #0f766e; }
    .negative { fill: #b91c1c; }
    .positive { fill: #047857; }
    .below-target { fill: #64748b; }
    .target-band { fill: #d1fae5; opacity: 0.75; }
    .target-line { stroke: #047857; stroke-width: 3; }
    .warning-line { stroke: #b91c1c; stroke-width: 3; stroke-dasharray: 10 7; }
    .warning-cell { fill: #fecaca; }
    .negative-cell { fill: #fecaca; }
    .point { fill: #0f766e; stroke: #ffffff; stroke-width: 2; }
    .heat-1 { fill: #dbeafe; } .heat-2 { fill: #93c5fd; }
    .heat-3 { fill: #60a5fa; } .heat-4 { fill: #2563eb; }
    .heat-value { font-size: 17px; font-weight: 500; paint-order: stroke; stroke: #ffffff; stroke-width: 4px; stroke-linejoin: round; }
    @media (prefers-color-scheme: dark) {
      .bg { fill: #17191d; }
      text { fill: #f3f4f6; }
      .subtitle, .axis-label { fill: #d1d5db; }
      .value, .heat-value { stroke: #17191d; }
      .grid { stroke: #4b5563; } .axis { stroke: #d1d5db; }
      .ohlc { fill: #60a5fa; } .real { fill: #34d399; }
      .negative { fill: #f87171; } .positive { fill: #34d399; }
      .below-target { fill: #94a3b8; }
      .target-band { fill: #14532d; opacity: 0.7; }
      .target-line { stroke: #34d399; }
      .warning-line { stroke: #f87171; }
      .warning-cell, .negative-cell { fill: #7f1d1d; }
      .point { fill: #34d399; stroke: #17191d; }
      .heat-1 { fill: #1e3a5f; } .heat-2 { fill: #1d4f7a; }
      .heat-3 { fill: #246b9e; } .heat-4 { fill: #3182bd; }
    }
  </style>"""


def _svg_document(title: str, description: str, body: list[str], height: int) -> str:
    safe_title = html.escape(title)
    safe_description = html.escape(description)
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" '
            f'viewBox="0 0 1200 {height}" role="img" aria-labelledby="chart-title chart-desc">',
            f'  <title id="chart-title">{safe_title}</title>',
            f'  <desc id="chart-desc">{safe_description}</desc>',
            _svg_style(),
            f'  <rect class="bg" x="0" y="0" width="1200" height="{height}" />',
            *body,
            "</svg>",
            "",
        ]
    )


def _annual_profit_svg(pairs: list[ModelPair]) -> str:
    width = 820.0
    origin = 285.0
    maximum = max(max(pair.ohlc_net_profit, pair.real_ticks_net_profit) for pair in pairs)
    body = [
        '  <text class="title" x="55" y="42">約1年 Net Profit：OHLCと実ティック</text>',
        '  <text class="subtitle" x="55" y="72">同一戦略・同一期間・Deposit 3,000 USD（単位：USD）</text>',
        '  <rect class="ohlc" x="805" y="42" width="24" height="16" />',
        '  <text class="axis-label" x="838" y="57">1 minute OHLC</text>',
        '  <rect class="real" x="1010" y="42" width="24" height="16" />',
        '  <text class="axis-label" x="1043" y="57">実ティック</text>',
    ]
    for tick in range(0, 1201, 300):
        x = origin + width * tick / 1200
        body.extend(
            [
                f'  <line class="grid" x1="{x:.1f}" y1="95" x2="{x:.1f}" y2="665" />',
                f'  <text class="axis-label" x="{x:.1f}" y="692" text-anchor="middle">{tick:,}</text>',
            ]
        )
    for index, pair in enumerate(pairs):
        top = 112 + index * 78
        label = html.escape(pair.strategy_id)
        ohlc_width = width * pair.ohlc_net_profit / maximum
        real_width = width * pair.real_ticks_net_profit / maximum
        body.extend(
            [
                f'  <text class="label" x="55" y="{top + 29}">{label}</text>',
                f'  <rect class="ohlc" x="{origin}" y="{top}" width="{ohlc_width:.1f}" height="25" rx="3" />',
                f'  <text class="value" x="{origin + ohlc_width + 9:.1f}" y="{top + 19}">{pair.ohlc_net_profit:,.2f}</text>',
                f'  <rect class="real" x="{origin}" y="{top + 31}" width="{real_width:.1f}" height="25" rx="3" />',
                f'  <text class="value" x="{origin + real_width + 9:.1f}" y="{top + 50}">{pair.real_ticks_net_profit:,.2f}</text>',
            ]
        )
    body.append('  <text class="axis-label" x="695" y="725" text-anchor="middle">Net Profit (USD)</text>')
    return _svg_document(
        "約1年Net ProfitのOHLCと実ティック比較",
        "7戦略について、1 minute OHLCとEvery tick based on real ticksのNet Profitを横棒で比較する。",
        body,
        750,
    )


def _profit_delta_svg(pairs: list[ModelPair]) -> str:
    center = 660.0
    half_width = 475.0
    maximum = max(abs(pair.net_profit_delta) for pair in pairs)
    body = [
        '  <text class="title" x="55" y="42">実ティックによるNet Profit変化</text>',
        '  <text class="subtitle" x="55" y="72">差額 = 実ティックのNet Profit − 1 minute OHLCのNet Profit（単位：USD）</text>',
        f'  <line class="axis" x1="{center}" y1="95" x2="{center}" y2="625" />',
        f'  <text class="axis-label" x="{center}" y="656" text-anchor="middle">0</text>',
    ]
    for index, pair in enumerate(pairs):
        top = 112 + index * 70
        length = half_width * abs(pair.net_profit_delta) / maximum
        if pair.net_profit_delta < 0:
            x = center - length
            css = "negative"
            if x < 250:
                anchor = "start"
                label_x = x + 12
            else:
                anchor = "end"
                label_x = x - 10
        else:
            x = center
            css = "positive"
            anchor = "start"
            label_x = center + length + 10
        body.extend(
            [
                f'  <text class="label" x="55" y="{top + 24}">{html.escape(pair.strategy_id)}</text>',
                f'  <rect class="{css}" x="{x:.1f}" y="{top}" width="{length:.1f}" height="34" rx="3" />',
                f'  <text class="value" x="{label_x:.1f}" y="{top + 24}" text-anchor="{anchor}">{pair.net_profit_delta:+,.2f}</text>',
            ]
        )
    return _svg_document(
        "実ティックによるNet Profit変化",
        "実ティックのNet ProfitからOHLCのNet Profitを引いた差。左向きは減少、右向きは増加を示す。",
        body,
        690,
    )


def _quarterly_svg(rows: list[QuarterlyResult]) -> str:
    by_key = {(row.strategy_id, row.wf): row.metrics.net_profit for row in rows}
    maximum = max(by_key.values())
    left = 315
    cell_width = 190
    cell_height = 66
    body = [
        '  <text class="title" x="55" y="42">四半期別Net Profit</text>',
        '  <text class="subtitle" x="55" y="72">各四半期をDeposit 3,000 USDから独立開始（単位：USD）</text>',
    ]
    for column, wf in enumerate(WFS):
        x = left + column * cell_width
        body.extend(
            [
                f'  <text class="label" x="{x + cell_width / 2:.1f}" y="108" text-anchor="middle">{wf}</text>',
                f'  <text class="axis-label" x="{x + cell_width / 2:.1f}" y="132" text-anchor="middle">{WF_PERIODS[wf]}</text>',
            ]
        )
    for row_index, strategy in enumerate(STRATEGIES):
        y = 148 + row_index * cell_height
        body.append(
            f'  <text class="label" x="55" y="{y + 40}">{html.escape(strategy)}</text>'
        )
        for column, wf in enumerate(WFS):
            value = by_key[(strategy, wf)]
            ratio = value / maximum
            level = 1 if ratio < 0.2 else 2 if ratio < 0.4 else 3 if ratio < 0.7 else 4
            x = left + column * cell_width
            body.extend(
                [
                    f'  <rect class="heat-{level}" x="{x + 4}" y="{y + 4}" width="{cell_width - 8}" height="{cell_height - 8}" rx="4" />',
                    f'  <text class="heat-value" x="{x + cell_width / 2:.1f}" y="{y + 41}" text-anchor="middle">{value:,.2f}</text>',
                ]
            )
    body.append(
        '  <text class="axis-label" x="695" y="650" text-anchor="middle">色が濃いほど同一表内のNet Profitが大きい</text>'
    )
    return _svg_document(
        "四半期別Net Profit",
        "7戦略とWF1からWF4までのNet Profitをヒートマップで示す。全セルがプラスである。",
        body,
        685,
    )


def _risk_return_svg(models: list[ModelResult]) -> str:
    real = {row.strategy_id: row.metrics for row in models if row.model == 4}
    left, top, width, height = 120.0, 105.0, 970.0, 500.0
    max_dd, max_profit = 12.0, 800.0
    body = [
        '  <text class="title" x="55" y="42">実ティック：Net ProfitとEquity DD</text>',
        '  <text class="subtitle" x="55" y="72">右ほどDDが大きく、上ほど利益が大きい（原setのリスク量は未統一）</text>',
    ]
    for dd in range(0, 13, 2):
        x = left + width * dd / max_dd
        body.extend(
            [
                f'  <line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + height}" />',
                f'  <text class="axis-label" x="{x:.1f}" y="{top + height + 30}" text-anchor="middle">{dd}%</text>',
            ]
        )
    for profit in range(0, 801, 200):
        y = top + height - height * profit / max_profit
        body.extend(
            [
                f'  <line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + width}" y2="{y:.1f}" />',
                f'  <text class="axis-label" x="{left - 15}" y="{y + 6:.1f}" text-anchor="end">{profit}</text>',
            ]
        )
    label_offsets = {
        "xau_sr_scalp_h1": (14, -12),
        "xau_h1_c5": (14, -14),
        "daily_l": (14, 25),
        "e": (14, -12),
        "goldtradepro_a": (-14, -14),
        "mt5_longterm_e": (14, 25),
        "mt5_longterm_j": (-14, 25),
    }
    for strategy in STRATEGIES:
        metrics = real[strategy]
        x = left + width * metrics.equity_drawdown_percent / max_dd
        y = top + height - height * metrics.net_profit / max_profit
        dx, dy = label_offsets[strategy]
        anchor = "end" if dx < 0 else "start"
        body.extend(
            [
                f'  <circle class="point" cx="{x:.1f}" cy="{y:.1f}" r="8" />',
                f'  <text class="value" x="{x + dx:.1f}" y="{y + dy:.1f}" text-anchor="{anchor}">{html.escape(strategy)}  {metrics.net_profit:,.0f}</text>',
            ]
        )
    body.extend(
        [
            f'  <line class="axis" x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}" />',
            f'  <line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + height}" />',
            '  <text class="axis-label" x="605" y="680" text-anchor="middle">Equity Drawdown (%)</text>',
            '  <text class="axis-label" x="25" y="355" transform="rotate(-90 25 355)" text-anchor="middle">Net Profit (USD)</text>',
        ]
    )
    return _svg_document(
        "実ティックのNet ProfitとEquity Drawdown",
        "7戦略の実ティックNet Profitを縦軸、Equity Drawdownを横軸に示す散布図。",
        body,
        715,
    )


def _same_risk_annual_dd_svg(rows: list[SameRiskAnnualResult]) -> str:
    left, width, maximum = 285.0, 820.0, 14.0
    band_x = left + width * 4.5 / maximum
    band_width = width * 1.0 / maximum
    target_x = left + width * 5.0 / maximum
    body = [
        '  <text class="title" x="55" y="42">約1年 Equity DD：5%目標への調整結果</text>',
        '  <text class="subtitle" x="55" y="72">目標帯 4.5～5.5%／固定ロット・実ティック（単位：%）</text>',
        f'  <rect class="target-band" x="{band_x:.1f}" y="96" width="{band_width:.1f}" height="480" />',
        f'  <line class="target-line" x1="{target_x:.1f}" y1="96" x2="{target_x:.1f}" y2="576" />',
    ]
    for tick in range(0, 15, 2):
        x = left + width * tick / maximum
        body.extend(
            [
                f'  <line class="grid" x1="{x:.1f}" y1="96" x2="{x:.1f}" y2="576" />',
                f'  <text class="axis-label" x="{x:.1f}" y="610" text-anchor="middle">{tick}%</text>',
            ]
        )
    for index, row in enumerate(rows):
        top = 113 + index * 65
        bar_width = width * row.metrics.equity_drawdown_percent / maximum
        css = (
            "real"
            if row.selection_status == "within_target"
            else "negative"
            if row.selection_status == "minimum_lot_limited_above"
            else "below-target"
        )
        body.extend(
            [
                f'  <text class="label" x="55" y="{top + 24}">{html.escape(row.strategy_id)}</text>',
                f'  <rect class="{css}" x="{left}" y="{top}" width="{bar_width:.1f}" height="32" rx="3" />',
                f'  <text class="value" x="{left + bar_width + 10:.1f}" y="{top + 23}">{row.metrics.equity_drawdown_percent:.2f}%  ({row.selected_lot:.2f} lot)</text>',
            ]
        )
    body.extend(
        [
            f'  <text class="axis-label" x="{target_x:.1f}" y="640" text-anchor="middle">中央線：目標5.0%</text>',
            '  <text class="axis-label" x="695" y="672" text-anchor="middle">緑＝目標帯内、灰＝ロット刻みにより下側、赤＝最低ロットでも上限超過</text>',
        ]
    )
    return _svg_document(
        "約1年Equity DDの同一リスク調整結果",
        "7戦略の固定ロットによる約1年Equity Drawdown。4戦略が目標帯、2戦略がロット刻みにより下側、goldtradepro_aは最低ロットでも上限超過。",
        body,
        705,
    )


def _same_risk_annual_risk_return_svg(
    rows: list[SameRiskAnnualResult],
) -> str:
    left, top, width, height = 120.0, 105.0, 970.0, 500.0
    max_dd, max_profit = 12.0, 1400.0
    band_x = left + width * 4.5 / max_dd
    band_width = width * 1.0 / max_dd
    body = [
        '  <text class="title" x="55" y="42">固定ロット後の約1年リスク・リターン</text>',
        '  <text class="subtitle" x="55" y="72">右ほどEquity DDが大きく、上ほどNet Profitが大きい</text>',
        f'  <rect class="target-band" x="{band_x:.1f}" y="{top}" width="{band_width:.1f}" height="{height}" />',
    ]
    for dd in range(0, 13, 2):
        x = left + width * dd / max_dd
        body.extend(
            [
                f'  <line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + height}" />',
                f'  <text class="axis-label" x="{x:.1f}" y="{top + height + 30}" text-anchor="middle">{dd}%</text>',
            ]
        )
    for profit in range(0, 1401, 200):
        y = top + height - height * profit / max_profit
        body.extend(
            [
                f'  <line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + width}" y2="{y:.1f}" />',
                f'  <text class="axis-label" x="{left - 15}" y="{y + 6:.1f}" text-anchor="end">{profit:,}</text>',
            ]
        )
    offsets = {
        "xau_sr_scalp_h1": (14, 30),
        "xau_h1_c5": (14, -14),
        "daily_l": (14, -14),
        "e": (-14, -15),
        "goldtradepro_a": (-14, -14),
        "mt5_longterm_e": (-14, 28),
        "mt5_longterm_j": (-14, -14),
    }
    for row in rows:
        x = left + width * row.metrics.equity_drawdown_percent / max_dd
        y = top + height - height * row.metrics.net_profit / max_profit
        dx, dy = offsets[row.strategy_id]
        anchor = "end" if dx < 0 else "start"
        body.extend(
            [
                f'  <circle class="point" cx="{x:.1f}" cy="{y:.1f}" r="8" />',
                f'  <text class="value" x="{x + dx:.1f}" y="{y + dy:.1f}" text-anchor="{anchor}">{html.escape(row.strategy_id)}  {row.metrics.net_profit:,.0f}</text>',
            ]
        )
    body.extend(
        [
            f'  <line class="axis" x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}" />',
            f'  <line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + height}" />',
            '  <text class="axis-label" x="605" y="680" text-anchor="middle">Equity Drawdown (%)</text>',
            '  <text class="axis-label" x="25" y="355" transform="rotate(-90 25 355)" text-anchor="middle">Net Profit (USD)</text>',
        ]
    )
    return _svg_document(
        "固定ロット後の約1年リスク・リターン",
        "固定ロットで実行した7戦略のNet ProfitとEquity Drawdown。緑の縦帯は4.5から5.5パーセントの目標帯。",
        body,
        715,
    )


def _same_risk_quarterly_profit_svg(
    rows: list[SameRiskQuarterlyResult],
) -> str:
    by_key = {(row.strategy_id, row.wf): row.metrics.net_profit for row in rows}
    maximum = max(by_key.values())
    left, cell_width, cell_height = 315, 190, 66
    body = [
        '  <text class="title" x="55" y="42">固定ロット：四半期別Net Profit</text>',
        '  <text class="subtitle" x="55" y="72">各WFをDeposit 3,000 USDから独立開始（単位：USD）</text>',
    ]
    for column, wf in enumerate(WFS):
        x = left + column * cell_width
        body.extend(
            [
                f'  <text class="label" x="{x + cell_width / 2:.1f}" y="108" text-anchor="middle">{wf}</text>',
                f'  <text class="axis-label" x="{x + cell_width / 2:.1f}" y="132" text-anchor="middle">{WF_PERIODS[wf]}</text>',
            ]
        )
    for row_index, strategy in enumerate(STRATEGIES):
        y = 148 + row_index * cell_height
        body.append(
            f'  <text class="label" x="55" y="{y + 40}">{html.escape(strategy)}</text>'
        )
        for column, wf in enumerate(WFS):
            value = by_key[(strategy, wf)]
            ratio = max(value, 0.0) / maximum
            level = 1 if ratio < 0.2 else 2 if ratio < 0.4 else 3 if ratio < 0.7 else 4
            css = "negative-cell" if value < 0 else f"heat-{level}"
            x = left + column * cell_width
            body.extend(
                [
                    f'  <rect class="{css}" x="{x + 4}" y="{y + 4}" width="{cell_width - 8}" height="{cell_height - 8}" rx="4" />',
                    f'  <text class="heat-value" x="{x + cell_width / 2:.1f}" y="{y + 41}" text-anchor="middle">{value:+,.2f}</text>',
                ]
            )
    body.append(
        '  <text class="axis-label" x="695" y="650" text-anchor="middle">青が濃いほど利益が大きい／赤は赤字</text>'
    )
    return _svg_document(
        "固定ロットの四半期別Net Profit",
        "7戦略のWF1からWF4までのNet Profit。28件中27件が黒字で、mt5_longterm_eのWF4だけがマイナス0.02 USD。",
        body,
        685,
    )


def _same_risk_quarterly_dd_svg(
    rows: list[SameRiskQuarterlyResult],
) -> str:
    by_key = {
        (row.strategy_id, row.wf): row.metrics.equity_drawdown_percent
        for row in rows
    }
    left, cell_width, cell_height = 315, 190, 66
    body = [
        '  <text class="title" x="55" y="42">固定ロット：四半期別Equity DD</text>',
        '  <text class="subtitle" x="55" y="72">7.5%超を警告（単位：%）</text>',
    ]
    for column, wf in enumerate(WFS):
        x = left + column * cell_width
        body.extend(
            [
                f'  <text class="label" x="{x + cell_width / 2:.1f}" y="108" text-anchor="middle">{wf}</text>',
                f'  <text class="axis-label" x="{x + cell_width / 2:.1f}" y="132" text-anchor="middle">{WF_PERIODS[wf]}</text>',
            ]
        )
    for row_index, strategy in enumerate(STRATEGIES):
        y = 148 + row_index * cell_height
        body.append(
            f'  <text class="label" x="55" y="{y + 40}">{html.escape(strategy)}</text>'
        )
        for column, wf in enumerate(WFS):
            value = by_key[(strategy, wf)]
            level = 1 if value <= 2.5 else 2 if value <= 4.0 else 3 if value <= 5.5 else 4
            css = "warning-cell" if value > 7.5 else f"heat-{level}"
            x = left + column * cell_width
            body.extend(
                [
                    f'  <rect class="{css}" x="{x + 4}" y="{y + 4}" width="{cell_width - 8}" height="{cell_height - 8}" rx="4" />',
                    f'  <text class="heat-value" x="{x + cell_width / 2:.1f}" y="{y + 41}" text-anchor="middle">{value:.2f}%</text>',
                ]
            )
    body.append(
        '  <text class="axis-label" x="695" y="650" text-anchor="middle">赤＝7.5%警戒線超過（WF4 goldtradepro_a：13.28%）</text>'
    )
    return _svg_document(
        "固定ロットの四半期別Equity Drawdown",
        "7戦略のWF1からWF4までのEquity Drawdown。goldtradepro_aのWF4だけが7.5パーセント警戒線を超えた。",
        body,
        685,
    )


def _money(value: float) -> str:
    return f"{value:+,.2f}"


def _percent_change(pair: ModelPair) -> float:
    return 100.0 * pair.net_profit_delta / abs(pair.ohlc_net_profit)


def _report_markdown(
    quarterly: list[QuarterlyResult],
    models: list[ModelResult],
    pairs: list[ModelPair],
    manifest: dict[str, object],
    quarterly_run: Path,
    model_run: Path,
) -> str:
    model_by_key = {(row.model, row.strategy_id): row.metrics for row in models}
    quarter_by_key = {(row.wf, row.strategy_id): row.metrics for row in quarterly}
    decreasing = sum(pair.net_profit_delta < 0 for pair in pairs)
    increasing = sum(pair.net_profit_delta > 0 for pair in pairs)
    largest = max(pairs, key=lambda item: abs(item.net_profit_delta))
    tick_results = [
        item for item in manifest["results"] if item.get("model") == 4
    ]
    tick_quality = tick_results[0]["tick_data_quality"]
    completed_at = str(manifest.get("finished_at_jst", ""))

    lines = [
        "# Ultimate Breakout System Gold 7戦略比較 — 現在結果 暫定総合報告書",
        "",
        "- 実行状態: **SUCCESS**",
        "- 対象: Ultimate Breakout System v7.5 demo / XAUUSD用7戦略",
        "- 主要比較: 四半期28件＋約1年モデリング方式比較14件＝42件",
        "- 約1年期間: 2025-07-01～2026-06-30",
        "- Deposit / Currency / Leverage: 3,000 USD / USD / 1:500",
        "- 実行完了: " + completed_at,
        "- 報告書の位置づけ: Phase 0～2完了時点の暫定評価",
        "",
        "## 1. 目的",
        "",
        "本報告書の目的は、Ultimate Breakout System（UBS）のXAUUSD用set 7個について、原setの戦略設定を維持した状態で、収益性、四半期安定性、実ティック感応度、リスク効率を比較することである。",
        "",
        "単純なNet Profit順位だけでなく、`1 minute OHLC`から`Every tick based on real ticks`へ変更したときに成績や取引系列がどの程度変化するかを確認し、次段階の同一リスク比較で重点的に検証すべき戦略を特定する。",
        "",
        "本段階ではsetごとの資金管理設定を揃えていない。そのため、結果は**原設定の特徴比較**であり、最終的な優劣順位ではない。",
        "",
        "## 2. 結果",
        "",
        "### 2.1 総合結論",
        "",
        "- 7戦略すべてが4四半期すべてで黒字だった。",
        "- 約1年の連続テストでも、7戦略すべてがOHLC・実ティックの両方で黒字を維持した。",
        f"- 実ティックではOHLC比で{decreasing}戦略の利益が減少し、{increasing}戦略の利益が増加した。",
        f"- 最大のモデル差は`{largest.strategy_id}`の{_money(largest.net_profit_delta)} USD（{_percent_change(largest):+.2f}%）だった。",
        "- Tradesは6戦略で同数だったが、deal系列SHA-256は7戦略すべてで異なった。取引数が同じでも、日時・方向・約定価格の少なくとも一部が変化している。",
        "- `xau_h1_c5`と`mt5_longterm_j`は実ティックでNet Profitが増加し、モデル差が比較的小さい。",
        "- `goldtradepro_a`は高利益を維持したが、実ティックEquity DDが10.86%で、他戦略よりリスク量が大きい。",
        "- `xau_sr_scalp_h1`はOHLC利益が最大だった一方、実ティックで利益が50.85%減少し、モデル依存性が最も大きい。",
        "",
        "### 2.2 約1年のモデリング方式比較",
        "",
        "![約1年Net ProfitのOHLCと実ティック比較](assets/ubs_gold_strategy_comparison/annual-net-profit.svg)",
        "",
        "| 戦略 | OHLC NP | 実ティック NP | 差額 | 差率 | Trades | 実ティック PF | 実ティック RF | 実ティック DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    pair_by_strategy = {pair.strategy_id: pair for pair in pairs}
    for strategy in STRATEGIES:
        pair = pair_by_strategy[strategy]
        real = model_by_key[(4, strategy)]
        lines.append(
            f"| {strategy} | {_money(pair.ohlc_net_profit)} | "
            f"{_money(pair.real_ticks_net_profit)} | {_money(pair.net_profit_delta)} | "
            f"{_percent_change(pair):+.2f}% | {pair.ohlc_trades} → {pair.real_ticks_trades} | "
            f"{real.profit_factor:.2f} | {real.recovery_factor:.2f} | "
            f"{real.equity_drawdown_percent:.2f}% |"
        )
    lines.extend(
        [
            "",
            "![実ティックによるNet Profit変化](assets/ubs_gold_strategy_comparison/model-profit-delta.svg)",
            "",
            "OHLCから実ティックへの変更で、`xau_sr_scalp_h1`と`e`は特に大きく利益が低下した。反対に`xau_h1_c5`と`mt5_longterm_j`は利益を維持または増加させた。",
            "",
            "### 2.3 実ティックでの利益とDrawdown",
            "",
            "![実ティックのNet ProfitとEquity DD](assets/ubs_gold_strategy_comparison/real-tick-risk-return.svg)",
            "",
            "左上ほど低DD・高利益だが、原setのロット計算と最大保有数が異なるため、この図だけで最終順位を決めない。`xau_h1_c5`は実ティックNP 450.40 USD、DD 1.87%で、現設定では比較的良好な位置にある。`goldtradepro_a`はNP 726.71 USDと高いが、DDも10.86%である。",
            "",
            "### 2.4 四半期安定性",
            "",
            "![四半期別Net Profit](assets/ubs_gold_strategy_comparison/quarterly-net-profit.svg)",
            "",
            "| 戦略 | WF1 | WF2 | WF3 | WF4 | 4期合計 | 黒字期 | 最大DD |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for strategy in STRATEGIES:
        values = [quarter_by_key[(wf, strategy)] for wf in WFS]
        lines.append(
            f"| {strategy} | "
            + " | ".join(_money(value.net_profit) for value in values)
            + f" | {_money(sum(value.net_profit for value in values))} | "
            f"{sum(value.net_profit > 0 for value in values)}/4 | "
            f"{max(value.equity_drawdown_percent for value in values):.2f}% |"
        )
    lines.extend(
        [
            "",
            "全28ケースが黒字だった。ただし、`xau_sr_scalp_h1`のWF1はPF 1.05、`mt5_longterm_j`のWF2はPF 1.02と損益分岐点に近い。`goldtradepro_a`はWF4でPF 1.17、Equity DD 13.21%まで悪化しており、期間依存性に注意が必要である。",
            "",
            "## 3. 詳細",
            "",
            "### 3.1 対象と固定条件",
            "",
            "| 項目 | 条件 |",
            "|---|---|",
            "| EA | Ultimate Breakout System v7.5 demo |",
            "| Symbol / Tester時間足 | XAUUSD / H1 |",
            "| Deposit / Currency / Leverage | 3,000 USD / USD / 1:500 |",
            "| Execution | No Delay |",
            "| set | v6系原setを保持し、v7.5で不足する表示入力だけをTester基準値で補った忠実拡張set |",
            "| 四半期モデル | 1 minute OHLC |",
            "| 約1年モデル | 1 minute OHLC / Every tick based on real ticks |",
            "| WF1 | 2025-07-01～2025-09-30 |",
            "| WF2 | 2025-10-01～2025-12-31 |",
            "| WF3 | 2026-01-01～2026-03-31 |",
            "| WF4 | 2026-04-01～2026-06-30 |",
            "",
            "### 3.2 実行件数と監査",
            "",
            "| 監査項目 | 結果 |",
            "|---|---:|",
            "| 四半期比較 | 28/28成功 |",
            "| 約1年OHLC | 7/7成功 |",
            "| 約1年実ティック | 7/7成功 |",
            "| 約1年比較ペア | 7/7生成 |",
            "| 表示入力互換性 | 全件PASS |",
            "| 実ティック品質ゲート | 7/7 PASS |",
            "| HTMLレポート欠損 | 0 |",
            "| 約1年比較のdeal系列一致 | 0/7 |",
            "",
            "### 3.3 実ティックデータ品質",
            "",
            f"同期範囲は`{tick_quality['synchronized_from']}`～`{tick_quality['synchronized_to']}`で、対象期間全体を覆っている。対象{int(tick_quality['total_minute_bars']):,}分足のうち{int(tick_quality['fallback_minute_count']):,}分に実ティックがなく、生成ティック補完率は{100 * float(tick_quality['fallback_ratio']):.6f}%だった。約1年比較専用の許容上限0.01%以内であり、全7ケースを注記付きPASSとした。",
            "",
            "短期スモークの既定監査は引き続き補完率0%を要求する。期間端不足、丸一日欠損、集計不能、0.01%超過は今回の比較でもFAILとなる。",
            "",
            "### 3.4 モデル差の詳細",
            "",
            "| 戦略 | NP差 | PF差 | RF差 | Sharpe差 | DD差 | Trades差 | deal系列 |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for strategy in STRATEGIES:
        pair = pair_by_strategy[strategy]
        lines.append(
            f"| {strategy} | {_money(pair.net_profit_delta)} | "
            f"{pair.profit_factor_delta:+.2f} | {pair.recovery_factor_delta:+.2f} | "
            f"{pair.sharpe_ratio_delta:+.2f} | {pair.equity_drawdown_delta:+.2f} pp | "
            f"{pair.real_ticks_trades - pair.ohlc_trades:+d} | "
            f"{'一致' if pair.deal_sequence_same else '不一致'} |"
        )
    lines.extend(
        [
            "",
            "deal系列は、deal件数に加えて日時・方向・約定価格を順番どおり正規化したSHA-256で比較した。全戦略で不一致だったため、モデリング方式は単なる会計表示ではなく、約定経路へ影響している。",
            "",
            "### 3.5 四半期合計と約1年連続テストの違い",
            "",
            "四半期比較は各WFをDeposit 3,000 USDから独立に開始する。したがって、4期合計は四半期安定性を見る参考値であり、連続運用や複利運用の成績ではない。約1年テストは2025-07-01から2026-06-30まで資金状態を引き継ぐため、ロット調整、保有状態、期間境界の影響によって4期合計と一致しない場合がある。",
            "",
            "### 3.6 戦略別の暫定評価",
            "",
            "| 戦略 | 暫定評価 | 次に確認すべき点 |",
            "|---|---|---|",
            "| xau_sr_scalp_h1 | OHLC利益は最大だが、実ティックで大幅減 | モデル差と価格経路への依存 |",
            "| xau_h1_c5 | 低DDで実ティック利益が増加 | 同一リスク化後も優位か |",
            "| daily_l | 四半期安定性と低DDが良好 | 48取引という標本数 |",
            "| e | 高利益だがモデル差とDD上昇が大きい | 実ティック環境でのリスク |",
            "| goldtradepro_a | 高利益を維持しモデル差は小さめ | DDとWF4悪化 |",
            "| mt5_longterm_e | 159取引で比較的評価しやすい | 同一リスク時の効率 |",
            "| mt5_longterm_j | 利益は小さいがモデル差とDDが小さい | WF2の損益分岐点接近 |",
            "",
            "### 3.7 制約",
            "",
            "- 各setの正確な最適化期間とフォワード期間が残っていないため、厳密なOut-of-Sample成績とは断定できない。",
            "- setごとに`LotPerBalance_step`、`HistoricalMaxDD`、`MaxTrades`などが異なり、利益額とリスク量を分離できていない。",
            "- 使用EAはv7.5デモ版であり、将来利用する製品版との完全同一性は保証できない。",
            "- 実ティック比較は直近約1年に限定される。",
            "- 遅延、ランダム延滞、Commission・Swapストレス、低資金耐性、戦略間相関は未評価である。",
            "",
            "### 3.8 次段階",
            "",
            "1. 資金管理パラメータの小規模感応度テストを行う。",
            "2. 同一リスクの定義と目標DDを決定し、Phase 3を実行する。",
            "3. 固定・ランダム遅延と取引コスト耐性を評価する。",
            "4. 初期資金耐性と日次・週次損益相関を評価する。",
            "5. 上記完了後、最終順位とポートフォリオ候補を含む最終報告書へ更新する。",
            "",
            "### 3.9 正本成果物",
            "",
            f"- 四半期比較: `{quarterly_run.as_posix()}/`",
            f"- 約1年モデル比較: `{model_run.as_posix()}/`",
            f"- 約1年戦略別比較: `{(model_run / 'model_comparison_pairs.csv').as_posix()}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_ubs_current_report(
    quarterly_run: Path,
    model_run: Path,
    output_path: Path,
) -> Path:
    """Build the interim UBS report and accessible SVG charts from saved evidence."""
    if output_path.exists():
        raise UBSReportError(f"Existing report is not overwritten: {output_path}")
    quarterly = _load_quarterly(quarterly_run)
    models, pairs, manifest = _load_models(model_run)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    assets = output_path.parent / "assets" / "ubs_gold_strategy_comparison"
    if assets.exists():
        raise UBSReportError(f"Existing report assets are not overwritten: {assets}")
    assets.mkdir(parents=True)
    charts = {
        "annual-net-profit.svg": _annual_profit_svg(pairs),
        "model-profit-delta.svg": _profit_delta_svg(pairs),
        "quarterly-net-profit.svg": _quarterly_svg(quarterly),
        "real-tick-risk-return.svg": _risk_return_svg(models),
    }
    for name, content in charts.items():
        (assets / name).write_text(content, encoding="utf-8")
    report = _report_markdown(
        quarterly, models, pairs, manifest, quarterly_run, model_run
    )
    output_path.write_text(report + "\n", encoding="utf-8")
    return output_path


def _selection_status_label(status: str) -> str:
    return {
        "within_target": "目標帯内",
        "lot_granularity_limited_below": "ロット刻みにより下側",
        "minimum_lot_limited_above": "最低ロットでも上限超過",
    }.get(status, status)


def _same_risk_report_markdown(
    annual: list[SameRiskAnnualResult],
    quarterly: list[SameRiskQuarterlyResult],
    original_models: list[ModelResult],
    selection: dict[str, object],
    quarterly_manifest: dict[str, object],
    selection_path: Path,
    quarterly_run: Path,
    original_model_run: Path,
) -> str:
    annual_by = {row.strategy_id: row for row in annual}
    quarter_by = {(row.wf, row.strategy_id): row for row in quarterly}
    original_by = {
        row.strategy_id: row.metrics for row in original_models if row.model == 4
    }
    raw_selections = selection["selections"]
    assert isinstance(raw_selections, list)
    selection_by = {
        str(item["strategy_id"]): item
        for item in raw_selections
        if isinstance(item, dict)
    }
    target = selection.get("target_equity_drawdown_percent")
    if not isinstance(target, dict):
        raise UBSReportError("Same-risk target DD definition is missing")
    target_minimum = float(target["minimum"])
    target_value = float(target["target"])
    target_maximum = float(target["maximum"])
    within_count = sum(row.selection_status == "within_target" for row in annual)
    positive_quarters = sum(row.metrics.net_profit > 0 for row in quarterly)
    warnings = [row for row in quarterly if row.dd_warning]

    first_selected = selection_by[STRATEGIES[0]]
    annual_evidence = _load_json(
        Path(str(first_selected["selected_result_file"])),
        "selected annual evidence",
    )
    annual_tick = annual_evidence.get("tick_data_quality")
    if not isinstance(annual_tick, dict):
        raise UBSReportError("Selected annual tick quality is missing")
    quarterly_raw = quarterly_manifest.get("results")
    assert isinstance(quarterly_raw, list)
    wf_fallback: dict[str, int] = {}
    for wf in WFS:
        wf_items = [item for item in quarterly_raw if item.get("wf") == wf]
        values = {
            0
            if item["tick_data_quality"].get("fallback_minute_count") is None
            else int(item["tick_data_quality"]["fallback_minute_count"])
            for item in wf_items
        }
        if len(values) != 1:
            raise UBSReportError(f"Inconsistent quarterly tick quality: {wf}")
        wf_fallback[wf] = values.pop()

    lines = [
        "# Ultimate Breakout System Gold 7戦略 — 同一リスク比較・四半期安全確認 総合報告書",
        "",
        "- 実行状態: **SUCCESS**",
        "- 対象: Ultimate Breakout System v7.5 demo / XAUUSD用7戦略",
        "- 評価段階: Phase 3（固定ロットによる年間DD調整と四半期安全確認）",
        "- 主要実測: 年間ロット候補11件＋四半期28件＝39件",
        "- 期間: 2025-07-01～2026-06-30（WF1～WF4）",
        "- Deposit / Currency / Leverage: 3,000 USD / USD / 1:500",
        "- モデル / Execution: Every tick based on real ticks / No Delay",
        f"- 実行完了: {quarterly_manifest.get('finished_at_jst', '')}",
        "",
        "## 1. 目的",
        "",
        "本報告書の目的は、Ultimate Breakout System（UBS）のXAUUSD用7戦略を、原setの異なる資金管理設定のまま比較する段階から一歩進め、固定ロットによって約1年の最大Equity Drawdown（DD）を概ね5%へ近づけた条件で、収益性と四半期安定性を比較することである。",
        "",
        f"目標はDD {target_value:.1f}%、許容帯は{target_minimum:.1f}～{target_maximum:.1f}%とした。選択したロットを変えずにWF1～WF4を実ティックで独立テストし、各四半期のDD 7.5%超を安全上の警告として検出した。",
        "",
        "ここでいう5%はバックテスト結果を揃えるための比較基準であり、EAの強制決済条件やライブ運用時の損失停止トリガーではない。",
        "",
        "## 2. 結論",
        "",
        "### 2.1 総合判断",
        "",
        f"- 年間DDを目標帯へ収められたのは{within_count}/7戦略だった。`xau_sr_scalp_h1`、`xau_h1_c5`、`daily_l`、`e`である。",
        "- `mt5_longterm_e`と`mt5_longterm_j`は0.01 lot刻みの制約により、上側候補が5.5%を超えるため安全側の低いロットを選んだ。",
        "- `goldtradepro_a`は最低ロット0.01でも年間DD 10.86%だった。この戦略だけは他6戦略とリスクを揃えられておらず、利益額を横並びで順位付けできない。",
        "- 目標帯内の4戦略では、`xau_h1_c5`が年間Net Profit +1,351.68 USD、PF 2.77で最も高い。`daily_l`は+675.19 USD、DD 4.75%で次に高い。",
        f"- 四半期は28/28件が正常完了し、{positive_quarters}/28件が黒字だった。唯一の赤字は`WF4_mt5_longterm_e`の-0.02 USDで、実質的に損益分岐だった。",
        f"- 四半期DD 7.5%超は{len(warnings)}件だけで、`WF4_goldtradepro_a`の13.28%だった。年間の最低ロット制約が四半期でもリスクとして表面化した。",
        "- 現段階では、同一DD帯で収益性が最も高い候補は`xau_h1_c5`、収益と安定性のバランス候補は`daily_l`である。ただし最適化期間の正確な記録がないため、最終順位ではなく次段階へ進める候補評価とする。",
        "",
        "### 2.2 約1年DDの調整結果",
        "",
        "![約1年Equity DDの調整結果](assets/ubs_same_risk_comparison/annual-equity-dd.svg)",
        "",
        "| 戦略 | 固定lot | Net Profit | Trades | PF | RF | Sharpe | Equity DD | 選択結果 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for strategy in STRATEGIES:
        row = annual_by[strategy]
        lines.append(
            f"| {strategy} | {row.selected_lot:.2f} | {_money(row.metrics.net_profit)} | "
            f"{row.metrics.trades} | {row.metrics.profit_factor:.2f} | "
            f"{row.metrics.recovery_factor:.2f} | {row.metrics.sharpe_ratio:.2f} | "
            f"{row.metrics.equity_drawdown_percent:.2f}% | "
            f"{_selection_status_label(row.selection_status)} |"
        )
    lines.extend(
        [
            "",
            "緑の目標帯内へ4戦略を収めた。灰色の2戦略はロット刻みのため意図的に安全側を選択し、赤色の`goldtradepro_a`は最低ロットでも上限を超えた。",
            "",
            "### 2.3 年間リスク・リターン",
            "",
            "![固定ロット後の約1年リスク・リターン](assets/ubs_same_risk_comparison/annual-risk-return.svg)",
            "",
            "目標帯内では`xau_h1_c5`が明確に高いNet Profitを示した。`mt5_longterm_e`と`mt5_longterm_j`はDDが目標より低いため、利益額が小さいことだけを理由に劣後とは判断しない。`goldtradepro_a`は目標帯外なので、この図でも別枠として読む必要がある。",
            "",
            "### 2.4 四半期収益",
            "",
            "![固定ロットの四半期別Net Profit](assets/ubs_same_risk_comparison/quarterly-net-profit.svg)",
            "",
            "| 戦略 | WF1 | WF2 | WF3 | WF4 | 4期単純合計 | 黒字期 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for strategy in STRATEGIES:
        values = [quarter_by[(wf, strategy)].metrics.net_profit for wf in WFS]
        lines.append(
            f"| {strategy} | "
            + " | ".join(_money(value) for value in values)
            + f" | {_money(sum(values))} | {sum(value > 0 for value in values)}/4 |"
        )
    lines.extend(
        [
            "",
            "`xau_h1_c5`はWF2・WF3で大きく伸び、4期単純合計でも+1,299.75 USDだった。`xau_sr_scalp_h1`と`xau_h1_c5`はWF1のPFがともに1.03、`mt5_longterm_j`はWF2のPFが1.01であり、黒字でも損益分岐点に近い期間がある。",
            "",
            "### 2.5 四半期DD",
            "",
            "![固定ロットの四半期別Equity DD](assets/ubs_same_risk_comparison/quarterly-equity-dd.svg)",
            "",
            "| 戦略 | WF1 | WF2 | WF3 | WF4 | 四半期最大DD | 発生期 | 警告 |",
            "|---|---:|---:|---:|---:|---:|---|---:|",
        ]
    )
    for strategy in STRATEGIES:
        values = [quarter_by[(wf, strategy)] for wf in WFS]
        maximum = max(values, key=lambda item: item.metrics.equity_drawdown_percent)
        lines.append(
            f"| {strategy} | "
            + " | ".join(
                f"{value.metrics.equity_drawdown_percent:.2f}%" for value in values
            )
            + f" | {maximum.metrics.equity_drawdown_percent:.2f}% | "
            f"{maximum.wf} | {sum(value.dd_warning for value in values)} |"
        )
    lines.extend(
        [
            "",
            "`goldtradepro_a`はWF1～WF3ではDD 1.88～5.54%だったが、WF4で13.28%へ急増した。年間DD 10.86%だけでなく、期間別にも損失の偏りが確認されたため、現ロットのまま他戦略と同じリスク枠へ含めるべきではない。",
            "",
            "## 3. 詳細",
            "",
            "### 3.1 対象期間と固定条件",
            "",
            "| 項目 | 条件 |",
            "|---|---|",
            "| EA | Ultimate Breakout System v7.5 demo |",
            "| Symbol / Tester時間足 | XAUUSD / H1 |",
            "| Deposit / Currency / Leverage | 3,000 USD / USD / 1:500 |",
            "| モデル | Every tick based on real ticks |",
            "| Execution | No Delay |",
            "| 年間期間 | 2025-07-01～2026-06-30 |",
            "| WF1 | 2025-07-01～2025-09-30 |",
            "| WF2 | 2025-10-01～2025-12-31 |",
            "| WF3 | 2026-01-01～2026-03-31 |",
            "| WF4 | 2026-04-01～2026-06-30 |",
            "| 年間DD目標 | 5.0%（許容帯4.5～5.5%） |",
            "| 四半期警戒線 | Equity DD 7.5%超 |",
            "",
            "### 3.2 同一リスク化の方法",
            "",
            "資金管理方式のパイロットテストにより、UBS v7.5では`Risk=0`を固定ロット方式として使用できることを確認した。派生setでは次の値だけを資金管理用に設定し、売買ロジック、取引上限、エントリー条件、決済条件は変更していない。",
            "",
            "```text",
            "Risk = 0",
            "AdjustLotsizeToVariableValues = false",
            "StartLots = 選択lot",
            "MaxLots = 選択lot",
            "```",
            "",
            "年間結果と四半期結果の全entry dealについて、最小volumeと最大volumeが選択lotに一致することを監査した。年間候補では元結果とdeal系列も一致しており、ロット量を変えても売買日時・方向・約定価格の経路を維持したことを確認している。",
            "",
            "### 3.3 ロット候補と選択理由",
            "",
            "| 戦略 | 測定候補（lot → 年間DD） | 採用lot | 判断 |",
            "|---|---|---:|---|",
        ]
    )
    for strategy in STRATEGIES:
        item = selection_by[strategy]
        candidates = item.get("tested_candidates")
        if not isinstance(candidates, list):
            raise UBSReportError(f"Tested candidates are missing: {strategy}")
        candidate_text = ", ".join(
            f"{float(candidate['lot']):.2f} → {float(candidate['equity_drawdown_percent']):.2f}%"
            for candidate in candidates
            if isinstance(candidate, dict)
        )
        row = annual_by[strategy]
        lines.append(
            f"| {strategy} | {candidate_text} | {row.selected_lot:.2f} | "
            f"{_selection_status_label(row.selection_status)} |"
        )
    lines.extend(
        [
            "",
            "候補選択は5.0%への絶対距離だけでなく、許容上限5.5%を優先した。例えば`mt5_longterm_j`は0.03 lotのDD 5.64%の方が目標へ近いが、上限を超えるため0.02 lot（3.89%）を採用した。",
            "",
            "### 3.4 原設定から固定ロットへの変化",
            "",
            "| 戦略 | 原設定 NP | 原設定 DD | 固定lot NP | 固定lot DD | NP差 | DD差 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for strategy in STRATEGIES:
        original = original_by[strategy]
        selected = annual_by[strategy].metrics
        lines.append(
            f"| {strategy} | {_money(original.net_profit)} | "
            f"{original.equity_drawdown_percent:.2f}% | {_money(selected.net_profit)} | "
            f"{selected.equity_drawdown_percent:.2f}% | "
            f"{_money(selected.net_profit - original.net_profit)} | "
            f"{selected.equity_drawdown_percent - original.equity_drawdown_percent:+.2f} pp |"
        )
    lines.extend(
        [
            "",
            "この差は戦略ロジックの改善ではなく、資金管理方式と取引量を固定したことによるエクスポージャー変更である。したがって、NP増加を最適化効果とは解釈しない。",
            "",
            "### 3.5 四半期合計と年間連続テスト",
            "",
            "| 戦略 | 年間連続NP | 四半期単純合計 | 差（四半期合計 − 年間） |",
            "|---|---:|---:|---:|",
        ]
    )
    for strategy in STRATEGIES:
        annual_profit = annual_by[strategy].metrics.net_profit
        quarter_sum = sum(
            quarter_by[(wf, strategy)].metrics.net_profit for wf in WFS
        )
        lines.append(
            f"| {strategy} | {_money(annual_profit)} | {_money(quarter_sum)} | "
            f"{_money(quarter_sum - annual_profit)} |"
        )
    lines.extend(
        [
            "",
            "各WFは毎回Deposit 3,000 USDから独立して開始する。一方、年間テストは資金状態と保有状態を期間全体で引き継ぐ。このため四半期単純合計は年間連続テストと一致せず、複利運用結果やポートフォリオ収益を表すものでもない。",
            "",
            "### 3.6 完全性・品質監査",
            "",
            "| 監査項目 | 結果 |",
            "|---|---:|",
            "| 年間ロット候補 | 11/11成功 |",
            "| 最終選択 | 7/7、選択元result・set SHA-256付き |",
            "| 四半期実ティック | 28/28成功 |",
            "| 表示入力互換性 | 年間選択7件＋四半期28件すべてPASS |",
            "| entry固定lot | 年間選択7件＋四半期28件すべて一致 |",
            "| 実ティック品質 | 年間選択7件＋四半期28件すべてPASS |",
            f"| 四半期DD警告 | {len(warnings)}件 |",
            "",
            f"年間では{int(annual_tick['total_minute_bars']):,}分足のうち{int(annual_tick['fallback_minute_count']):,}分に実ティックがなく、生成ティック補完率は{100 * float(annual_tick['fallback_ratio']):.6f}%だった。年間上限0.01%以内のため注記付きPASSとした。",
            "",
            f"四半期の補完分数はWF1={wf_fallback['WF1']}分、WF2={wf_fallback['WF2']}分、WF3={wf_fallback['WF3']}分、WF4={wf_fallback['WF4']}分だった。WF1の24分は年間と同じ既知区間で、四半期上限0.03%かつ絶対数24分以下を満たした。7戦略で同じ市場データを使うため、これは戦略ごとに別の欠損が累積したものではない。",
            "",
            "### 3.7 戦略別の判断",
            "",
            "| 戦略 | 現段階の判断 | 主な注意点 |",
            "|---|---|---|",
            "| xau_sr_scalp_h1 | 目標DD帯内、4期すべて黒字 | WF1 PF 1.03、モデル感応度は前段階で大きかった |",
            "| xau_h1_c5 | 目標帯内で年間・四半期合計とも収益首位 | WF1 PF 1.03、WF2 DD 5.30% |",
            "| daily_l | 目標帯内で4期黒字、比較的均衡 | 年間48 tradesで標本数が少ない |",
            "| e | 目標帯内で4期黒字 | 年間利益は目標帯内4戦略で最小 |",
            "| goldtradepro_a | 高利益だが同一リスク比較から除外相当 | 最低lotでも年間DD 10.86%、WF4 DD 13.28% |",
            "| mt5_longterm_e | 安全側lot、年間159 trades | WF4 -0.02 USD、ロット刻みでDD目標未達 |",
            "| mt5_longterm_j | 安全側lot、4期黒字 | WF2 PF 1.01、ロット刻みでDD目標未達 |",
            "",
            "### 3.8 制約",
            "",
            "- 各setの正確な最適化期間とフォワード期間が残っていないため、厳密なOut-of-Sample比較とは断定できない。",
            "- 過去約1年の最大DDを5%へ近づけても、将来のDDが5%以内になる保証はない。",
            "- 0.01 lot刻みでは全戦略のDDを完全一致させられない。4戦略だけが許容帯内、2戦略は下側、1戦略は上側である。",
            "- DD 7.5%は警告基準であり、テスター内の決済トリガーではない。",
            "- No Delayの結果であり、遅延・追加コスト・スリッページ耐性は本報告書の対象外である。",
            "- 戦略を同時稼働した場合の証拠金競合、損益相関、ポートフォリオDDは未評価である。",
            "",
            "### 3.9 次段階",
            "",
            "1. `goldtradepro_a`を別リスク枠に分離するか、より小さい取引単位を利用できる環境を検討する。",
            "2. `xau_h1_c5`と`daily_l`を中心に、固定・ランダム遅延と取引コスト耐性を確認する。",
            "3. 初期資金を下げたCapital Stressと、複数戦略同時運用時の証拠金・相関・合成DDを評価する。",
            "",
            "### 3.10 正本成果物",
            "",
            f"- 最終ロット選択: `{selection_path.as_posix()}`",
            f"- 四半期実ティック結果: `{quarterly_run.as_posix()}/`",
            f"- 四半期集計: `{(quarterly_run / 'quarterly_same_risk_summary.csv').as_posix()}`",
            f"- 原設定の約1年比較: `{original_model_run.as_posix()}/`",
        ]
    )
    return "\n".join(lines)


def build_ubs_same_risk_report(
    selection_path: Path,
    quarterly_run: Path,
    original_model_run: Path,
    output_path: Path,
) -> Path:
    """Build the UBS fixed-lot same-risk report from saved evidence."""
    if output_path.exists():
        raise UBSReportError(f"Existing report is not overwritten: {output_path}")
    annual, selection = _load_same_risk_selection(selection_path)
    quarterly, quarterly_manifest = _load_same_risk_quarterly(
        quarterly_run, annual
    )
    original_models, _, _ = _load_models(original_model_run)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    assets = output_path.parent / "assets" / "ubs_same_risk_comparison"
    if assets.exists():
        raise UBSReportError(f"Existing report assets are not overwritten: {assets}")
    assets.mkdir(parents=True)
    charts = {
        "annual-equity-dd.svg": _same_risk_annual_dd_svg(annual),
        "annual-risk-return.svg": _same_risk_annual_risk_return_svg(annual),
        "quarterly-net-profit.svg": _same_risk_quarterly_profit_svg(quarterly),
        "quarterly-equity-dd.svg": _same_risk_quarterly_dd_svg(quarterly),
    }
    for name, content in charts.items():
        (assets / name).write_text(content, encoding="utf-8")
    report = _same_risk_report_markdown(
        annual,
        quarterly,
        original_models,
        selection,
        quarterly_manifest,
        selection_path,
        quarterly_run,
        original_model_run,
    )
    output_path.write_text(report + "\n", encoding="utf-8")
    return output_path
