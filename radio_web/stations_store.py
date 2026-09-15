"""Managed station-preset store: load, validate, serialize and persist.

The six radio presets ship as ``lib/mpd_service/stations.conf`` (the built-in
default). The web UI never edits that file; it writes a managed copy at
``<managed_dir>/stations.ini`` which :class:`MPDService` already prefers over the
shipped file (Phase 1 wired ``read_config_preferred`` — see
``lib/mpd_service/mpd_service.py``). Editing presets therefore means: validate,
write ``stations.ini`` atomically with one ``.bak`` backup,
then restart the player so it re-reads the file (``restart_radio`` action).

This module is **standard-library only** to keep ``radio_web`` dependency-free.
Its INI reader/writer mirror the coercion-free subset
of ``lib.utilities.UtilityLibrary._parse_config`` exactly so what we write here
round-trips through the player's own parser (asserted in the tests).
"""

import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import config_store, validators

# There are six physical preset buttons, so the managed list is always six slots.
# Extra stations are out of scope for v1.
PRESET_COUNT = 6

STATIONS_FILENAME = "stations.ini"
STATIONS_BACKUP_FILENAME = "stations.ini.bak"

# The shipped built-in presets, resolved relative to the application tree.
# radio_web lives beside lib/ under /opt/raspberry-kitchen-radio.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN_STATIONS_PATH = os.path.join(_REPO_ROOT, "lib", "mpd_service", "stations.conf")

_SECTION_RE = re.compile(r"^\[(.+)\]$")


def managed_stations_path() -> str:
    """Return the absolute path of the managed ``stations.ini``."""
    return os.path.join(config_store.managed_dir(), STATIONS_FILENAME)


def managed_backup_path() -> str:
    """Return the absolute path of the managed ``stations.ini.bak``."""
    return os.path.join(config_store.managed_dir(), STATIONS_BACKUP_FILENAME)


def _parse_ini(text: str) -> List[Tuple[str, Dict[str, str]]]:
    """Parse INI text into an ordered list of ``(section, {key: value})``.

    Preserves section order (the player maps section order to preset order).
    Values are kept as plain strings — no ``true``/``int`` coercion — which is
    all the station fields (name/url/logo) need.
    """
    sections: List[Tuple[str, Dict[str, str]]] = []
    current: Optional[Dict[str, str]] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match is not None:
            current = {}
            sections.append((match.group(1), current))
            continue
        if "=" in line and current is not None:
            key, value = (part.strip() for part in line.split("=", 1))
            current[key] = value
    return sections


def _slots_from_sections(
    sections: List[Tuple[str, Dict[str, str]]],
) -> List[Dict[str, str]]:
    """Normalize parsed sections into exactly :data:`PRESET_COUNT` slot dicts.

    Each slot is ``{"name", "url", "logo"}``. The display name prefers the
    ``name`` key and falls back to the section header. The list is padded with
    empty slots or truncated so it is always :data:`PRESET_COUNT` long.
    """
    slots: List[Dict[str, str]] = []
    for header, values in sections[:PRESET_COUNT]:
        slots.append(
            {
                "name": values.get("name") or header,
                "url": values.get("url", ""),
                "logo": values.get("logo", ""),
            }
        )
    while len(slots) < PRESET_COUNT:
        slots.append({"name": "", "url": "", "logo": ""})
    return slots


def load_stations() -> List[Dict[str, str]]:
    """Return the six preset slots, preferring the managed file.

    Mirrors the player's ``read_config_preferred`` precedence: the managed
    ``stations.ini`` fully replaces the shipped ``stations.conf`` when present,
    otherwise the built-in defaults are used.
    """
    managed = config_store.read_text(managed_stations_path())
    text = managed if managed is not None else _read_builtin()
    return _slots_from_sections(_parse_ini(text))


def _read_builtin() -> str:
    with open(BUILTIN_STATIONS_PATH, encoding="utf-8") as handle:
        return handle.read()


def load_builtin_stations() -> List[Dict[str, str]]:
    """Return the six built-in preset slots (ignoring any managed file)."""
    return _slots_from_sections(_parse_ini(_read_builtin()))


def serialize_stations(slots: List[Dict[str, str]]) -> str:
    """Render ``slots`` as INI text compatible with the player's parser.

    The section header is the display name (matching ``stations.conf``), with
    ``url``, ``logo`` and ``name`` keys underneath. Empty slots are skipped so
    the player never sees a preset with a blank URL.
    """
    blocks: List[str] = []
    for slot in slots:
        name = (slot.get("name") or "").strip()
        url = (slot.get("url") or "").strip()
        if not name or not url:
            continue
        logo = (slot.get("logo") or "").strip()
        blocks.append(f"[{name}]\n" f"url = {url}\n" f"logo = {logo}\n" f"name = {name}\n")
    return "\n".join(blocks) + ("\n" if blocks else "")


def validate_slots(slots: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Validate and clean all populated slots, or raise :class:`ValueError`.

    A slot is "populated" if it has any of name/url/logo set; a fully empty slot
    is allowed (an unused preset) and passes through untouched. Validation
    errors are prefixed with the 1-based preset number for a clear message.
    """
    cleaned: List[Dict[str, str]] = []
    for index, slot in enumerate(slots, start=1):
        name = (slot.get("name") or "").strip()
        url = (slot.get("url") or "").strip()
        logo = (slot.get("logo") or "").strip()
        if not name and not url and not logo:
            cleaned.append({"name": "", "url": "", "logo": ""})
            continue
        try:
            cleaned.append(
                {
                    "name": validators.validate_station_name(name),
                    "url": validators.validate_stream_url(url),
                    "logo": validators.validate_logo_filename(logo),
                }
            )
        except ValueError as exc:
            raise ValueError(f"Preset {index}: {exc}") from exc
    return cleaned


def save_stations(slots: List[Dict[str, str]]) -> None:
    """Validate, back up and atomically persist ``slots`` to ``stations.ini``.

    Follows the managed-file recipe: validate everything first,
    keep one ``.bak`` of the previous managed file, then write atomically at
    mode ``0644``. Raises :class:`ValueError` if any field is invalid (nothing
    is written in that case).
    """
    cleaned = validate_slots(slots)
    payload = serialize_stations(cleaned)
    _backup_existing()
    config_store.atomic_write(managed_stations_path(), payload, config_store.CONFIG_MODE)


def _backup_existing() -> None:
    current = config_store.read_text(managed_stations_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current, config_store.CONFIG_MODE)


def restore_builtin() -> None:
    """Remove the managed override so the shipped presets take effect again.

    Deletes ``stations.ini`` (and its ``.bak``) if present; a subsequent
    :func:`load_stations` — and the player's ``read_config_preferred`` — then
    falls back to the built-in ``stations.conf``. Idempotent.
    """
    for path in (managed_stations_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


# --- Stream test ------------------------------------------------------------

# Bounded, server-side reachability check for a stream URL ("test a stream
# before saving"). Standard library only: a short timeout
# and a tiny capped read — we never buffer the stream.
_STREAM_TIMEOUT_SECONDS = 6.0
_STREAM_PROBE_BYTES = 1024


def test_stream(url: str) -> Tuple[bool, str]:
    """Probe ``url`` with a bounded request; return ``(ok, human_message)``.

    Validates the URL first (same whitelist as saving), then issues a single
    GET with a short timeout and reads at most a kilobyte. Any success status
    with readable bytes counts as reachable. Never raises; failures are returned
    as a descriptive message.
    """
    try:
        clean = validators.validate_stream_url(url)
    except ValueError as exc:
        return False, str(exc)
    request = Request(clean, method="GET", headers={"User-Agent": "radio-web"})
    try:
        with urlopen(request, timeout=_STREAM_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200) or 200
            chunk = response.read(_STREAM_PROBE_BYTES)
        if 200 <= int(status) < 400 and chunk:
            return True, f"Stream reachable ({int(status)})."
        return False, f"Stream returned status {int(status)} with no data."
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"Stream not reachable: {reason}."
    except (OSError, ValueError) as exc:
        return False, f"Stream test failed: {exc}."
