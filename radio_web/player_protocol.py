"""Shared local protocol for playback commands sent to ``radio.py``."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

SOCKET_PATH = os.environ.get("RADIO_CONTROL_SOCKET", "/run/radio-control.sock")
ACTION_IDS: Tuple[str, ...] = ("play", "pause", "next", "previous")
MAX_MESSAGE_BYTES = 4 * 1024
SOCKET_TIMEOUT_SECONDS = 2.0


def encode_request(action: str) -> bytes:
    """Encode one argument-free playback request."""
    return (json.dumps({"action": action, "args": {}}) + "\n").encode("utf-8")


def decode_request(raw: bytes) -> str:
    """Decode a request and reject malformed fields or arguments."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("request must be a JSON object")
    if set(data) - {"action", "args"}:
        raise ValueError("request contains unknown fields")
    action = data.get("action")
    if not isinstance(action, str):
        raise ValueError("missing action")
    args = data.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    if args:
        raise ValueError("playback actions do not accept arguments")
    return action


def encode_response(ok: bool, code: str, message: str, source: str) -> bytes:
    """Encode one bounded, structured playback result."""
    payload = {
        "ok": bool(ok),
        "code": str(code),
        "message": str(message),
        "source": str(source),
    }
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("response is too large")
    return raw


def decode_response(raw: bytes) -> Dict[str, Any]:
    """Decode and validate a structured playback result."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")
    if set(data) != {"ok", "code", "message", "source"}:
        raise ValueError("response has invalid fields")
    if not isinstance(data["ok"], bool):
        raise ValueError("response ok must be a boolean")
    for field in ("code", "message", "source"):
        if not isinstance(data[field], str):
            raise ValueError(f"response {field} must be a string")
    return data
