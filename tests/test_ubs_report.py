from __future__ import annotations

import csv
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from mt5_ea_validator.ubs_report import (
    CHART_FILES,
    SAME_RISK_CHART_FILES,
    STRATEGIES,
    WFS,
    UBSReportError,
    build_ubs_current_report,
    build_ubs_same_risk_report,
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

    def _same_risk_evidence(
        self, root: Path
    ) -> tuple[Path, Path, Path]:
        _, original_model = self._evidence(root)
        selection_directory = root / "same-risk-selection"
        quarterly_run = root / "same-risk-quarterly"
        selection_directory.mkdir()
        quarterly_run.mkdir()
        selections = []
        selected_lots = {
            "xau_sr_scalp_h1": 0.03,
            "xau_h1_c5": 0.03,
            "daily_l": 0.04,
            "e": 0.03,
            "goldtradepro_a": 0.01,
            "mt5_longterm_e": 0.01,
            "mt5_longterm_j": 0.02,
        }
        selected_dd = {
            "xau_sr_scalp_h1": 4.90,
            "xau_h1_c5": 5.29,
            "daily_l": 4.75,
            "e": 4.88,
            "goldtradepro_a": 10.86,
            "mt5_longterm_e": 3.84,
            "mt5_longterm_j": 3.89,
        }
        for index, strategy in enumerate(STRATEGIES, start=1):
            lot = selected_lots[strategy]
            status = (
                "minimum_lot_limited_above"
                if strategy == "goldtradepro_a"
                else "lot_granularity_limited_below"
                if strategy in {"mt5_longterm_e", "mt5_longterm_j"}
                else "within_target"
            )
            reason = (
                "minimum_tested_lot_still_exceeds_5.5_percent_limit"
                if status == "minimum_lot_limited_above"
                else "upper_adjacent_lot_exceeds_5.5_percent_limit"
                if status == "lot_granularity_limited_below"
                else "selected_from_4.5_to_5.5_percent_target_band"
            )
            metrics = {
                "net_profit": 100.0 * index,
                "trades": 20 + index,
                "profit_factor": 1.5,
                "recovery_factor": 2.0,
                "sharpe_ratio": 3.0,
                "equity_drawdown_percent": selected_dd[strategy],
            }
            selected_result = selection_directory / f"{strategy}_result.json"
            selected_result.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "strategy_id": strategy,
                        "metrics": metrics,
                        "compatibility": {"passed": True},
                        "tick_data_quality": {
                            "passed": True,
                            "total_minute_bars": 352554,
                            "fallback_minute_count": 24,
                            "fallback_ratio": 0.00006807,
                        },
                        "entry_volume_audit": {
                            "minimum_entry_volume": lot,
                            "maximum_entry_volume": lot,
                        },
                    }
                ),
                encoding="utf-8",
            )
            candidates = [
                {
                    "lot": lot,
                    "equity_drawdown_percent": selected_dd[strategy],
                    "net_profit": metrics["net_profit"],
                }
            ]
            selections.append(
                {
                    "strategy_id": strategy,
                    "selected_lot": f"{lot:.2f}",
                    "equity_drawdown_percent": selected_dd[strategy],
                    "net_profit": metrics["net_profit"],
                    "trades": metrics["trades"],
                    "profit_factor": metrics["profit_factor"],
                    "selection_status": status,
                    "selection_reason": reason,
                    "tested_candidates": candidates,
                    "selected_result_file": str(selected_result),
                }
            )
        selection_path = selection_directory / "final_selection.json"
        selection_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "target_equity_drawdown_percent": {
                        "minimum": 4.5,
                        "target": 5.0,
                        "maximum": 5.5,
                    },
                    "strategy_count": 7,
                    "selections": selections,
                }
            ),
            encoding="utf-8",
        )

        quarterly_rows = []
        manifest_results = []
        for wf_index, wf in enumerate(WFS, start=1):
            for strategy_index, strategy in enumerate(STRATEGIES, start=1):
                lot = selected_lots[strategy]
                dd = (
                    13.28
                    if wf == "WF4" and strategy == "goldtradepro_a"
                    else 2.0 + 0.1 * strategy_index
                )
                warning = dd > 7.5
                metrics = _metric_values(10.0 * wf_index * strategy_index, 20)
                metrics["equity_drawdown_percent"] = dd
                row = {
                    "wf": wf,
                    "strategy_id": strategy,
                    "selected_lot": lot,
                    "selection_status": selections[strategy_index - 1][
                        "selection_status"
                    ],
                    **metrics,
                    "dd_warning_above_7_5_percent": warning,
                }
                quarterly_rows.append(row)
                manifest_results.append(
                    {
                        "status": "success",
                        "case_id": f"{wf}_{strategy}",
                        "wf": wf,
                        "strategy_id": strategy,
                        "compatibility": {"passed": True},
                        "tick_data_quality": {
                            "passed": True,
                            "fallback_minute_count": 24 if wf == "WF1" else None,
                        },
                        "entry_volume_audit": {
                            "minimum_entry_volume": lot,
                            "maximum_entry_volume": lot,
                        },
                    }
                )
        _write_csv(
            quarterly_run / "quarterly_same_risk_summary.csv", quarterly_rows
        )
        (quarterly_run / "quarterly_same_risk_summary.json").write_text(
            json.dumps({"case_count": 28, "warning_count": 1}),
            encoding="utf-8",
        )
        (quarterly_run / "run_manifest.json").write_text(
            json.dumps(
                {
                    "status": "success",
                    "finished_at_jst": "2026-09-04T17:42:23+09:00",
                    "results": manifest_results,
                }
            ),
            encoding="utf-8",
        )
        return selection_path, quarterly_run, original_model

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

    def test_builds_same_risk_report_with_accessible_charts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection, quarterly, original_model = self._same_risk_evidence(root)
            output = root / "reports" / "same-risk.md"

            result = build_ubs_same_risk_report(
                selection, quarterly, original_model, output
            )

            self.assertEqual(result, output)
            report = output.read_text(encoding="utf-8")
            purpose = report.index("## 1. 目的")
            conclusion = report.index("## 2. 結論")
            details = report.index("## 3. 詳細")
            self.assertLess(purpose, conclusion)
            self.assertLess(conclusion, details)
            self.assertIn("年間ロット候補11件＋四半期28件＝39件", report)
            self.assertIn("最低ロット0.01でも年間DD 10.86%", report)
            self.assertIn("WF4_mt5_longterm_e", report)
            self.assertIn("7.5%は警告基準", report)
            for strategy in STRATEGIES:
                self.assertIn(strategy, report)
            asset_directory = output.parent / "assets" / "ubs_same_risk_comparison"
            for chart_name in SAME_RISK_CHART_FILES:
                self.assertIn(
                    f"assets/ubs_same_risk_comparison/{chart_name}", report
                )
                svg_path = asset_directory / chart_name
                svg = svg_path.read_text(encoding="utf-8")
                ET.fromstring(svg)
                self.assertIn("<title", svg)
                self.assertIn("<desc", svg)
                self.assertIn('width="1200"', svg)
                self.assertIn("prefers-color-scheme: dark", svg)
                self.assertIn("font-size: 16px", svg)
            annual_dd = (asset_directory / "annual-equity-dd.svg").read_text(
                encoding="utf-8"
            )
            self.assertIn("目標帯 4.5～5.5%", annual_dd)
            quarterly_dd = (
                asset_directory / "quarterly-equity-dd.svg"
            ).read_text(encoding="utf-8")
            self.assertIn("7.5%超を警告", quarterly_dd)
            self.assertIn("13.28%", quarterly_dd)
            for period in (
                "2025-07-01～2025-09-30",
                "2025-10-01～2025-12-31",
                "2026-01-01～2026-03-31",
                "2026-04-01～2026-06-30",
            ):
                self.assertIn(period, quarterly_dd)

    def test_same_risk_report_rejects_quarterly_lot_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection, quarterly, original_model = self._same_risk_evidence(root)
            manifest_path = quarterly / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["results"][0]["entry_volume_audit"][
                "maximum_entry_volume"
            ] = 0.99
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(UBSReportError, "entry lot does not match"):
                build_ubs_same_risk_report(
                    selection,
                    quarterly,
                    original_model,
                    root / "reports" / "same-risk.md",
                )


if __name__ == "__main__":
    unittest.main()
