"""Shared wire protocol between ``radio-web`` and the privileged helper.

The privilege split: the unprivileged
``radio-web`` process asks a **root-owned** helper (``radio_web.helper``,
started by ``S79radio-helper``) to perform a small, fixed set of privileged
operations. The two processes talk over a local **unix domain socket** using
the tiny framing defined here, so the whitelist and encoding can never drift
between client and server — both import this module.

Framing: one request and one response per connection, each a single UTF-8 JSON
object terminated by a newline. A request is ``{"action": "<id>", "args":
{...}}``; a response is ``{"ok": <bool>, "message": "<text>"}``. Only fixed
action identifiers are ever sent (never a shell string, path or executable),
and the server independently re-validates every field.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Tuple

# Local unix socket the helper listens on. Overridable via ``RADIO_HELPER_SOCKET``
# so tests can point client and server at a temp path; the init script and the
# default both use ``/run/radio-helper.sock``.
SOCKET_PATH = os.environ.get("RADIO_HELPER_SOCKET", "/run/radio-helper.sock")

# The complete, fixed whitelist of privileged action identifiers. Anything not
# here is rejected by both the client (before connecting) and the server. New
# privileged actions are added here and nowhere else.
#   restart_radio  restart the player supervisor (re-reads managed config)
#   restart_mpd    restart the MPD service
#   restart_ssh    restart Dropbear (re-reads managed remote-access config)
#   reboot         reboot the board
#   shutdown       power the board off
#   set_hostname   persist the device name (applied on reboot); arg: "name"
#   set_time       persist timezone / NTP server; args: "timezone","ntp_server"
#   set_wifi       write wpa_supplicant.conf + static-IP and try with rollback;
#                  args: ssid, psk, country, ip_address, ip_prefix, ip_gateway,
#                  ip_dns
#   wifi_confirm   promote the pending WiFi change (cancel the auto-rollback)
#   wifi_rollback  restore the previous WiFi config now
#   set_audio_hardware  select the active sound card (applied on reboot); the
#                  concrete config.txt/asound.conf/mpd.conf edits are owned by
#                  the helper and driven by a validated profile id; arg: "profile"
#   apply_equalizer regenerate the ALSA route and restart all PCM consumers;
#                  no arguments are accepted
#   inspect_firmware / install_firmware / cancel_staged_firmware
#                  fixed-path firmware operations; no arguments are accepted
#   firmware_status / prepare_rollback
#                  bounded status or fixed other-slot trial; no arguments accepted
ACTION_IDS: Tuple[str, ...] = (
    "restart_radio",
    "restart_mpd",
    "restart_ssh",
    "reboot",
    "shutdown",
    "set_hostname",
    "set_time",
    "set_wifi",
    "wifi_confirm",
    "wifi_rollback",
    "set_audio_hardware",
    "apply_equalizer",
    "inspect_firmware",
    "install_firmware",
    "cancel_staged_firmware",
    "firmware_status",
    "prepare_rollback",
    "create_data_backup",
    "inspect_data_restore",
    "apply_data_restore",
    "cancel_data_restore",
)

# Cap a framed message so a malformed peer cannot exhaust memory. Requests and
# responses here are tiny (a few fixed fields), so this is a generous safety net.
MAX_MESSAGE_BYTES = 8 * 1024


def encode_request(action: str, args: Dict[str, Any] | None = None) -> bytes:
    """Serialize a request to a newline-terminated JSON line (bytes)."""
    payload = {"action": action, "args": dict(args or {})}
    return (json.dumps(payload) + "\n").encode("utf-8")


def encode_response(ok: bool, message: str, data: Dict[str, Any] | None = None) -> bytes:
    """Serialize a response to a newline-terminated JSON line (bytes)."""
    payload: Dict[str, Any] = {"ok": bool(ok), "message": str(message)}
    if data is not None:
        payload["data"] = data
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("response is too large")
    return raw


def decode_request(raw: bytes) -> Tuple[str, Dict[str, Any]]:
    """Parse a request line into ``(action, args)``.

    Raises :class:`ValueError` on anything malformed: not JSON, not an object,
    a missing/non-string ``action``, or a non-object ``args``. The caller maps
    that to a rejected response.
    """
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("request must be a JSON object")
    action = data.get("action")
    if not isinstance(action, str):
        raise ValueError("missing action")
    args = data.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    return action, args


def decode_response(raw: bytes) -> Tuple[bool, str]:
    """Parse a response line into ``(ok, message)``.

    Raises :class:`ValueError` on anything malformed so the client can surface
    a generic failure rather than trust a garbled reply.
    """
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")
    return bool(data.get("ok", False)), str(data.get("message", ""))


def decode_data_response(raw: bytes) -> Tuple[bool, str, Dict[str, Any]]:
    """Parse a response carrying a bounded structured data object."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")
    values = data.get("data", {})
    if not isinstance(values, dict):
        raise ValueError("response data must be an object")
    return bool(data.get("ok", False)), str(data.get("message", "")), values
