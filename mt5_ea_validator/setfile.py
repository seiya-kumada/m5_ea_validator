from __future__ import annotations

from pathlib import Path


class SetFileError(ValueError):
    """Raised when the dedicated set file is missing or unsafe."""


REQUIRED_QQ_WF1_VALUES = {
    "InpLotsFixed": "0.01",
    "InpOrdersMax": "10",
    "InpDDMode": "0",
    "InpDDValue": "0.0",
    "InpUseNfpFridayFilter": "true",
    "InpTradingFridayNight": "false",
}


def read_set_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8")


def parse_set_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        values[name.strip()] = raw_value.split("||", 1)[0].strip()
    return values


def validate_dedicated_set(
    path: Path,
    required_values: dict[str, str],
    *,
    label: str = "QQ/WF",
) -> dict[str, str]:
    if not path.is_file():
        raise SetFileError(f"{label}専用setがありません: {path}")
    values = parse_set_values(read_set_text(path))
    failures = {
        name: (values.get(name), expected)
        for name, expected in required_values.items()
        if values.get(name) != expected
    }
    if failures:
        details = ", ".join(
            f"{name}={actual!r} (expected {expected!r})"
            for name, (actual, expected) in failures.items()
        )
        raise SetFileError(f"{label}専用setの固定値が不正です: {details}")
    return values


def validate_qq_wf1_set(path: Path) -> dict[str, str]:
    return validate_dedicated_set(path, REQUIRED_QQ_WF1_VALUES, label="QQ/WF1")


def validate_qq_wf_set(
    path: Path,
    required_values: dict[str, str],
    *,
    label: str = "QQ/WF",
) -> dict[str, str]:
    """Backward-compatible alias for existing callers."""
    return validate_dedicated_set(path, required_values, label=label)


def write_mt5_unicode(path: Path, text: str, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"既存ファイルは上書きしません: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    path.write_text(normalized, encoding="utf-16", newline="")


def stage_dedicated_set(
    source: Path,
    destination: Path,
    required_values: dict[str, str] | None = None,
    *,
    label: str = "QQ/WF1",
) -> None:
    source_text = read_set_text(source)
    validate_dedicated_set(
        source,
        required_values or REQUIRED_QQ_WF1_VALUES,
        label=label,
    )
    if destination.exists():
        existing_values = parse_set_values(read_set_text(destination))
        source_values = parse_set_values(source_text)
        if existing_values == source_values:
            return
        raise SetFileError(
            f"ステージング先に内容の異なるsetがあります。上書きしません: {destination}"
        )
    write_mt5_unicode(destination, source_text)
