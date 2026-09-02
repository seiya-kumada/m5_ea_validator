from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from mt5_ea_validator.tick_history import (
    TickHistoryGateError,
    audit_latest_real_tick_test,
    audit_saved_ubs_run,
    required_coverage_end,
)


def _block(*, synchronized_to: str, warning_lines: tuple[str, ...] = ()) -> str:
    lines = [
        "AA\t0\t14:00:00.000\tTester\tXAUUSD,H1 (Demo): testing of "
        "Experts\\Market\\Ultimate Breakout System.ex5 from "
        "2026.08.01 00:00 to 2026.08.31 00:00",
        "BB\t0\t14:00:01.000\tCore 01\tXAUUSD: history ticks synchronized "
        f"from 2025.01.02 to {synchronized_to}",
        *warning_lines,
        "CC\t0\t14:00:10.000\tTester\tautomatic testing finished",
    ]
    return "\n".join(lines) + "\n"


class TickHistoryTests(unittest.TestCase):
    def test_required_end_is_last_weekday_before_to_date(self) -> None:
        self.assertEqual(required_coverage_end("2026.08.31"), "2026.08.28")
        self.assertEqual(required_coverage_end("2025.09.30"), "2025.09.29")

    def test_complete_real_tick_block_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tester.log"
            path.write_text(_block(synchronized_to="2026.08.28"), encoding="utf-8")

            audit = audit_latest_real_tick_test(
                path,
                symbol="XAUUSD",
                period="H1",
                from_date="2026.08.01",
                to_date="2026.08.31",
            )

        self.assertTrue(audit.passed)
        self.assertEqual(audit.synchronized_to, "2026.08.28")
        self.assertEqual(audit.fallback_warning_count, 0)
        self.assertEqual(audit.reasons, ())

    def test_incomplete_history_and_generated_fallback_fail(self) -> None:
        warnings = (
            "DD\t3\t14:00:02.000\tCore 01\tXAUUSD : 2026.08.28 23:59 - "
            "no real ticks within a day",
            "EE\t3\t14:00:02.000\tCore 01\tXAUUSD : 2026.08.01 00:00 - "
            "2026.08.31 00:00 real ticks absent for 2376 minutes of 27519 "
            "total minute bars, every tick generation used",
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tester.log"
            path.write_text(
                _block(synchronized_to="2026.08.27", warning_lines=warnings),
                encoding="utf-8",
            )

            audit = audit_latest_real_tick_test(
                path,
                symbol="XAUUSD",
                period="H1",
                from_date="2026.08.01",
                to_date="2026.08.31",
            )

        self.assertFalse(audit.passed)
        self.assertEqual(audit.fallback_warning_count, 2)
        self.assertIn("tick_coverage_ends_before_required_date", audit.reasons)
        self.assertIn("generated_tick_fallback_detected", audit.reasons)

    def test_sparse_quantified_fallback_can_pass_with_explicit_tolerance(self) -> None:
        warnings = (
            "DD\t3\t14:00:02.000\tCore 01\tXAUUSD : 2025.07.03 23:59 - "
            "real ticks absent for 12 minutes out of 1379 total minute bars "
            "within a day",
            "EE\t3\t14:00:02.000\tCore 01\tXAUUSD : 2025.07.01 00:00 - "
            "2026.06.30 00:00 real ticks absent for 24 minutes of 352554 "
            "total minute bars, every tick generation used",
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tester.log"
            path.write_text(
                _block(synchronized_to="2026.08.28", warning_lines=warnings),
                encoding="utf-8",
            )

            audit = audit_latest_real_tick_test(
                path,
                symbol="XAUUSD",
                period="H1",
                from_date="2026.08.01",
                to_date="2026.08.31",
                max_generated_fallback_ratio=0.0001,
            )

        self.assertTrue(audit.passed)
        self.assertEqual(audit.fallback_minute_count, 24)
        self.assertEqual(audit.total_minute_bars, 352554)
        self.assertAlmostEqual(audit.fallback_ratio or 0.0, 24 / 352554)
        self.assertTrue(audit.sparse_fallback_tolerance_applied)
        self.assertEqual(audit.reasons, ())

    def test_full_day_fallback_is_not_accepted_by_sparse_tolerance(self) -> None:
        warnings = (
            "DD\t3\t14:00:02.000\tCore 01\tXAUUSD : 2026.08.28 23:59 - "
            "no real ticks within a day",
            "EE\t3\t14:00:02.000\tCore 01\tXAUUSD : 2026.08.01 00:00 - "
            "2026.08.31 00:00 real ticks absent for 1 minutes of 27519 "
            "total minute bars, every tick generation used",
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tester.log"
            path.write_text(
                _block(synchronized_to="2026.08.28", warning_lines=warnings),
                encoding="utf-8",
            )

            audit = audit_latest_real_tick_test(
                path,
                symbol="XAUUSD",
                period="H1",
                from_date="2026.08.01",
                to_date="2026.08.31",
                max_generated_fallback_ratio=0.0001,
            )

        self.assertFalse(audit.passed)
        self.assertFalse(audit.sparse_fallback_tolerance_applied)
        self.assertIn("generated_tick_fallback_detected", audit.reasons)

    def test_cumulative_log_uses_latest_matching_completed_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tester.log"
            text = _block(
                synchronized_to="2026.08.27",
                warning_lines=(
                    "DD\t3\t14:00:02.000\tCore 01\tXAUUSD : "
                    "2026.08.28 23:59 - no real ticks within a day",
                ),
            ) + _block(synchronized_to="2026.08.28")
            path.write_text(text, encoding="utf-16")

            audit = audit_latest_real_tick_test(
                path,
                symbol="XAUUSD",
                period="H1",
                from_date="2026.08.01",
                to_date="2026.08.31",
            )

        self.assertTrue(audit.passed)
        self.assertEqual(audit.test_block_start_line, 5)
        self.assertEqual(audit.fallback_warning_count, 0)

    def test_missing_matching_block_returns_failed_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tester.log"
            path.write_text("unrelated\n", encoding="utf-8")

            audit = audit_latest_real_tick_test(
                path,
                symbol="XAUUSD",
                period="H1",
                from_date="2026.08.01",
                to_date="2026.08.31",
            )

        self.assertFalse(audit.passed)
        self.assertEqual(audit.reasons, ("matching_test_block_not_found",))

    def test_saved_run_audit_writes_separate_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            log = run / "tester_20260902.log"
            log.write_text(_block(synchronized_to="2026.08.28"), encoding="utf-8")
            manifest = {
                "run_id": "run-1",
                "conditions": {
                    "model": 4,
                    "symbol": "XAUUSD",
                    "period": "H1",
                    "from_date": "2026.08.01",
                    "to_date": "2026.08.31",
                },
                "results": [
                    {
                        "strategy_id": "strategy_a",
                        "log_snapshots": [str(log)],
                    }
                ],
            }
            manifest_path = run / "run_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            before = manifest_path.read_bytes()

            output = audit_saved_ubs_run(run)
            summary = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["audited_result_count"], 1)
            self.assertEqual(manifest_path.read_bytes(), before)

    def test_saved_run_audit_raises_after_writing_failed_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            log = run / "tester_20260902.log"
            log.write_text(_block(synchronized_to="2026.08.27"), encoding="utf-8")
            (run / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-fail",
                        "conditions": {
                            "model": 4,
                            "symbol": "XAUUSD",
                            "period": "H1",
                            "from_date": "2026.08.01",
                            "to_date": "2026.08.31",
                        },
                        "results": [
                            {
                                "strategy_id": "strategy_a",
                                "log_snapshots": [str(log)],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(TickHistoryGateError) as raised:
                audit_saved_ubs_run(run)

            summary = json.loads(raised.exception.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "fail")


if __name__ == "__main__":
    unittest.main()
