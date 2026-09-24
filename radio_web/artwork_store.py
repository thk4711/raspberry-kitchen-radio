"""Managed opt-in setting for Bluetooth online artwork lookup."""

import os
import re
from typing import Dict, Mapping, Optional

from . import config_store

ARTWORK_FILENAME = "artwork.ini"
ARTWORK_BACKUP_FILENAME = "artwork.ini.bak"
ARTWORK_SECTION = "online_artwork"
PROVIDER = "musicbrainz"

DEFAULTS: Dict[str, str] = {
    "enabled": "false",
    "provider": PROVIDER,
}

_SECTION_RE = re.compile(r"^\[(.+)\]$")


def managed_artwork_path() -> str:
    """Return the absolute path of the managed ``artwork.ini``."""
    return os.path.join(config_store.managed_dir(), ARTWORK_FILENAME)


def managed_backup_path() -> str:
    """Return the absolute path of the one-generation backup."""
    return os.path.join(config_store.managed_dir(), ARTWORK_BACKUP_FILENAME)


def _coerce_bool(value: str) -> Optional[bool]:
    lowered = value.strip().lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("", "false", "0", "no", "off"):
        return False
    return None


def _parse_managed(text: str) -> Dict[str, str]:
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
        if section == ARTWORK_SECTION and "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if key in DEFAULTS:
                fields[key] = value
    return fields


def load_artwork() -> Dict[str, str]:
    """Load settings, failing closed for malformed or unsupported providers."""
    settings = dict(DEFAULTS)
    managed = config_store.read_text(managed_artwork_path())
    if managed is None:
        return settings

    parsed = _parse_managed(managed)
    if parsed.get("provider", PROVIDER).strip().lower() != PROVIDER:
        return settings
    enabled = _coerce_bool(parsed.get("enabled", "false"))
    if enabled is not None:
        settings["enabled"] = "true" if enabled else "false"
    return settings


def validate_settings(submitted: Mapping[str, object]) -> Dict[str, str]:
    """Validate the editable opt-in while keeping the provider fixed."""
    unknown = set(submitted) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown artwork setting '{sorted(unknown)[0]}'.")

    provider = str(submitted.get("provider", PROVIDER)).strip().lower()
    if provider != PROVIDER:
        raise ValueError("Unsupported online artwork provider.")

    raw_enabled = submitted.get("enabled", "false")
    enabled: Optional[bool]
    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    else:
        enabled = _coerce_bool(str(raw_enabled))
    if enabled is None:
        raise ValueError("Online artwork must be enabled or disabled.")
    return {
        "enabled": "true" if enabled else "false",
        "provider": PROVIDER,
    }


def serialize_artwork(settings: Mapping[str, str]) -> str:
    """Render canonical ``artwork.ini`` contents."""
    return f"[{ARTWORK_SECTION}]\n" f"enabled = {settings['enabled']}\n" f"provider = {PROVIDER}\n"


def save_artwork(submitted: Mapping[str, object]) -> None:
    """Validate, back up and atomically persist the artwork setting."""
    settings = validate_settings(submitted)
    current = config_store.read_text(managed_artwork_path())
    if current is not None:
        config_store.atomic_write(managed_backup_path(), current, config_store.CONFIG_MODE)
    config_store.atomic_write(
        managed_artwork_path(), serialize_artwork(settings), config_store.CONFIG_MODE
    )


def restore_builtin() -> None:
    """Remove managed files so lookup returns to its disabled default."""
    for path in (managed_artwork_path(), managed_backup_path()):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
