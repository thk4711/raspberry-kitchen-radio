"""Validated persistent settings for the ten-band parametric equalizer."""

import configparser
import math
import os
import struct
import tempfile
from typing import Dict, List, Mapping, Tuple, TypedDict

from . import config_store

EQUALIZER_FILENAME = "equalizer.ini"
EQUALIZER_BACKUP_FILENAME = "equalizer.ini.bak"
FILTER_TYPES = ("bell", "low_shelf", "high_shelf", "high_pass", "low_pass")
FILTER_TYPE_IDS = {name: index for index, name in enumerate(FILTER_TYPES)}
MAX_BANDS = 10

# Live runtime file the C LADSPA plugin re-reads without a stream restart. It is
# a non-persistent tmpfs cache regenerated from the persistent equalizer.ini; it
# must NOT live on the data partition. Layout mirrors radio_equalizer.c: a uint32
# magic, a uint32 generation counter, then 54 native-endian floats (preamp,
# five fields for each of ten bands, then three loudness controls:
# loudness_enabled, loudness_amount and current_volume). RADIO_EQUALIZER_RT
# overrides the path so host tests never touch /run.
RUNTIME_MAGIC = 0x52454132  # "REA2"; keep in sync with RT_MAGIC in radio_equalizer.c
# Preamp + five fields per band + three loudness controls (enabled, amount, volume).
RUNTIME_LOUDNESS_VALUES = 3
RUNTIME_CONTROL_VALUES = 1 + MAX_BANDS * 5 + RUNTIME_LOUDNESS_VALUES
RUNTIME_STRUCT = struct.Struct("=II" + "f" * RUNTIME_CONTROL_VALUES)
DEFAULT_RUNTIME_PATH = "/run/radio/equalizer.rt"

# Loudness compensation range. 0 disables the boost; 10 applies the full
# Fletcher-Munson "smile". The C plugin scales this with the current volume.
LOUDNESS_MIN_AMOUNT = 0.0
LOUDNESS_MAX_AMOUNT = 10.0

# Worst-case low-shelf boost (dB) loudness can add at low volume, mirroring
# LOUDNESS_LOW_MAX_DB in radio_equalizer.c. Used to reserve preamp headroom so
# the loudness "smile" never clips at its strongest (near silence).
LOUDNESS_LOW_MAX_DB = 10.0

# Preamp headroom range (dB). The automatic preamp is clamped into this range.
PREAMP_MIN_DB = -24.0
PREAMP_MAX_DB = 0.0


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
    loudness_enabled: bool
    loudness_amount: float


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
        "loudness_enabled": False,
        "loudness_amount": 0.0,
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


def computed_preamp_db(settings: "EqualizerSettings") -> float:
    """Return the automatic preamp (dB) that keeps the EQ chain from clipping.

    The preamp is no longer user-editable: it is derived from the largest
    positive boost the chain can apply so the summed level never exceeds 0 dB.
    That worst-case boost is the maximum of:

      * the largest positive gain among the *enabled* bands, and
      * the loudness low-shelf boost at its strongest (near silence), scaled by
        the loudness level: ``LOUDNESS_LOW_MAX_DB * amount / LOUDNESS_MAX_AMOUNT``.

    The result is ``-boost`` clamped into ``[PREAMP_MIN_DB, PREAMP_MAX_DB]`` so a
    flat EQ with no loudness needs no reduction (0 dB) and extreme boosts never
    ask for more than the range allows.
    """
    boost = 0.0
    if settings["enabled"]:
        for band in settings["bands"]:
            if band["enabled"] and band["gain_db"] > boost:
                boost = float(band["gain_db"])
        amount = float(settings["loudness_amount"])
        if amount > LOUDNESS_MIN_AMOUNT:
            loudness_boost = LOUDNESS_LOW_MAX_DB * (amount / LOUDNESS_MAX_AMOUNT)
            if loudness_boost > boost:
                boost = loudness_boost
    return max(PREAMP_MIN_DB, min(PREAMP_MAX_DB, -boost))


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
    loudness_amount = _number(
        submitted.get("loudness_amount", ""),
        "Loudness amount",
        LOUDNESS_MIN_AMOUNT,
        LOUDNESS_MAX_AMOUNT,
    )
    settings: EqualizerSettings = {
        "enabled": _boolean(submitted.get("eq_enabled", ""), "Equalizer"),
        # The preamp is set automatically (see computed_preamp_db); any submitted
        # value is ignored so the field can be a read-only, informational display.
        "preamp_db": 0.0,
        "bands": bands,
        # Loudness has a single control now: the level. 0 turns it off, so the
        # boolean is derived from the amount rather than a separate checkbox.
        "loudness_enabled": loudness_amount > LOUDNESS_MIN_AMOUNT,
        "loudness_amount": loudness_amount,
    }
    settings["preamp_db"] = computed_preamp_db(settings)
    return settings


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
    lines.extend(
        (
            "",
            "[loudness]",
            f"enabled = {'true' if settings['loudness_enabled'] else 'false'}",
            f"amount = {settings['loudness_amount']:g}",
        )
    )
    return "\n".join(lines) + "\n"


def _as_form(settings: EqualizerSettings) -> Dict[str, object]:
    values: Dict[str, object] = {
        "eq_enabled": settings["enabled"],
        "eq_preamp_db": settings["preamp_db"],
        "loudness_enabled": settings["loudness_enabled"],
        "loudness_amount": settings["loudness_amount"],
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
        # Loudness was added after the initial release; a persisted equalizer.ini
        # written by older firmware has no [loudness] section, so fall back to the
        # shipped defaults instead of rejecting the whole file.
        loudness_defaults = defaults()
        values["loudness_enabled"] = parser.get(
            "loudness",
            "enabled",
            fallback="true" if loudness_defaults["loudness_enabled"] else "false",
        )
        values["loudness_amount"] = parser.get(
            "loudness", "amount", fallback=str(loudness_defaults["loudness_amount"])
        )
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


def control_values(settings: EqualizerSettings, current_volume: float = 100.0) -> List[float]:
    """Return the 54 LADSPA control values in port order.

    The order matches the C plugin and ``audio_hardware_apply._equalizer_controls``:
    preamp, then per band ``enabled, type, frequency, gain, Q``, then the three
    loudness controls ``loudness_enabled, loudness_amount, current_volume``. When
    the EQ is globally disabled every band collapses to an identity bell at
    ``0 dB`` so the live path can bypass without a config change.

    Loudness lives in the same stage (Option A): it is only applied while the EQ
    stage is enabled, so a disabled EQ collapses loudness to off as well.
    ``current_volume`` (0..100) lets the plugin taper the boost with the knob.
    """
    if not settings["enabled"]:
        return [0.0] * RUNTIME_CONTROL_VALUES
    values: List[float] = [computed_preamp_db(settings)]
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
    values.extend(
        (
            1.0 if settings["loudness_amount"] > LOUDNESS_MIN_AMOUNT else 0.0,
            float(settings["loudness_amount"]),
            float(max(0.0, min(100.0, current_volume))),
        )
    )
    return values


def _read_generation(path: str) -> int:
    generation, _ = _read_generation_and_volume(path)
    return generation


def _read_generation_and_volume(path: str) -> Tuple[int, float]:
    """Return ``(generation, current_volume)`` from an existing runtime file.

    ``current_volume`` defaults to ``100.0`` (full, i.e. no loudness taper) when
    the file is absent, truncated, or has the wrong magic. Reading it back lets a
    web EQ apply preserve the volume the ADC loop last pushed, so re-applying the
    EQ never resets loudness tracking to full volume.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read(RUNTIME_STRUCT.size)
    except OSError:
        return 0, 100.0
    if len(data) < RUNTIME_STRUCT.size:
        return 0, 100.0
    magic, generation = struct.unpack_from("=II", data)
    if magic != RUNTIME_MAGIC:
        return 0, 100.0
    unpacked = RUNTIME_STRUCT.unpack(data)
    # Layout: magic, generation, then RUNTIME_CONTROL_VALUES floats; the volume
    # is the final control value.
    current_volume = float(unpacked[-1])
    return generation, current_volume


def _write_runtime_payload(control: List[float]) -> None:
    """Atomically write the fixed-size runtime block, bumping the generation.

    The write is a fixed-size temp file in the same directory swapped in with
    ``os.replace`` so the plugin never sees a torn frame. The directory is created
    if missing (it lives on tmpfs, e.g. ``/run/radio``).

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
    payload = RUNTIME_STRUCT.pack(RUNTIME_MAGIC, generation, *control)
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


def write_runtime(settings: EqualizerSettings) -> None:
    """Atomically write the live runtime file, bumping the generation counter.

    The plugin re-reads this file (no stream restart) whenever the generation
    changes. The current-volume slot is carried over from any existing file so a
    web EQ apply preserves the volume the ADC loop last pushed for loudness
    tracking.
    """
    _, current_volume = _read_generation_and_volume(runtime_path())
    _write_runtime_payload(control_values(settings, current_volume))


def write_runtime_volume(current_volume: float) -> None:
    """Update only the loudness current-volume slot and bump the generation.

    Called from the ADC/volume loop (via ``radio.py``) whenever the physical knob
    moves, so the loudness boost tapers with volume without a stream restart. It
    reloads the persistent settings so band/preamp/loudness parameters stay
    authoritative; the write is best effort and cheap (a debounced, fixed-size
    atomic replace, never per audio sample).
    """
    _write_runtime_payload(control_values(load_equalizer(), current_volume))
