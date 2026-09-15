"""Managed ADS1115 calibration and the player's live ADC telemetry snapshot."""

import json
import math
import os
import re
import time
from typing import Any, Dict

from . import config_store

ADC_FILENAME = "adc.ini"
ADC_BACKUP_FILENAME = "adc.ini.bak"
ADC_STATUS_FILE = os.environ.get("RADIO_ADC_STATUS_FILE") or "/tmp/radio-adc.json"
ADC_STATUS_STALE_SECONDS = 3.0

DEFAULTS: Dict[str, str] = {
    "volume_min_input": "0.93",
    "volume_max_input": "3282",
    "button_min": "100",
    "button_max": "3100",
    "button_tolerance": "150",
    "switch_threshold": "300",
}

_SECTION_RE = re.compile(r"^\[(.+)\]$")
_MAX_ADC_MV = 6144.0


def managed_adc_path() -> str:
    return os.path.join(config_store.managed_dir(), ADC_FILENAME)


def managed_backup_path() -> str:
    return os.path.join(config_store.managed_dir(), ADC_BACKUP_FILENAME)


def _parse_adc(text: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match is not None:
            section = match.group(1)
            continue
        if section != "adc" or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key in DEFAULTS:
            fields[key] = value
    return fields


def load_adc() -> Dict[str, str]:
    settings = dict(DEFAULTS)
    managed = config_store.read_text(managed_adc_path())
    if managed is not None:
        settings.update(_parse_adc(managed))
    return settings


def _number(value: str, label: str) -> float:
    try:
        number = float((value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(number) or not 0 <= number <= _MAX_ADC_MV:
        raise ValueError(f"{label} must be between 0 and {_MAX_ADC_MV:g} mV.")
    return number


def _fmt(number: float) -> str:
    return f"{number:.3f}".rstrip("0").rstrip(".")


def validate_settings(submitted: Dict[str, str]) -> Dict[str, str]:
    volume_min = _number(submitted.get("volume_min_input", ""), "Volume minimum")
    volume_max = _number(submitted.get("volume_max_input", ""), "Volume maximum")
    button_min = _number(submitted.get("button_min", ""), "Button minimum")
    button_max = _number(submitted.get("button_max", ""), "Button maximum")
    tolerance = _number(submitted.get("button_tolerance", ""), "Button tolerance")
    threshold = _number(submitted.get("switch_threshold", ""), "Power threshold")
    if volume_max - volume_min < 10:
        raise ValueError("Volume maximum must be at least 10 mV above the minimum.")
    if button_max - button_min < 60:
        raise ValueError("Button maximum must be at least 60 mV above the minimum.")
    half_step = (button_max - button_min) / 12
    if tolerance >= half_step:
        raise ValueError(
            f"Button tolerance must be below {half_step:.1f} mV so button bands do not overlap."
        )
    return {
        "volume_min_input": _fmt(volume_min),
        "volume_max_input": _fmt(volume_max),
        "button_min": _fmt(button_min),
        "button_max": _fmt(button_max),
        "button_tolerance": _fmt(tolerance),
        "switch_threshold": _fmt(threshold),
    }


def serialize_adc(cleaned: Dict[str, str]) -> str:
    lines = ["[adc]"]
    lines.extend(f"{key} = {cleaned[key]}" for key in DEFAULTS)
    return "\n".join(lines) + "\n"


def save_adc(submitted: Dict[str, str]) -> None:
    cleaned = validate_settings(submitted)
    current = config_store.read_text(managed_adc_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current, config_store.CONFIG_MODE)
    config_store.atomic_write(managed_adc_path(), serialize_adc(cleaned), config_store.CONFIG_MODE)


def restore_builtin() -> None:
    for path in (managed_adc_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def live_snapshot() -> Dict[str, Any]:
    """Return a bounded, defensive view of the volatile player snapshot."""
    try:
        if os.path.getsize(ADC_STATUS_FILE) > 64 * 1024:
            raise ValueError("snapshot is too large")
        with open(ADC_STATUS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("snapshot is not an object")
        updated = float(data.get("updated_at", 0))
        data["available"] = True
        data["stale"] = updated <= 0 or time.time() - updated > ADC_STATUS_STALE_SECONDS
        return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"available": False, "stale": True, "channels": {}}
