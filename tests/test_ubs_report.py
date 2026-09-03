from __future__ import annotations

import csv
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from mt5_ea_validator.ubs_report import (
    CHART_FILES,
    STRATEGIES,
    WFS,
    UBSReportError,
    build_ubs_current_report,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _metric_values(profit: float, trades: int) -> dict[str, object]:
    return {
        "net_profit": profit,
        "trades": trades,
        "profit_factor": 1.5,
        "recovery_factor": 2.0,
        "sharpe_ratio": 3.0,
        "equity_drawdown_percent": 2.5,
    }


class UBSReportTests(unittest.TestCase):
    def _evidence(self, root: Path) -> tuple[Path, Path]:
        quarterly = root / "quarterly"
        model = root / "model"
        quarterly.mkdir()
        model.mkdir()
        (quarterly / "suite_manifest.json").write_text(
            json.dumps({"status": "success"}), encoding="utf-8"
        )
        quarterly_rows = []
        for wf_index, wf in enumerate(WFS, start=1):
            for strategy_index, strategy in enumerate(STRATEGIES, start=1):
                quarterly_rows.append(
                    {
                        "wf": wf,
                        "strategy_id": strategy,
                        **_metric_values(10.0 * wf_index * strategy_index, 20),
                    }
                )
        _write_csv(quarterly / "quarterly_summary.csv", quarterly_rows)

        model_rows = []
        pair_rows = []
        manifest_results = []
        for model_id, model_label in (
            (1, "1 minute OHLC"),
            (4, "Every tick based on real ticks"),
        ):
            for strategy_index, strategy in enumerate(STRATEGIES, start=1):
                profit = 100.0 * strategy_index + (10.0 if model_id == 4 else 0.0)
                model_rows.append(
                    {
                        "model": model_id,
                        "model_label": model_label,
                        "strategy_id": strategy,
                        **_metric_values(profit, 30),
                    }
                )
                result = {
                    "status": "success",
                    "model": model_id,
                    "strategy_id": strategy,
                }
                if model_id == 4:
                    result["tick_data_quality"] = {
                        "passed": True,
                        "synchronized_from": "2025.07.01",
                        "synchronized_to": "2026.06.30",
                        "total_minute_bars": 352554,
                        "fallback_minute_count": 24,
                        "fallback_ratio": 0.00006807,
                    }
                manifest_results.append(result)
        for strategy_index, strategy in enumerate(STRATEGIES, start=1):
            pair_rows.append(
                {
                    "strategy_id": strategy,
                    "ohlc_net_profit": 100.0 * strategy_index,
                    "real_ticks_net_profit": 100.0 * strategy_index + 10.0,
                    "net_profit_delta_real_minus_ohlc": 10.0,
                    "ohlc_trades": 30,
                    "real_ticks_trades": 30,
                    "profit_factor_delta_real_minus_ohlc": 0.1,
                    "recovery_factor_delta_real_minus_ohlc": 0.2,
                    "sharpe_ratio_delta_real_minus_ohlc": 0.3,
                    "equity_drawdown_percent_delta_real_minus_ohlc": 0.4,
                    "deal_sequence_same": "False",
                }
            )
        _write_csv(model / "model_comparison_summary.csv", model_rows)
        _write_csv(model / "model_comparison_pairs.csv", pair_rows)
        (model / "suite_manifest.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "finished_at_jst": "2026-09-03T14:17:40+09:00",
                    "results": manifest_results,
                }
            ),
            encoding="utf-8",
        )
        return quarterly, model

    def test_builds_report_in_requested_order_with_accessible_charts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quarterly, model = self._evidence(root)
            output = root / "doc" / "reports" / "report.md"

            result = build_ubs_current_report(quarterly, model, output)

            self.assertEqual(result, output)
            report = output.read_text(encoding="utf-8")
            purpose = report.index("## 1. 目的")
            outcome = report.index("## 2. 結果")
            details = report.index("## 3. 詳細")
            self.assertLess(purpose, outcome)
            self.assertLess(outcome, details)
            self.assertIn("42件", report)
            self.assertIn("全28ケースが黒字", report)
            asset_directory = output.parent / "assets" / "ubs_gold_strategy_comparison"
            for chart_name in CHART_FILES:
                self.assertIn(
                    f"assets/ubs_gold_strategy_comparison/{chart_name}", report
                )
                svg_path = asset_directory / chart_name
                svg = svg_path.read_text(encoding="utf-8")
                ET.fromstring(svg)
                self.assertIn("<title", svg)
                self.assertIn("<desc", svg)
                self.assertIn('width="1200"', svg)
                self.assertIn("prefers-color-scheme: dark", svg)
                self.assertIn("font-size: 16px", svg)
            delta_svg = (asset_directory / "model-profit-delta.svg").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "差額 = 実ティックのNet Profit − 1 minute OHLCのNet Profit",
                delta_svg,
            )
            quarterly_svg = (
                asset_directory / "quarterly-net-profit.svg"
            ).read_text(encoding="utf-8")
            for period in (
                "2025-07-01～2025-09-30",
                "2025-10-01～2025-12-31",
                "2026-01-01～2026-03-31",
                "2026-04-01～2026-06-30",
            ):
                self.assertIn(period, quarterly_svg)

    def test_rejects_incomplete_quarterly_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quarterly, model = self._evidence(root)
            with (quarterly / "quarterly_summary.csv").open(
                encoding="utf-8", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            _write_csv(quarterly / "quarterly_summary.csv", rows[:-1])

            with self.assertRaisesRegex(UBSReportError, "28 unique"):
                build_ubs_current_report(
                    quarterly,
                    model,
                    root / "reports" / "report.md",
                )


if __name__ == "__main__":
    unittest.main()
