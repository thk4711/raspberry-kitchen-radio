"""Managed music-source feature flags: load, validate and persist.

The six user-facing sources ship enabled by default.
The web UI writes a managed override at ``<managed_dir>/sources.ini`` which both
the player (``radio.py``) and the init scripts read. A missing file — or a
missing key — means "enabled", so a fresh image behaves exactly as it does
today. Editing the flags means: validate, write ``sources.ini`` atomically with
one ``.bak`` backup, then restart the player so it
rebuilds the source objects (the ``restart_radio`` action). The init scripts
re-read the file on their own restart / next boot.

Standard-library only, mirroring :mod:`radio_web.stations_store`. The file layout
is a single ``[sources]`` section with ``key = true/false`` values, which the
POSIX-sh reader (``/usr/sbin/radio-source-enabled``) and the player's own
``read_config_layered`` parser both understand.

D-Bus and Avahi are **derived** from the user-facing flags and are never exposed
as switches: :func:`derived_dependencies` computes
them so the Sources page can show them as read-only notes.
"""
import os
import re
from typing import Dict, List, Optional, Tuple

from . import config_store

# The canonical, ordered, user-facing source keys and their display labels.
# ``internet_radio`` maps to MPD. SSH is a device remote-access service, not a
# MusicSource, and is configured through ``device.ini`` instead.
# Order matches the Music sources page.
SOURCE_KEYS: List[Tuple[str, str]] = [
    ("internet_radio", "Internet Radio"),
    ("airplay", "AirPlay"),
    ("spotify", "Spotify"),
    ("bluetooth", "Bluetooth"),
    ("usb_audio", "USB Audio"),
]

# The INI section the flags live under, matching the player-side parser.
SOURCES_SECTION = "sources"

SOURCES_FILENAME = "sources.ini"
SOURCES_BACKUP_FILENAME = "sources.ini.bak"

_SECTION_RE = re.compile(r"^\[(.+)\]$")


def _keys() -> List[str]:
    return [key for key, _label in SOURCE_KEYS]


def default_flags() -> Dict[str, bool]:
    """Return the built-in defaults: every source enabled."""
    return {key: True for key in _keys()}


def managed_sources_path() -> str:
    """Return the absolute path of the managed ``sources.ini``."""
    return os.path.join(config_store.managed_dir(), SOURCES_FILENAME)


def managed_backup_path() -> str:
    """Return the absolute path of the managed ``sources.ini.bak``."""
    return os.path.join(config_store.managed_dir(), SOURCES_BACKUP_FILENAME)


def _coerce_bool(value: str) -> Optional[bool]:
    """Coerce an INI string to bool, or ``None`` when unrecognised."""
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off"):
        return False
    return None


def _parse_sources(text: str) -> Dict[str, bool]:
    """Parse ``sources.ini`` text into a ``{key: bool}`` dict.

    Only recognised keys under the ``[sources]`` section are returned;
    everything else is ignored (the file is treated as advisory, never fatal).
    """
    flags: Dict[str, bool] = {}
    known = set(_keys())
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match is not None:
            section = match.group(1)
            continue
        if section == SOURCES_SECTION and "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if key in known:
                coerced = _coerce_bool(value)
                if coerced is not None:
                    flags[key] = coerced
    return flags


def load_sources() -> Dict[str, bool]:
    """Return the music-source flags, merging the managed file over the defaults.

    Absent file or absent key means "enabled" — a fresh image behaves exactly
    as today. The result always contains every key in :data:`SOURCE_KEYS`.
    """
    flags = default_flags()
    managed = config_store.read_text(managed_sources_path())
    if managed is not None:
        flags.update(_parse_sources(managed))
    return flags


def validate_flags(flags: Dict[str, bool]) -> Dict[str, bool]:
    """Return a cleaned ``{key: bool}`` dict, or raise :class:`ValueError`.

    Rejects any key not in :data:`SOURCE_KEYS` (strict whitelist) and any non-boolean value. Missing keys default
    to enabled so a partial submission never silently disables a source.
    """
    known = set(_keys())
    cleaned = default_flags()
    for key, value in flags.items():
        if key not in known:
            raise ValueError(f"Unknown source '{key}'.")
        if not isinstance(value, bool):
            raise ValueError(f"Source '{key}' must be true or false.")
        cleaned[key] = value
    return cleaned


def serialize_sources(flags: Dict[str, bool]) -> str:
    """Render ``flags`` as ``[sources]`` INI text (canonical key order)."""
    lines = [f"[{SOURCES_SECTION}]"]
    for key in _keys():
        lines.append(f"{key} = {'true' if flags.get(key, True) else 'false'}")
    return "\n".join(lines) + "\n"


def _backup_existing() -> None:
    current = config_store.read_text(managed_sources_path())
    if current is not None:
        config_store.atomic_write(
            managed_backup_path(), current, config_store.CONFIG_MODE
        )


def save_sources(flags: Dict[str, bool]) -> None:
    """Validate, back up and atomically persist ``flags`` to ``sources.ini``.

    Follows the managed-file recipe: validate everything first,
    keep one ``.bak`` of the previous managed file, then write atomically at
    mode ``0644``. Raises :class:`ValueError` if any flag is invalid (nothing is
    written in that case).
    """
    cleaned = validate_flags(flags)
    payload = serialize_sources(cleaned)
    _backup_existing()
    config_store.atomic_write(
        managed_sources_path(), payload, config_store.CONFIG_MODE
    )


def restore_builtin() -> None:
    """Remove the managed override so every source is enabled again.

    Deletes ``sources.ini`` (and its ``.bak``) if present; a subsequent
    :func:`load_sources` — and the init scripts — then fall back to the built-in
    all-enabled defaults. Idempotent.
    """
    for path in (managed_sources_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def derived_dependencies(flags: Dict[str, bool]) -> List[str]:
    """Return human-readable notes for the internal deps the flags imply.

    D-Bus and Avahi are never exposed as switches;
    they are computed from the selected user-facing sources so the UI can show
    why they are running.
    """
    notes: List[str] = []
    if flags.get("bluetooth", True):
        notes.append("Bluetooth uses the system D-Bus (org.bluez).")
    if flags.get("airplay", True):
        notes.append("AirPlay advertises the receiver over Avahi (mDNS).")
    return notes
