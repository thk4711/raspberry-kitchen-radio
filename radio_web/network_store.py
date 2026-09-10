"""WiFi + static-IP configuration for the web administration interface.

This is the highest-risk managed setting:
a bad WiFi change can lock the admin out of the very interface that made it. So
the flow is **never** a plain write-and-restart. It is save → validate → try →
**auto-rollback**:

1. The web layer validates the SSID / passphrase / country / optional static IP.
2. The privileged helper (:mod:`radio_web.helper`) keeps the current
   ``wpa_supplicant.conf`` (and static-IP env) as ``*.prev``, writes the new
   config, drops a ``/run`` *pending* marker with a deadline, and restarts WiFi.
3. A bounded watcher confirms association + an IP before the deadline. On
   success it promotes the config and clears the marker; on failure (or if the
   admin never clicks "Confirm") it restores ``*.prev`` and restarts WiFi.

The one-shot SD-card ``radio-config.txt`` remains the recovery route if the box
drops off the network entirely. Once its active settings have been consumed and
commented, web writes persist in ``/etc/wpa_supplicant.conf`` and the static-IP
handoff file that ``S41wlan`` reads.

This module holds the **web-side** pieces only: validating the submitted form,
building the normalised config dict the helper consumes, and reading the current
read-only status (SSID, mode, pending-rollback state). All privileged file
writes and the rollback timer live in the helper. Standard-library only.
"""
import os
import re
import subprocess
from typing import Any, Dict, Optional

from . import validators

# Persistent handoff files the privileged helper and S41wlan use. The SD-card
# radio-config.txt is one-shot, so later boots and web edits reuse these files.
# Overridable so tests never touch the real system paths.
STATIC_IP_FILE = os.environ.get("RADIO_WLAN_STATIC_FILE", "/etc/radio/wlan-static.env")
ROLLBACK_MARKER = os.environ.get(
    "RADIO_WLAN_ROLLBACK_MARKER", "/run/wlan-rollback-pending"
)
WIFI_COUNTRY_FILE = os.environ.get("RADIO_WIFI_COUNTRY_FILE", "/etc/radio/wifi-country")

# How long the rollback watcher waits for association + an IP before reverting.
DEFAULT_ROLLBACK_SECONDS = 60

_IW_LINK_CMD = ["iw", "dev", "wlan0", "link"]
_COMMAND_TIMEOUT = 2.0


def validate_wifi_form(form: Dict[str, str]) -> Dict[str, str]:
    """Return a normalised WiFi/static-IP config dict, or raise ``ValueError``.

    Keys returned: ``ssid``, ``psk``, ``country`` (may be ``""``), and the
    static-IP quartet ``ip_address``/``ip_prefix``/``ip_gateway``/``ip_dns``
    which are all ``""`` when DHCP is selected. The helper re-validates every
    field before writing anything.
    """
    cleaned: Dict[str, str] = {
        "ssid": validators.validate_ssid(form.get("ssid", "")),
        "psk": validators.validate_wifi_passphrase(form.get("psk", "")),
        "country": validators.validate_country_code(form.get("country", "")),
        "ip_address": "",
        "ip_prefix": "",
        "ip_gateway": "",
        "ip_dns": "",
    }
    mode = (form.get("ip_mode", "dhcp") or "dhcp").strip().lower()
    if mode not in ("dhcp", "static"):
        raise ValueError("Choose DHCP or a static address.")
    if mode == "static":
        cleaned["ip_address"] = validators.validate_ipv4(
            form.get("ip_address", ""), "IP address"
        )
        cleaned["ip_prefix"] = str(
            validators.validate_ipv4_prefix(form.get("ip_prefix", "24"))
        )
        cleaned["ip_gateway"] = validators.validate_ipv4(
            form.get("ip_gateway", ""), "Gateway"
        )
        cleaned["ip_dns"] = validators.validate_dns_list(form.get("ip_dns", ""))
    return cleaned


def rollback_pending() -> Optional[Dict[str, Any]]:
    """Return the pending-rollback state, or ``None`` when nothing is pending.

    The helper writes a ``deadline=<epoch>`` line into :data:`ROLLBACK_MARKER`
    when a WiFi change is on trial. The page reads it to show a "confirm this
    still works / it will auto-revert" banner. A malformed marker is treated as
    "no pending change".
    """
    try:
        with open(ROLLBACK_MARKER, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    deadline = None
    for line in text.splitlines():
        if line.startswith("deadline="):
            try:
                deadline = float(line.split("=", 1)[1].strip())
            except ValueError:
                deadline = None
    return {"deadline": deadline}


def _read_static_env() -> Dict[str, str]:
    """Parse the running static-IP handoff file into a dict (best effort)."""
    result: Dict[str, str] = {}
    try:
        with open(STATIC_IP_FILE, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                result[key.strip()] = value.strip().strip('"')
    except OSError:
        return {}
    return result


def _current_ssid() -> Optional[str]:
    """Return the associated SSID via ``iw dev wlan0 link`` (or ``None``)."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            _IW_LINK_CMD, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"^\s*SSID:\s*(.+)$", out, re.MULTILINE)
    return match.group(1).strip() if match else None


def current_status() -> Dict[str, Any]:
    """Return read-only network status for the page (never raises)."""
    static = _read_static_env()
    return {
        "ssid": _current_ssid(),
        "mode": "static" if static.get("IP") else "dhcp",
        "static": static,
        "pending": rollback_pending(),
    }
