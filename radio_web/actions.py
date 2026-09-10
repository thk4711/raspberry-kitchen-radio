"""Client for the fixed action-id → privileged operation mapping.

The web UI never accepts a shell string, service path, filename or executable
name from HTTP input. Instead the browser sends a **fixed action identifier**
(e.g. ``restart_radio``) and this module forwards it — after checking it against
the whitelist — to the **root-owned helper** over a local unix socket
(:mod:`radio_web.helper`, :mod:`radio_web.helper_protocol`). Anything not in
:data:`radio_web.helper_protocol.ACTION_IDS` is rejected here, before any socket
call is made.

The privilege split is complete:
``radio-web`` now runs as a dedicated unprivileged user, so it cannot restart
services or reboot the board itself — every privileged operation goes over the
socket to the helper, which validates and performs it. The call surface here is
unchanged from earlier phases (``run_action(action_id)`` returns
``(ok, message)``), so existing call sites in :mod:`radio_web.routes` did not
change.
"""

import logging
import socket
from typing import Any, Dict, Tuple

from . import helper_protocol

logger = logging.getLogger("radio_web.actions")

# Client-side connect + reply timeout (seconds). Kept short so a wedged or
# missing helper never ties up a request worker thread; the helper caps its own
# operations separately.
_CONNECT_TIMEOUT_SECONDS = 35.0
# Applying EQ restarts four independent audio consumers. Each helper-side
# restart remains individually capped, but the complete fixed operation can
# legitimately exceed the ordinary single-action client timeout.
_EQUALIZER_TIMEOUT_SECONDS = 130.0
# Client-side whitelist, mirroring the helper. New privileged actions are added
# to helper_protocol.ACTION_IDS (and the helper's dispatch table).
ACTIONS = helper_protocol.ACTION_IDS


def is_valid_action(action_id: str) -> bool:
    """Return True iff ``action_id`` is a known, whitelisted action."""
    return action_id in ACTIONS


def run_action(action_id: str, **args: Any) -> Tuple[bool, str]:
    """Ask the privileged helper to run ``action_id``; reject anything unknown.

    Returns ``(ok, message)``. An unknown/off-whitelist id (including attempts
    like ``"/bin/sh -c ..."``) is rejected here without contacting the helper.
    A helper that is down, unreachable or slow degrades to a friendly
    ``(False, message)`` rather than raising.
    """
    if action_id not in ACTIONS:
        logger.warning("run_action: rejected unknown action id")
        return False, "Unknown action."
    request = helper_protocol.encode_request(action_id, args)
    try:
        timeout = (
            _EQUALIZER_TIMEOUT_SECONDS
            if action_id == "apply_equalizer"
            else _CONNECT_TIMEOUT_SECONDS
        )
        return _exchange(request, timeout=timeout)
    except OSError as exc:
        logger.error("run_action: helper unreachable: %s", exc)
        return False, "The privileged helper is not available."


def query_firmware_status() -> Tuple[bool, str, Dict[str, Any]]:
    """Return the helper's bounded, structured firmware status response."""
    request = helper_protocol.encode_request("firmware_status", {})
    try:
        raw = _exchange_raw(request, timeout=_CONNECT_TIMEOUT_SECONDS)
        return helper_protocol.decode_data_response(raw)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.error("query_firmware_status: helper query failed: %s", exc)
        return False, "Firmware status is unavailable.", {}


def _exchange(request: bytes, *, timeout: float = _CONNECT_TIMEOUT_SECONDS) -> Tuple[bool, str]:
    """Send one framed request over the unix socket and read one response."""
    raw = _exchange_raw(request, timeout=timeout)
    try:
        return helper_protocol.decode_response(raw)
    except (ValueError, UnicodeDecodeError):
        return False, "The privileged helper returned a malformed response."


def _exchange_raw(request: bytes, *, timeout: float) -> bytes:
    """Exchange one bounded helper message and return its raw response."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(helper_protocol.SOCKET_PATH)
        sock.sendall(request)
        chunks = []
        total = 0
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if b"\n" in chunk or total > helper_protocol.MAX_MESSAGE_BYTES:
                break
    raw = b"".join(chunks)
    if not raw:
        raise ValueError("the privileged helper returned no response")
    if len(raw) > helper_protocol.MAX_MESSAGE_BYTES:
        raise ValueError("the privileged helper response is too large")
    return raw


# Convenience type alias kept for callers that annotate the action args map.
ActionArgs = Dict[str, Any]
