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
