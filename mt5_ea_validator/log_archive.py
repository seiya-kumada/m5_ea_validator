"""Non-destructive prefix-deduplicated log archive pilot (no deletion support)."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath

from .tick_history import audit_latest_real_tick_test

CHUNK = 1024 * 1024


def digest(path: Path, length: int | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = length
        while remaining is None or remaining > 0:
            block = stream.read(CHUNK if remaining is None else min(CHUNK, remaining))
            if not block:
                if remaining:
                    raise ValueError(f"Short file: {path}")
                break
            h.update(block)
            if remaining is not None:
                remaining -= len(block)
    return h.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Invalid relative path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in (".", "..") for p in relative.split("/")):
        raise ValueError("Unsafe relative path")
    target = root.joinpath(*parts.parts).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError("Path escapes root")
    return target


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def create_archive(data_root: Path, sources: list[Path], output: Path) -> dict:
    root = data_root.resolve()
    records = []
    for index, source in enumerate(sources, 1):
        source = source.resolve(strict=True)
        if not source.is_relative_to(root) or source.suffix.lower() != ".log":
            raise ValueError("Only logs within data root are supported")
        records.append({"path": source.relative_to(root).as_posix(),
                        "length": source.stat().st_size, "sha256": digest(source)})
        if index % 50 == 0:
            print(f"HASH {index}/{len(sources)}", flush=True)
    if not records or len({r["path"] for r in records}) != len(records):
        raise ValueError("Sources must be nonempty and unique")
    output.mkdir(parents=True, exist_ok=False)
    (output / "objects").mkdir()
    objects = {}
    representatives = []
    for entry in sorted(records, key=lambda r: (-r["length"], r["path"])):
        parent = next((r for r in representatives
                       if Path(r["path"]).name == Path(entry["path"]).name
                       and digest(root / r["path"], entry["length"]) == entry["sha256"]), None)
        if parent is None:
            parent = entry
            representatives.append(entry)
            key = entry["sha256"]
            if key not in objects:
                blob = output / "objects" / f"{key}.gz"
                with (root / entry["path"]).open("rb") as src, blob.open("xb") as raw:
                    with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=1, mtime=0) as dst:
                        shutil.copyfileobj(src, dst, CHUNK)
                objects[key] = {"length": entry["length"], "compressed_sha256": digest(blob)}
        entry["object"] = parent["sha256"]
    for entry in records:
        source = root / entry["path"]
        if source.stat().st_size != entry["length"] or digest(source) != entry["sha256"]:
            raise ValueError("Source changed during archive creation")
    manifest = {"schema_version": 1, "format": "gzip-prefix-sha256", "files": records, "objects": objects}
    write_json(output / "manifest.json", manifest)
    return manifest


def restore_archive(archive: Path, destination: Path) -> dict:
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1 or manifest["format"] != "gzip-prefix-sha256":
        raise ValueError("Unsupported archive")
    targets = set()
    for entry in manifest["files"]:
        target = safe_path(destination, entry["path"])
        if target in targets:
            raise ValueError("Duplicate output path")
        targets.add(target)
        if not isinstance(entry["length"], int) or entry["length"] < 0:
            raise ValueError("Invalid length")
        if not re.fullmatch("[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("Invalid hash")
        if entry["object"] not in manifest["objects"]:
            raise ValueError("Missing object")
        if entry["length"] > manifest["objects"][entry["object"]]["length"]:
            raise ValueError("Prefix exceeds object")
    for key, info in manifest["objects"].items():
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("Invalid object key")
        blob = safe_path(archive, f"objects/{key}.gz")
        if digest(blob) != info["compressed_sha256"]:
            raise ValueError("Compressed object hash mismatch")
        h = hashlib.sha256(); size = 0
        with gzip.open(blob, "rb") as stream:
            while block := stream.read(CHUNK):
                h.update(block); size += len(block)
        if size != info["length"] or h.hexdigest() != key:
            raise ValueError("Object content mismatch")
    destination.mkdir(parents=True, exist_ok=False)
    for index, entry in enumerate(manifest["files"], 1):
        target = safe_path(destination, entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(archive / "objects" / f"{entry['object']}.gz", "rb") as src, target.open("xb") as dst:
            remaining = entry["length"]
            while remaining:
                block = src.read(min(CHUNK, remaining))
                if not block:
                    raise ValueError("Short archive object")
                dst.write(block); remaining -= len(block)
        if digest(target) != entry["sha256"] or target.stat().st_size != entry["length"]:
            raise ValueError("Restored file mismatch")
        if index % 50 == 0:
            print(f"RESTORE {index}/{len(manifest['files'])}", flush=True)
    return manifest


EXCLUDED_ROOTS = frozenset({"log_archive_pilot", "log_archives"})


def discover_logs(data_root: Path) -> list[Path]:
    return sorted(p for folder in data_root.iterdir()
                  if folder.name.casefold() not in EXCLUDED_ROOTS
                  for p in ([folder] if folder.is_file() else folder.rglob("*.log"))
                  if p.is_file() and p.suffix.lower() == ".log")


def run_all(data_root: Path, output: Path) -> dict:
    data_root = data_root.resolve()
    output = output.resolve()
    if not output.is_relative_to(data_root / "log_archives") or output == data_root / "log_archives":
        raise ValueError("Full verification output must be a new folder under data/log_archives")
    sources = discover_logs(data_root)
    if not sources:
        raise ValueError("No source logs")
    inventory = [{"path": p.relative_to(data_root).as_posix(), "length": p.stat().st_size,
                  "mtime_ns": p.stat().st_mtime_ns} for p in sources]
    total = sum(r["length"] for r in inventory)
    free = shutil.disk_usage(data_root).free
    required = total * 2 + 1_000_000_000
    if free < required:
        raise ValueError(f"Insufficient free space: {free}, required {required}")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "source_inventory.json", {"excluded_roots": sorted(EXCLUDED_ROOTS),
               "files": inventory, "source_bytes": total, "free_bytes_before": free,
               "required_free_bytes": required})
    print(f"ARCHIVE START files={len(sources)} bytes={total} free={free}", flush=True)
    archive = output / "archive"
    manifest = create_archive(data_root, sources, archive)
    print(f"ARCHIVE COMPLETE objects={len(manifest['objects'])}", flush=True)
    restored = output / "restored"
    restore_archive(archive, restored)
    checks = []
    for index, entry in enumerate(manifest["files"], 1):
        original = data_root / entry["path"]
        copy = restored / entry["path"]
        check = {"path": entry["path"], "sha256": entry["sha256"],
                 "restored_hash_matches": digest(copy) == entry["sha256"] and copy.stat().st_size == entry["length"]}
        saved_path = original.parent.parent / "tick_data_quality.json"
        if original.name.startswith("tester_") and saved_path.is_file():
            try:
                saved = json.loads(saved_path.read_text(encoding="utf-8"))
                kwargs = {k: saved[k] for k in ("symbol", "period", "from_date", "to_date")}
                kwargs["max_generated_fallback_ratio"] = saved["max_fallback_ratio"]
                original_audit = audit_latest_real_tick_test(original, **kwargs).to_dict()
                restored_audit = audit_latest_real_tick_test(copy, **kwargs).to_dict()
                for audit in (saved, original_audit, restored_audit):
                    audit.pop("source_log", None)
                check.update(audit_status="checked", saved_audit_matches=saved == original_audit,
                             restored_audit_matches=original_audit == restored_audit, audit=restored_audit)
            except (ValueError, KeyError, OSError) as exc:
                check.update(audit_status="error", audit_error=str(exc))
        else:
            check.update(audit_status="not_applicable", audit_reason=(
                "not_tester_snapshot" if not original.name.startswith("tester_") else "no_adjacent_tick_data_quality"))
        checks.append(check)
        if index % 25 == 0:
            print(f"VERIFY {index}/{len(sources)}", flush=True)
    # Final pass detects changes to original evidence during verification.
    for check, entry, before in zip(checks, manifest["files"], inventory):
        original = data_root / entry["path"]
        check["original_unchanged"] = (original.stat().st_size == before["length"] == entry["length"]
                                       and original.stat().st_mtime_ns == before["mtime_ns"]
                                       and digest(original) == entry["sha256"])
    source_list_unchanged = discover_logs(data_root) == sources
    audit_checks = [c for c in checks if c["audit_status"] == "checked"]
    passed = source_list_unchanged and all(c["original_unchanged"] and c["restored_hash_matches"]
             and c["audit_status"] != "error" and c.get("saved_audit_matches", True)
             and c.get("restored_audit_matches", True) for c in checks)
    result = {"status": "pass" if passed else "fail", "source_files": len(sources),
              "source_bytes": total, "source_list_unchanged": source_list_unchanged,
              "object_count": len(manifest["objects"]),
              "object_uncompressed_bytes": sum(r["length"] for r in manifest["objects"].values()),
              "archive_bytes_including_manifest": sum(p.stat().st_size for p in archive.rglob("*") if p.is_file()),
              "audit_checked": len(audit_checks), "audit_not_applicable": sum(c["audit_status"] == "not_applicable" for c in checks),
              "audit_errors": sum(c["audit_status"] == "error" for c in checks), "checks": checks}
    write_json(output / "verification_result.json", result)
    return result


def run_pilot(data_root: Path, sources: list[Path], output: Path) -> dict:
    # A new output directory ensures no previous evidence can be overwritten.
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "archive"
    restored = output / "restored"
    manifest = create_archive(data_root, sources, archive)
    restore_archive(archive, restored)
    checks = []
    for entry in manifest["files"]:
        original = data_root / entry["path"]
        saved_path = original.parent.parent / "tick_data_quality.json"
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        kwargs = {k: saved[k] for k in ("symbol", "period", "from_date", "to_date")}
        kwargs["max_generated_fallback_ratio"] = saved["max_fallback_ratio"]
        original_audit = audit_latest_real_tick_test(original, **kwargs).to_dict()
        restored_audit = audit_latest_real_tick_test(restored / entry["path"], **kwargs).to_dict()
        for audit in (saved, original_audit, restored_audit):
            audit.pop("source_log", None)
        checks.append({"path": entry["path"], "sha256": entry["sha256"],
                       "original_unchanged": original.stat().st_size == entry["length"] and digest(original) == entry["sha256"],
                       "restored_hash_matches": digest(restored / entry["path"]) == entry["sha256"],
                       "saved_audit_matches": saved == original_audit,
                       "restored_audit_matches": original_audit == restored_audit,
                       "audit": restored_audit})
    result = {"status": "pass" if all(all(c[k] for k in ("original_unchanged", "restored_hash_matches", "saved_audit_matches", "restored_audit_matches")) for c in checks) else "fail",
              "source_files": len(checks), "source_bytes": sum(r["length"] for r in manifest["files"]),
              "object_count": len(manifest["objects"]),
              "object_uncompressed_bytes": sum(r["length"] for r in manifest["objects"].values()),
              "archive_bytes_including_manifest": sum(p.stat().st_size for p in archive.rglob("*") if p.is_file()),
              "checks": checks}
    write_json(output / "pilot_result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path, action="append")
    group.add_argument("--all-logs", action="store_true")
    args = parser.parse_args()
    result = (run_all(args.data_root, args.output) if args.all_logs
              else run_pilot(args.data_root.resolve(), args.source, args.output))
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
    if result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
