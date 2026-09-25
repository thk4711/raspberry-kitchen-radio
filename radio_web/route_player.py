"""Passwordless public playback-control and metadata API."""

import json
import logging
import re
import socket
from typing import Any, Dict
from urllib.parse import urlencode

from . import player_protocol, system_status
from .route_common import _JSON, Request, Response

logger = logging.getLogger("radio_web.route_player")

API_PREFIX = "/api/v1/player"
API_PATHS = frozenset(
    {
        API_PREFIX,
        f"{API_PREFIX}/play",
        f"{API_PREFIX}/pause",
        f"{API_PREFIX}/next",
        f"{API_PREFIX}/previous",
    }
)

_SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ARTWORK_IDS = frozenset({"airplay-jpg", "airplay-png", "bluetooth", "spotify"})
_STATION_ARTWORK_RE = re.compile(r"^station:[A-Za-z0-9._-]+\.(?:png|jpg|jpeg)$", re.IGNORECASE)
_ARTWORK_VERSION_RE = re.compile(r"^[A-Fa-f0-9]{1,64}$")
_MAX_METADATA_LENGTH = 1024


class PlayerUnavailable(Exception):
    """The local player-control socket could not return a valid response."""


def _json_response(status: int, payload: Dict[str, Any]) -> Response:
    return (
        status,
        _JSON,
        json.dumps(payload, separators=(",", ":")),
        [("Cache-Control", "no-store")],
    )


def api_error(status: int, code: str, message: str) -> Response:
    """Return a stable JSON error for an API request."""
    return _json_response(
        status,
        {"ok": False, "code": code, "message": message, "source": ""},
    )


def _safe_text(value: Any) -> str:
    return value[:_MAX_METADATA_LENGTH] if isinstance(value, str) else ""


def _safe_source(value: Any) -> str:
    return value if isinstance(value, str) and _SOURCE_ID_RE.fullmatch(value) else ""


def _safe_artwork(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    identifier = value.get("id")
    version = value.get("version")
    if not isinstance(identifier, str) or not isinstance(version, str):
        return {}
    if identifier not in _ARTWORK_IDS and _STATION_ARTWORK_RE.fullmatch(identifier) is None:
        return {}
    if _ARTWORK_VERSION_RE.fullmatch(version) is None:
        return {}
    query = urlencode({"id": identifier, "v": version})
    return {
        "id": identifier,
        "version": version,
        "url": f"/dashboard/artwork?{query}",
    }


def public_status(player: Dict[str, Any]) -> Dict[str, Any]:
    """Project a status snapshot onto the fixed public schema."""
    available = player.get("available") is True
    stale = player.get("stale") is not False
    power = player.get("power") if isinstance(player.get("power"), bool) else None
    active_source = _safe_source(player.get("active_source")) or None

    now_playing = player.get("now_playing")
    if not isinstance(now_playing, dict):
        now_playing = {}
    raw_sources = player.get("sources")
    if not isinstance(raw_sources, dict):
        raw_sources = {}

    sources: Dict[str, Dict[str, bool]] = {}
    for raw_source_id, entry in raw_sources.items():
        source_id = _safe_source(raw_source_id)
        if source_id and isinstance(entry, dict) and isinstance(entry.get("playing"), bool):
            sources[source_id] = {"playing": entry["playing"]}

    playing = None
    active_entry = sources.get(active_source or "")
    if active_entry is not None:
        playing = active_entry["playing"]
    elif isinstance(now_playing.get("state"), bool):
        playing = now_playing["state"]

    return {
        "available": available,
        "stale": stale,
        "power": power,
        "active_source": active_source,
        "playing": playing,
        "metadata": {
            "name": _safe_text(now_playing.get("name")),
            "title": _safe_text(now_playing.get("title")),
            "artwork": _safe_artwork(now_playing.get("artwork")),
        },
        "sources": sources,
    }


def _exchange(action: str) -> Dict[str, Any]:
    if action not in player_protocol.ACTION_IDS:
        raise ValueError("unknown playback action")

    request = player_protocol.encode_request(action)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(player_protocol.SOCKET_TIMEOUT_SECONDS)
            client.connect(player_protocol.SOCKET_PATH)
            client.sendall(request)
            chunks = []
            total = 0
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if b"\n" in chunk or total > player_protocol.MAX_MESSAGE_BYTES:
                    break
    except (OSError, TimeoutError) as exc:
        raise PlayerUnavailable from exc

    raw = b"".join(chunks)
    if (
        not raw
        or len(raw) > player_protocol.MAX_MESSAGE_BYTES
        or not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
    ):
        raise PlayerUnavailable
    try:
        return player_protocol.decode_response(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PlayerUnavailable from exc


def _player_get(_req: Request) -> Response:
    return _json_response(200, public_status(system_status.player_status()))


def _control(req: Request, action: str) -> Response:
    if req.media_type != "application/json":
        return api_error(415, "unsupported_media_type", "Use application/json.")
    try:
        payload = json.loads(req.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return api_error(400, "invalid_json", "The request body must be valid JSON.")
    if payload != {}:
        return api_error(400, "invalid_request", "Playback commands do not accept arguments.")

    try:
        result = _exchange(action)
    except PlayerUnavailable:
        logger.warning("Playback command unavailable: player socket did not respond")
        return api_error(503, "player_unavailable", "The player is unavailable.")

    code = result["code"]
    ok = result["ok"]
    source = _safe_source(result["source"])
    public_results = {
        "accepted": (200, True, "Playback command accepted"),
        "power_off": (409, False, "Power switch is off"),
        "command_failed": (409, False, "Playback command was rejected"),
        "internal_error": (500, False, "Playback command failed"),
    }
    mapped = public_results.get(code)
    if mapped is None or ok is not mapped[1]:
        logger.warning("Playback command returned an invalid result")
        return api_error(503, "player_unavailable", "The player is unavailable.")
    status, _expected_ok, message = mapped
    return _json_response(
        status,
        {"ok": ok, "code": code, "message": message, "source": source},
    )


def _player_play_post(req: Request) -> Response:
    return _control(req, "play")


def _player_pause_post(req: Request) -> Response:
    return _control(req, "pause")


def _player_next_post(req: Request) -> Response:
    return _control(req, "next")


def _player_previous_post(req: Request) -> Response:
    return _control(req, "previous")
