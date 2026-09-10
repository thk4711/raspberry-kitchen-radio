"""Secure, read-only access to the artwork for the active music source.

The player snapshot publishes a constrained artwork identifier, never a file
path. This module maps that identifier to one of the known artwork locations and
validates the file size and signature before returning bytes to the browser.
"""

import os
from typing import Dict, Optional, Tuple

from . import logo_store, stations_store, validators

Artwork = Tuple[str, bytes]

LOGO_DIR = logo_store.BUILTIN_LOGO_DIR
RUNTIME_ARTWORK: Dict[str, str] = {
    "spotify": "/tmp/spotify_cover.jpg",
    "airplay-jpg": "/tmp/shairport-image.jpg",
    "airplay-png": "/tmp/shairport-image.png",
}
MAX_ARTWORK_BYTES = 5 * 1024 * 1024


def _station_path(identifier: str) -> Optional[str]:
    filename = identifier.removeprefix("station:")
    try:
        filename = validators.validate_logo_filename(filename)
    except ValueError:
        return None
    configured = {station.get("logo", "") for station in stations_store.load_stations()}
    if not filename or filename not in configured:
        return None
    # Keep LOGO_DIR as the built-in directory seam for focused artwork tests.
    managed = os.path.join(logo_store.managed_logo_dir(), filename)
    if os.path.isfile(managed):
        return managed
    path = os.path.join(LOGO_DIR, filename)
    return path if os.path.isfile(path) else None


def _path_for(identifier: str) -> Optional[str]:
    if identifier.startswith("station:"):
        return _station_path(identifier)
    return RUNTIME_ARTWORK.get(identifier)


def _content_type(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def load(identifier: str) -> Optional[Artwork]:
    """Return validated ``(content_type, bytes)`` for ``identifier`` if present."""
    path = _path_for(identifier)
    if not path:
        return None
    try:
        size = os.path.getsize(path)
        if size <= 0 or size > MAX_ARTWORK_BYTES:
            return None
        with open(path, "rb") as handle:
            data = handle.read(MAX_ARTWORK_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_ARTWORK_BYTES:
        return None
    content_type = _content_type(data)
    return (content_type, data) if content_type else None
