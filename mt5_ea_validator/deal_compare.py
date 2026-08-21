from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal
from itertools import islice
from pathlib import Path

from mt5_ea_validator.report import _TableCellParser, _decode_report


DEAL_LOG_PATTERN = re.compile(
    r"(?P<time>20\d{2}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"deal #[0-9]+ (?P<direction>buy|sell) [0-9.]+ \S+ "
    r"at (?P<price>[0-9.]+) done"
)


def _canonical(time: str, direction: str, price: str) -> str:
    return f"{time}|{direction.casefold()}|{format(Decimal(price), 'f')}"


def manual_deal_sequence(
    path: Path,
    *,
    start_line: int,
    end_line: int,
) -> tuple[str, ...]:
    if start_line <= 0 or end_line < start_line:
        raise ValueError("manual log line range is invalid")
    result: list[str] = []
    with path.open("r", encoding="utf-16") as stream:
        for line in islice(stream, start_line - 1, end_line):
            match = DEAL_LOG_PATTERN.search(line)
            if match:
                result.append(
                    _canonical(
                        match.group("time"),
                        match.group("direction"),
                        match.group("price"),
                    )
                )
    return tuple(result)


def report_deal_sequence(path: Path) -> tuple[str, ...]:
    parser = _TableCellParser()
    parser.feed(_decode_report(path.read_bytes()))
    return tuple(
        _canonical(row[0], row[3], row[6].replace(",", ""))
        for row in parser.rows
        if (
            len(row) >= 13
            and row[4].casefold() in {"in", "out", "in/out"}
            and row[3].casefold() in {"buy", "sell"}
        )
    )


def compare_sequences(
    manual: tuple[str, ...], report: tuple[str, ...]
) -> dict[str, object]:
    mismatches = [
        {"index": index, "manual": manual_value, "report": report_value}
        for index, (manual_value, report_value) in enumerate(zip(manual, report))
        if manual_value != report_value
    ]
    return {
        "manual_count": len(manual),
        "report_count": len(report),
        "identity_multiset_match": sorted(manual) == sorted(report),
        "mismatch_count": len(mismatches) + abs(len(manual) - len(report)),
        "first_mismatches": mismatches[:20],
        "manual_only_tail": list(manual[len(report) : len(report) + 20]),
        "report_only_tail": list(report[len(manual) : len(manual) + 20]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--start-line", type=int, required=True)
    parser.add_argument("--end-line", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    comparison = compare_sequences(
        manual_deal_sequence(
            args.log,
            start_line=args.start_line,
            end_line=args.end_line,
        ),
        report_deal_sequence(args.report),
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0 if comparison["mismatch_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
