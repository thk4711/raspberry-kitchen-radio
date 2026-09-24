"""Pure helpers for publishing the player's read-only status snapshot.

Kept separate from ``radio.py`` (which imports the full hardware/display stack)
so the snapshot serialization and atomic write can be unit-tested in isolation.
The web administration interface (``radio_web``) reads the file this writes.
Only display-safe primitives are published — never
stream URLs, credentials or any secret.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The player runs as root while the dashboard runs as the unprivileged
# ``radio-web`` user.  The snapshot contains only display-safe data (never URLs
# or credentials), so it must be world-readable for that privilege split to
# work.  Apply the mode to each temporary file before the atomic replacement so
# the destination is never exposed with tempfile's default 0600 mode.
STATUS_FILE_MODE = 0o644
_ARTWORK_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.(?:png|jpg|jpeg)$", re.IGNORECASE)


def artwork_descriptor(source: str, cover: str, fingerprint: str = "") -> Dict[str, str]:
    """Return a safe artwork ID/version without exposing ``cover``'s file path."""
    identifier = ""
    basename = os.path.basename(cover or "")
    if (
        source == "mpd"
        and cover
        and basename == cover.rsplit("/", 1)[-1]
        and ".." not in cover
        and _ARTWORK_FILENAME_RE.fullmatch(basename)
    ):
        identifier = f"station:{basename}"
    elif source == "spotify" and cover == "/tmp/spotify_cover.jpg":
        identifier = "spotify"
    elif source == "bluetooth" and cover == "/tmp/bluetooth_cover.jpg":
        identifier = "bluetooth"
    elif source == "airplay" and cover in (
        "/tmp/shairport-image.jpg",
        "/tmp/shairport-image.png",
    ):
        identifier = "airplay-png" if cover.endswith(".png") else "airplay-jpg"
    if not identifier:
        return {}
    version_source = fingerprint or identifier
    version = hashlib.sha256(version_source.encode("utf-8")).hexdigest()[:16]
    return {"id": identifier, "version": version}


def resolve_status_file(config: Dict[str, Any], env_value: Optional[str]) -> str:
    """Resolve the status-snapshot path, or ``''`` when disabled/unwritable.

    Resolution order mirrors the heartbeat: ``env_value`` (the
    ``RADIO_STATUS_FILE`` env var) wins, then ``[status] file`` in the config,
    else a tmpfs default. ``[status] enabled = false`` or an empty env value
    disables it. Returns ``''`` when disabled or when the target is not
    writable, so the caller treats writing as a no-op.
    """
    st_conf = config.get("status", {})
    if st_conf.get("enabled", True) is False:
        return ""

    if env_value is not None:
        path = env_value.strip()
    else:
        path = str(st_conf.get("file", "/tmp/radio-status.json")).strip()
    if not path:
        return ""

    try:
        with open(path, "a"):
            pass
    except OSError as exc:
        logger.warning("Status snapshot disabled; cannot write %s: %s", path, exc)
        return ""
    logger.info("Status snapshot file: %s", path)
    return path


def build_snapshot(
    power: bool,
    active_name: str,
    now_playing: Dict[str, Any],
    services: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the display-safe status dict published to the web UI."""
    sources = {service["name"]: {"playing": bool(service.get("state"))} for service in services}
    return {
        "updated_at": time.time(),
        "power": bool(power),
        "active_source": active_name,
        "now_playing": now_playing,
        "sources": sources,
    }


def write_snapshot(status_file: str, snapshot: Dict[str, Any]) -> None:
    """Atomically write ``snapshot`` as JSON; a silent no-op when disabled.

    Uses ``tempfile`` + ``os.replace`` so a reader never
    observes a partial file. Any error degrades to a no-op.
    """
    if not status_file:
        return
    try:
        payload = json.dumps(snapshot)
    except (TypeError, ValueError) as exc:
        logger.debug("Unable to serialize status snapshot: %s", exc)
        return
    directory = os.path.dirname(status_file) or "."
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, STATUS_FILE_MODE)
        os.replace(tmp_path, status_file)
    except OSError as exc:
        logger.debug("Unable to write status snapshot: %s", exc)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
