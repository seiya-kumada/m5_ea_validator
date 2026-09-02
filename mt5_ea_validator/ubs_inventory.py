from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.setfile import parse_set_values, read_set_text


class UBSInventoryError(ValueError):
    """Raised when a reproducible UBS inventory cannot be created."""


MAJOR_SETTING_NAMES = (
    "ForceSymbol",
    "UseAutoLoader",
    "Run_Strategy",
    "EntryModel",
    "BacktestSpeed",
    "SpreadFilter",
    "MaxSpread",
    "AllowBuyTrades",
    "AllowSellTrades",
    "ATR_Timeframe",
    "VolTimeframe",
    "VolAtrTimeframe",
    "VolMaxTrades",
    "ST1_Timeframe",
    "MaxTrades",
    "ST1_Expiration_hours",
    "AdjustLotsizeToVariableValues",
    "Risk",
    "StartLots",
    "Manual_RiskPerTrade",
    "MaxRiskInDollar_input",
    "LotPerBalance_step",
    "MaxRiskPerStrategy_Value",
    "HistoricalMaxDD",
    "MaxLots",
    "UseEquity",
    "OnlyUp",
    "CheckMargin",
    "AutoGMT",
    "Broker_GMT_OFFSET_Summer",
    "Broker_GMT_OFFSET_Winter",
    "UseMQL5Calendar",
    "EnableNFP_Filter",
    "EnableIR_Filter",
    "EnableCPI_Filter",
)


def _iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _parameter_names(text: str) -> list[str]:
    names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        names.append(line.split("=", 1)[0].strip())
    return names


def _file_record(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "file_name": path.name,
        "size_bytes": stat.st_size,
        "modified_at_utc": _iso_utc(stat.st_mtime),
        "sha256": sha256_file(path),
    }


def build_ubs_inventory(
    set_directory: Path,
    ea_path: Path,
    *,
    ea_version_label: str,
    symbol: str = "XAUUSD",
    generated_at: datetime | None = None,
) -> dict[str, object]:
    if not set_directory.is_dir():
        raise UBSInventoryError(f"UBS setフォルダがありません: {set_directory}")
    if not ea_path.is_file():
        raise UBSInventoryError(f"UBS EAがありません: {ea_path}")
    if not ea_version_label.strip():
        raise UBSInventoryError("UBS EAのバージョンラベルが空です")

    set_records: list[dict[str, object]] = []
    for path in sorted(set_directory.glob("*.set"), key=lambda item: item.name.casefold()):
        text = read_set_text(path)
        values = parse_set_values(text)
        if values.get("ForceSymbol") != symbol:
            continue
        names = _parameter_names(text)
        duplicates = sorted(
            name for name, count in Counter(names).items() if count > 1
        )
        if duplicates:
            raise UBSInventoryError(
                f"重複パラメータがあるため目録化できません: {path.name}: "
                + ", ".join(duplicates)
            )
        record = _file_record(path)
        record.update(
            {
                "parameter_count": len(values),
                "major_settings": {
                    name: values.get(name) for name in MAJOR_SETTING_NAMES
                },
                "all_settings": dict(sorted(values.items())),
                "tester_period": None,
                "tester_period_status": "unresolved",
            }
        )
        set_records.append(record)

    if not set_records:
        raise UBSInventoryError(
            f"ForceSymbol={symbol}のUBS setがありません: {set_directory}"
        )

    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "generated_at_utc": timestamp.isoformat(),
        "symbol_filter": symbol,
        "set_directory": str(set_directory.resolve()),
        "set_count": len(set_records),
        "ea": {
            **_file_record(ea_path),
            "version_label": ea_version_label.strip(),
        },
        "sets": set_records,
        "notes": [
            "Original set files were read only and were not modified.",
            "Tester periods remain unresolved until supported by explicit evidence.",
            "ST1_Timeframe is an internal setting and is not treated as the Tester period.",
        ],
    }


def _run_id(inventory: dict[str, object]) -> str:
    generated = datetime.fromisoformat(str(inventory["generated_at_utc"]))
    stamp = generated.strftime("%Y%m%dT%H%M%S%f")
    digest = hashlib.sha256(
        json.dumps(inventory, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    return f"{stamp}_{digest}"


def write_ubs_inventory(
    inventory: dict[str, object],
    output_root: Path,
) -> Path:
    run_directory = output_root / _run_id(inventory)
    run_directory.mkdir(parents=True, exist_ok=False)

    manifest_path = run_directory / "ubs_gold_set_inventory.json"
    manifest_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    csv_path = run_directory / "ubs_gold_major_settings.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fieldnames = ["file_name", "sha256", "parameter_count", *MAJOR_SETTING_NAMES]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for record in inventory["sets"]:
            assert isinstance(record, dict)
            major = record["major_settings"]
            assert isinstance(major, dict)
            writer.writerow(
                {
                    "file_name": record["file_name"],
                    "sha256": record["sha256"],
                    "parameter_count": record["parameter_count"],
                    **major,
                }
            )
    return run_directory


def create_ubs_inventory(
    set_directory: Path,
    ea_path: Path,
    output_root: Path,
    *,
    ea_version_label: str,
    symbol: str = "XAUUSD",
    now: Callable[[], datetime] | None = None,
) -> Path:
    inventory = build_ubs_inventory(
        set_directory,
        ea_path,
        ea_version_label=ea_version_label,
        symbol=symbol,
        generated_at=(now or (lambda: datetime.now(timezone.utc)))(),
    )
    return write_ubs_inventory(inventory, output_root)
