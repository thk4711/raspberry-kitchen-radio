"""Reboot-persistent, capability-protected firmware update progress."""

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

TRACKING_PATH = Path("/data/update/upload/current.json")
MAX_TRACKING_BYTES = 16 * 1024
TOKEN_TTL_SECONDS = 24 * 60 * 60
WEB_GID = 601

_PUBLIC_FIELDS = {
    "state",
    "message",
    "version",
    "active_slot",
    "target_slot",
    "percent",
    "result",
    "failure_reason",
    "updated_at",
}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _read() -> Optional[Dict[str, Any]]:
    try:
        if TRACKING_PATH.stat().st_size > MAX_TRACKING_BYTES:
            return None
        value = json.loads(TRACKING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _write(value: Dict[str, Any]) -> None:
    TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        fd, temporary = tempfile.mkstemp(dir=str(TRACKING_PATH.parent), prefix=".tracking-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.geteuid() == 0:
            os.chown(temporary, 0, WEB_GID)
            os.chmod(temporary, 0o640)
        else:
            os.chmod(temporary, 0o600)
        os.replace(temporary, TRACKING_PATH)
        temporary = ""
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def create() -> str:
    """Create the sole active tracker and return its unpersisted capability token."""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    _write(
        {
            "schema": 1,
            "token_sha256": _digest(token),
            "created_at": now,
            "updated_at": now,
            "state": "starting",
            "message": "Preparing the firmware update.",
        }
    )
    return token


def update(state: str, **values: Any) -> None:
    """Update the current tracker while retaining its capability hash."""
    current = _read()
    if current is None or not isinstance(current.get("token_sha256"), str):
        return
    if current.get("state") in {"accepted", "automatic_rollback", "failed", "reboot_failed"}:
        return
    current_target = current.get("target_slot")
    supplied_target = values.get("target_slot")
    if current_target and supplied_target and current_target != supplied_target:
        return
    current["state"] = str(state)[:32]
    current["updated_at"] = int(time.time())
    for key in _PUBLIC_FIELDS - {"state", "updated_at"}:
        if key in values and values[key] is not None:
            value = values[key]
            current[key] = (
                str(value)[:160] if key in {"message", "result", "failure_reason"} else value
            )
    _write(current)


def status(token: str) -> Optional[Dict[str, Any]]:
    """Return bounded public state when ``token`` authorizes the current tracker."""
    current = _read()
    if current is None or not token:
        return None
    expected = current.get("token_sha256")
    created = current.get("created_at")
    if not isinstance(expected, str) or not isinstance(created, int):
        return None
    if int(time.time()) - created > TOKEN_TTL_SECONDS:
        return None
    try:
        actual = _digest(token)
    except UnicodeEncodeError:
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    return {key: current[key] for key in _PUBLIC_FIELDS if key in current}
