import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

from mt5_ea_validator.log_archive import create_archive, restore_archive, run_pilot, safe_path, run_all, discover_logs
from mt5_ea_validator.tick_history import audit_latest_real_tick_test


class LogArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()

    def source(self, name, content):
        path = self.data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_round_trip_prefix_duplicates_empty_and_independent(self):
        contents = [b"abc", b"abcdef", b"abc", b"", b"unrelated"]
        paths = [self.source(f"{i}/tester.log", value) for i, value in enumerate(contents)]
        archive = self.root / "archive"
        manifest = create_archive(self.data, paths, archive)
        self.assertEqual(len(manifest["objects"]), 2)
        restored = self.root / "restored"
        restore_archive(archive, restored)
        for path, content in zip(paths, contents):
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual((restored / path.relative_to(self.data)).read_bytes(), content)

    def test_existing_destinations_are_not_overwritten(self):
        path = self.source("test.log", b"abc")
        archive = self.root / "archive"
        create_archive(self.data, [path], archive)
        original = (archive / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            create_archive(self.data, [path], archive)
        self.assertEqual((archive / "manifest.json").read_bytes(), original)
        restored = self.root / "restored"
        restored.mkdir()
        with self.assertRaises(FileExistsError):
            restore_archive(archive, restored)

    def test_corrupt_object_rejected_before_restore(self):
        archive = self.root / "archive"
        create_archive(self.data, [self.source("t.log", b"abc")], archive)
        blob = next((archive / "objects").glob("*.gz"))
        blob.write_bytes(blob.read_bytes() + b"corrupt")
        destination = self.root / "restored"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            restore_archive(archive, destination)
        self.assertFalse(destination.exists())

    def test_unsafe_paths_rejected(self):
        for path in ("../escape.log", "/escape.log", "C:/escape.log", "a/../../escape.log", "a\\b.log", "."):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(self.root, path)
        archive = self.root / "archive"
        manifest = create_archive(self.data, [self.source("t.log", b"abc")], archive)
        manifest["files"][0]["path"] = "../escape.log"
        (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            restore_archive(archive, self.root / "restored")
        self.assertFalse((self.root / "escape.log").exists())

    def test_invalid_sources_rejected(self):
        outside = self.root / "outside.log"
        outside.write_bytes(b"abc")
        path = self.source("t.log", b"abc")
        for paths in ([], [outside], [path, path]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                create_archive(self.data, paths, self.root / "archive")
        self.assertFalse((self.root / "archive").exists())

    def test_wrong_restored_hash_rejected(self):
        archive = self.root / "archive"
        manifest = create_archive(self.data, [self.source("t.log", b"abc")], archive)
        manifest["files"][0]["sha256"] = "0" * 64
        (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Restored file mismatch"):
            restore_archive(archive, self.root / "restored")

    def test_pilot_compares_saved_and_restored_audits(self):
        log = (
            "XAUUSD,H1 (Demo): testing of EA from 2025.07.01 00:00 to 2025.09.30 00:00\n"
            "XAUUSD: history ticks synchronized from 2025.07.01 to 2025.09.29\n"
            "\tTester\tautomatic testing finished\n"
        ).encode()
        paths = [self.source(f"{i}/log_snapshots/tester.log", log * (i + 1)) for i in range(2)]
        for path in paths:
            saved = audit_latest_real_tick_test(path, symbol="XAUUSD", period="H1", from_date="2025.07.01", to_date="2025.09.30").to_dict()
            (path.parent.parent / "tick_data_quality.json").write_text(json.dumps(saved), encoding="utf-8")
        result = run_pilot(self.data, paths, self.root / "pilot")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["object_count"], 1)
        self.assertNotEqual(result["checks"][0]["audit"]["test_block_start_line"], result["checks"][1]["audit"]["test_block_start_line"])

    def test_all_excludes_archives_and_checks_every_source(self):
        self.source("log_archive_pilot/old.log", b"exclude")
        self.source("log_archives/old/restored.log", b"exclude")
        source = self.source("original/terminal.log", b"abc")
        self.assertEqual(discover_logs(self.data), [source])
        result = run_all(self.data, self.data / "log_archives" / "new")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["source_files"], 1)
        self.assertEqual(result["audit_not_applicable"], 1)
        self.assertTrue(result["checks"][0]["original_unchanged"])

    def test_all_rejects_insufficient_space_and_wrong_output(self):
        self.source("original/terminal.log", b"abc")
        with self.assertRaises(ValueError):
            run_all(self.data, self.data / "original" / "new")
        output = self.data / "log_archives" / "new"
        with patch("mt5_ea_validator.log_archive.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, "Insufficient free space"):
                run_all(self.data, output)
        self.assertFalse(output.exists())

    def test_all_audit_mismatch_is_failure(self):
        source = self.source("case/log_snapshots/tester_20260927.log", b"no matching test")
        saved = audit_latest_real_tick_test(source, symbol="XAUUSD", period="H1", from_date="2025.07.01", to_date="2025.09.30").to_dict()
        saved["passed"] = True
        (source.parent.parent / "tick_data_quality.json").write_text(json.dumps(saved), encoding="utf-8")
        result = run_all(self.data, self.data / "log_archives" / "new")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["audit_checked"], 1)
        self.assertTrue(result["checks"][0]["restored_audit_matches"])
        self.assertFalse(result["checks"][0]["saved_audit_matches"])


if __name__ == "__main__":
    unittest.main()
