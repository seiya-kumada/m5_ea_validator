from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from mt5_ea_validator.configuration import load_scenario
from mt5_ea_validator.report import (
    DealAudit,
    check_benchmark,
    parse_deal_audit,
    parse_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def report_html(*, net_profit: str = "53.98", trades: str = "147") -> str:
    return f"""<html><body><table>
    <tr><td>Total Net Profit:</td><td>{net_profit}</td></tr>
    <tr><td>Profit Factor:</td><td>1.26</td></tr>
    <tr><td>Recovery Factor:</td><td>0.54</td></tr>
    <tr><td>Sharpe Ratio:</td><td>2.99</td></tr>
    <tr><td>Equity Drawdown Relative:</td><td>3.35% (100.50)</td></tr>
    <tr><td>Total Trades:</td><td>{trades}</td></tr>
    </table></body></html>"""


def matching_deal_audit(scenario) -> DealAudit:
    return DealAudit(
        deal_count=scenario.benchmark.deal_count,
        deal_sequence_sha256=scenario.benchmark.deal_sequence_sha256,
        commission_total=-105.84,
        swap_total=0.0,
        deal_profit_total=159.75,
    )


class ReportTests(unittest.TestCase):
    def test_parses_english_utf16_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.htm"
            path.write_text(report_html(), encoding="utf-16")
            metrics = parse_report(path)

        self.assertEqual(metrics.net_profit, 53.98)
        self.assertEqual(metrics.trades, 147)
        self.assertEqual(metrics.profit_factor, 1.26)
        self.assertEqual(metrics.recovery_factor, 0.54)
        self.assertEqual(metrics.equity_drawdown_percent, 3.35)

    def test_parses_japanese_utf8_report(self) -> None:
        html = """<html><body><table>
        <tr><td>総損益:</td><td>53.98</td></tr>
        <tr><td>プロフィットファクター:</td><td>1.26</td></tr>
        <tr><td>リカバリファクター:</td><td>0.54</td></tr>
        <tr><td>シャープレシオ:</td><td>0.31</td></tr>
        <tr><td>証拠金相対ドローダウン:</td><td>3.35% (100.50)</td></tr>
        <tr><td>総取引数:</td><td>147</td></tr>
        </table></body></html>"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.htm"
            path.write_text(html, encoding="utf-8")
            metrics = parse_report(path)

        self.assertEqual(metrics.net_profit, 53.98)
        self.assertEqual(metrics.trades, 147)

    def test_benchmark_detects_mismatch(self) -> None:
        scenario = load_scenario(
            PROJECT_ROOT / "config" / "qq_wf1_capital.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.htm"
            path.write_text(report_html(net_profit="50.00"), encoding="utf-8")
            check = check_benchmark(
                parse_report(path),
                scenario.benchmark,
                matching_deal_audit(scenario),
            )

        self.assertFalse(check.passed)
        self.assertFalse(check.checks["net_profit"]["passed"])
        self.assertTrue(check.checks["trades"]["passed"])

    def test_benchmark_accepts_observed_net_profit_difference(self) -> None:
        scenario = load_scenario(
            PROJECT_ROOT / "config" / "qq_wf1_capital.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.htm"
            path.write_text(report_html(net_profit="53.91"), encoding="utf-8")
            check = check_benchmark(
                parse_report(path),
                scenario.benchmark,
                matching_deal_audit(scenario),
            )

        self.assertTrue(check.passed)
        self.assertEqual(scenario.benchmark.net_profit_tolerance, 0.10)

    def test_benchmark_skips_metrics_without_manual_reference(self) -> None:
        scenario = load_scenario(
            PROJECT_ROOT / "config" / "qq_wf2_capital.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.htm"
            path.write_text(
                report_html(net_profit="213.27", trades="199"),
                encoding="utf-8",
            )
            check = check_benchmark(
                parse_report(path),
                scenario.benchmark,
                matching_deal_audit(scenario),
            )

        self.assertTrue(check.passed)
        self.assertNotIn("profit_factor", check.checks)
        self.assertNotIn("equity_drawdown_percent", check.checks)

    def test_parses_deal_sequence_and_accounting_totals(self) -> None:
        rows = """
        <tr><td>2025.07.01 02:03:04</td><td>2</td><td>XAUUSD</td>
        <td>sell</td><td>out</td><td>0.01</td><td>3301.20</td><td>2</td>
        <td>-0.36</td><td>-0.05</td><td>1.10</td><td>3000.33</td><td>exit</td></tr>
        <tr><td>2025.07.01 01:02:03</td><td>1</td><td>XAUUSD</td>
        <td>buy</td><td>in</td><td>0.01</td><td>3300.10</td><td>1</td>
        <td>-0.36</td><td>0.00</td><td>0.00</td><td>2999.64</td><td>entry</td></tr>
        """
        canonical = (
            "2025.07.01 01:02:03|buy|3300.10\n"
            "2025.07.01 02:03:04|sell|3301.20\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.htm"
            path.write_text(f"<html><table>{rows}</table></html>", encoding="utf-8")
            audit = parse_deal_audit(path)

        self.assertEqual(audit.deal_count, 2)
        self.assertEqual(
            audit.deal_sequence_sha256, hashlib.sha256(canonical).hexdigest()
        )
        self.assertAlmostEqual(audit.commission_total, -0.72)
        self.assertAlmostEqual(audit.swap_total, -0.05)
        self.assertAlmostEqual(audit.deal_profit_total, 1.10)


if __name__ == "__main__":
    unittest.main()
