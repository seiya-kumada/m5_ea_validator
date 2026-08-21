from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mt5_ea_validator.deal_compare import (
    compare_sequences,
    manual_deal_sequence,
    report_deal_sequence,
)


class DealCompareTests(unittest.TestCase):
    def test_compares_manual_log_and_report_sequences(self) -> None:
        log_text = """header
2025.07.01 01:02:03 deal #1 buy 0.01 XAUUSD at 3300.10 done (based on order #1)
2025.07.01 02:03:04 deal #2 sell 0.01 XAUUSD at 3301.20 done (based on order #2)
footer
"""
        report_text = """<html><table>
<tr><td>2025.07.01 01:02:03</td><td>1</td><td>XAUUSD</td><td>buy</td><td>in</td><td>0.01</td><td>3300.10</td><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td><td>x</td></tr>
<tr><td>2025.07.01 02:03:04</td><td>2</td><td>XAUUSD</td><td>sell</td><td>out</td><td>0.01</td><td>3301.20</td><td>2</td><td>0</td><td>0</td><td>1</td><td>1</td><td>x</td></tr>
</table></html>"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "tester.log"
            report = root / "report.htm"
            log.write_text(log_text, encoding="utf-16")
            report.write_text(report_text, encoding="utf-8")
            manual = manual_deal_sequence(log, start_line=2, end_line=3)
            automated = report_deal_sequence(report)

        comparison = compare_sequences(manual, automated)
        self.assertEqual(comparison["manual_count"], 2)
        self.assertEqual(comparison["report_count"], 2)
        self.assertTrue(comparison["identity_multiset_match"])
        self.assertEqual(comparison["mismatch_count"], 0)

    def test_reports_field_mismatch(self) -> None:
        comparison = compare_sequences(("a", "b"), ("a", "c"))
        self.assertFalse(comparison["identity_multiset_match"])
        self.assertEqual(comparison["mismatch_count"], 1)
        self.assertEqual(comparison["first_mismatches"][0]["index"], 1)


if __name__ == "__main__":
    unittest.main()
