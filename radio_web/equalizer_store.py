"""Validated persistent settings for the ten-band parametric equalizer."""

import configparser
import math
import os
import struct
import tempfile
from typing import Dict, List, Mapping, TypedDict

from . import config_store

EQUALIZER_FILENAME = "equalizer.ini"
EQUALIZER_BACKUP_FILENAME = "equalizer.ini.bak"
FILTER_TYPES = ("bell", "low_shelf", "high_shelf", "high_pass", "low_pass")
FILTER_TYPE_IDS = {name: index for index, name in enumerate(FILTER_TYPES)}
MAX_BANDS = 10

# Live runtime file the C LADSPA plugin re-reads without a stream restart. It is
# a non-persistent tmpfs cache regenerated from the persistent equalizer.ini; it
# must NOT live on the data partition. Layout mirrors radio_equalizer.c: a uint32
# magic, a uint32 generation counter, then 51 native-endian floats (preamp plus
# five fields for each of ten bands). RADIO_EQUALIZER_RT overrides the path so
# host tests never touch /run.
RUNTIME_MAGIC = 0x52454131  # "REA1"; keep in sync with RT_MAGIC in radio_equalizer.c
RUNTIME_CONTROL_VALUES = 1 + MAX_BANDS * 5
RUNTIME_STRUCT = struct.Struct("=II" + "f" * RUNTIME_CONTROL_VALUES)
DEFAULT_RUNTIME_PATH = "/run/radio/equalizer.rt"


class EqualizerBand(TypedDict):
    enabled: bool
    type: str
    frequency: float
    gain_db: float
    q: float


class EqualizerSettings(TypedDict):
    enabled: bool
    preamp_db: float
    bands: List[EqualizerBand]


_DEFAULT_FREQUENCIES = (60, 120, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000)


def defaults() -> EqualizerSettings:
    return {
        "enabled": False,
        "preamp_db": -3.0,
        "bands": [
            {
                "enabled": False,
                "type": "bell",
                "frequency": float(frequency),
                "gain_db": 0.0,
                "q": 1.0,
            }
            for frequency in _DEFAULT_FREQUENCIES
        ],
    }


def managed_equalizer_path() -> str:
    return os.path.join(config_store.managed_dir(), EQUALIZER_FILENAME)


def managed_backup_path() -> str:
    return os.path.join(config_store.managed_dir(), EQUALIZER_BACKUP_FILENAME)


def _boolean(value: object, label: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{label} must be on or off.")


def _number(value: object, label: str, minimum: float, maximum: float) -> float:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return result


def validate_settings(submitted: Mapping[str, object]) -> EqualizerSettings:
    """Validate a flat HTML-form mapping and return canonical settings."""
    bands: List[EqualizerBand] = []
    for index in range(1, MAX_BANDS + 1):
        prefix = f"eq_band_{index}_"
        filter_type = str(submitted.get(prefix + "type", "")).strip()
        if filter_type not in FILTER_TYPES:
            raise ValueError(f"Band {index} has an unknown filter type.")
        bands.append(
            {
                "enabled": _boolean(submitted.get(prefix + "enabled", ""), f"Band {index}"),
                "type": filter_type,
                "frequency": _number(
                    submitted.get(prefix + "frequency", ""), f"Band {index} frequency", 20, 20000
                ),
                "gain_db": _number(
                    submitted.get(prefix + "gain_db", ""), f"Band {index} gain", -15, 15
                ),
                "q": _number(submitted.get(prefix + "q", ""), f"Band {index} Q", 0.1, 10),
            }
        )
    return {
        "enabled": _boolean(submitted.get("eq_enabled", ""), "Equalizer"),
        "preamp_db": _number(submitted.get("eq_preamp_db", ""), "Preamp", -24, 0),
        "bands": bands,
    }


def serialize_equalizer(settings: EqualizerSettings) -> str:
    lines = [
        "[equalizer]",
        f"enabled = {'true' if settings['enabled'] else 'false'}",
        f"preamp_db = {settings['preamp_db']:g}",
    ]
    for index, band in enumerate(settings["bands"], 1):
        lines.extend(
            (
                "",
                f"[band{index}]",
                f"enabled = {'true' if band['enabled'] else 'false'}",
                f"type = {band['type']}",
                f"frequency = {band['frequency']:g}",
                f"gain_db = {band['gain_db']:g}",
                f"q = {band['q']:g}",
            )
        )
    return "\n".join(lines) + "\n"


def _as_form(settings: EqualizerSettings) -> Dict[str, object]:
    values: Dict[str, object] = {
        "eq_enabled": settings["enabled"],
        "eq_preamp_db": settings["preamp_db"],
    }
    for index, band in enumerate(settings["bands"], 1):
        for key, value in band.items():
            values[f"eq_band_{index}_{key}"] = value
    return values


def load_equalizer() -> EqualizerSettings:
    text = config_store.read_text(managed_equalizer_path())
    if text is None:
        return defaults()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
        values: Dict[str, object] = {
            "eq_enabled": parser.get("equalizer", "enabled"),
            "eq_preamp_db": parser.get("equalizer", "preamp_db"),
        }
        for index in range(1, MAX_BANDS + 1):
            section = f"band{index}"
            for key in ("enabled", "type", "frequency", "gain_db", "q"):
                values[f"eq_band_{index}_{key}"] = parser.get(section, key)
        return validate_settings(values)
    except (configparser.Error, KeyError, ValueError):
        return defaults()


def save_equalizer(submitted: Mapping[str, object]) -> EqualizerSettings:
    settings = validate_settings(submitted)
    current = config_store.read_text(managed_equalizer_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current)
    config_store.atomic_write(managed_equalizer_path(), serialize_equalizer(settings))
    return settings


def settings_as_form(settings: EqualizerSettings) -> Dict[str, object]:
    """Expose canonical values for re-rendering a rejected form."""
    return _as_form(settings)


def restore_builtin() -> None:
    for path in (managed_equalizer_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def runtime_path() -> str:
    """Return the live runtime-file path (``RADIO_EQUALIZER_RT`` overrides)."""
    return os.environ.get("RADIO_EQUALIZER_RT", DEFAULT_RUNTIME_PATH)


def control_values(settings: EqualizerSettings) -> List[float]:
    """Return the 51 LADSPA control values in port order (preamp then bands).

    The order matches the C plugin and ``audio_hardware_apply._equalizer_controls``:
    preamp, then per band ``enabled, type, frequency, gain, Q``. When the EQ is
    globally disabled every band collapses to an identity bell at ``0 dB`` so the
    live path can bypass without a config change.
    """
    if not settings["enabled"]:
        return [0.0] * RUNTIME_CONTROL_VALUES
    values: List[float] = [float(settings["preamp_db"])]
    for band in settings["bands"]:
        values.extend(
            (
                1.0 if band["enabled"] else 0.0,
                float(FILTER_TYPE_IDS[band["type"]]),
                float(band["frequency"]),
                float(band["gain_db"]),
                float(band["q"]),
            )
        )
    return values


def _read_generation(path: str) -> int:
    try:
        with open(path, "rb") as handle:
            header = handle.read(RUNTIME_STRUCT.size)
    except OSError:
        return 0
    if len(header) < RUNTIME_STRUCT.size:
        return 0
    magic, generation = struct.unpack_from("=II", header)
    return generation if magic == RUNTIME_MAGIC else 0


def write_runtime(settings: EqualizerSettings) -> None:
    """Atomically write the live runtime file, bumping the generation counter.

    The plugin re-reads this file (no stream restart) whenever the generation
    changes. The write is a fixed-size temp file in the same directory swapped in
    with ``os.replace`` so the plugin never sees a torn frame. The directory is
    created if missing (it lives on tmpfs, e.g. ``/run/radio``).

    The runtime directory is forced to ``0755`` and the file to ``0644``
    regardless of the process umask. This is essential: the LADSPA plugin is
    opened inside every audio consumer, and MPD runs as the unprivileged ``mpd``
    user. With the target's ``0077`` root umask a plain ``makedirs`` would create
    ``/run/radio`` mode ``0700 root`` and MPD could not read the file, so the
    plugin would silently fall back to the stale ``asound.conf`` values and live
    updates would not reach internet radio.
    """
    path = runtime_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    # World-readable/executable so non-root consumers (notably the ``mpd`` user)
    # can traverse the directory and read the file, independent of umask.
    try:
        os.chmod(directory, 0o755)
    except OSError:
        pass
    generation = (_read_generation(path) + 1) & 0xFFFFFFFF
    if generation == 0:  # Skip 0 so a fresh file always looks "seen".
        generation = 1
    payload = RUNTIME_STRUCT.pack(RUNTIME_MAGIC, generation, *control_values(settings))
    fd, tmp = tempfile.mkstemp(prefix=".eqrt-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
