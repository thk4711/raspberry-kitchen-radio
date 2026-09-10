"""Privileged WiFi + static-IP apply with save → try → auto-rollback.

Runs **only inside the root helper** (:mod:`radio_web.helper`). It writes
``/etc/wpa_supplicant.conf`` and the static-IP handoff file that ``S41wlan``
reads, restarts WiFi, then spawns a bounded background watcher that reverts to
the previous config if association + an IP address is not confirmed before a
deadline (never a plain write-and-restart). Every value is re-validated here
even though the web layer already validated it, because the helper trusts
nothing.

Standard-library only. The one-shot SD-card ``radio-config.txt`` remains the
ultimate recovery route.
"""

import logging
import os
import subprocess
import threading
import time
from typing import Dict, Tuple

from . import network_store, validators

logger = logging.getLogger("radio_web.network_apply")

# Files the helper owns. Overridable so tests never touch the real /etc or /run.
WPA_CONF = os.environ.get("RADIO_WPA_CONF", "/etc/wpa_supplicant.conf")
WPA_PREV = WPA_CONF + ".prev"
WLAN_INIT_SCRIPT = os.environ.get("RADIO_WLAN_INIT", "/etc/init.d/S41wlan")

_RESTART_TIMEOUT_SECONDS = 30.0
# How often the watcher polls for association + an IP while on trial.
_WATCH_POLL_SECONDS = 3.0


def _wpa_escape(value: str) -> str:
    """Escape ``"`` and ``\\`` for a double-quoted wpa_supplicant field."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_wpa(ssid: str, psk: str) -> str:
    """Render a minimal single-network wpa_supplicant.conf (WPA-PSK only)."""
    return (
        "# Written by radio-web (privileged helper). The target wpa_supplicant is\n"
        "# built without CONFIG_CTRL_IFACE, so only a bare network={...} block\n"
        "# with key_mgmt=WPA-PSK is allowed.\n"
        "network={\n"
        f'    ssid="{_wpa_escape(ssid)}"\n'
        f'    psk="{_wpa_escape(psk)}"\n'
        "    key_mgmt=WPA-PSK\n"
        "}\n"
    )


def _render_static_env(config: Dict[str, str]) -> str:
    """Render the S41wlan static-IP handoff env file (or empty for DHCP)."""
    if not config.get("ip_address"):
        return ""
    prefix = config.get("ip_prefix") or "24"
    lines = [
        "# Written by radio-web. S41wlan applies this instead of DHCP.",
        f'IP="{config["ip_address"]}"',
        f'PREFIX="{prefix}"',
    ]
    if config.get("ip_gateway"):
        lines.append(f'ROUTER="{config["ip_gateway"]}"')
    if config.get("ip_dns"):
        lines.append(f'DNS="{config["ip_dns"]}"')
    return "\n".join(lines) + "\n"


def _atomic_write(path: str, data: str, mode: int = 0o600) -> None:
    target = os.path.realpath(path)
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{target}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, target)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _restart_wifi() -> bool:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            [WLAN_INIT_SCRIPT, "restart"],
            capture_output=True,
            text=True,
            timeout=_RESTART_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("wifi restart failed: %s", exc)
        return False
    return result.returncode == 0


def _validate_config(config: Dict[str, str]) -> Dict[str, str]:
    """Re-validate every field server-side; raise ``ValueError`` on any bad one."""
    cleaned = {
        "ssid": validators.validate_ssid(config.get("ssid", "")),
        "psk": validators.validate_wifi_passphrase(config.get("psk", "")),
        "country": validators.validate_country_code(config.get("country", "")),
        "ip_address": "",
        "ip_prefix": "",
        "ip_gateway": "",
        "ip_dns": "",
    }
    if config.get("ip_address"):
        cleaned["ip_address"] = validators.validate_ipv4(config["ip_address"], "IP address")
        cleaned["ip_prefix"] = str(validators.validate_ipv4_prefix(config.get("ip_prefix", "24")))
        if config.get("ip_gateway"):
            cleaned["ip_gateway"] = validators.validate_ipv4(config["ip_gateway"], "Gateway")
        if config.get("ip_dns"):
            cleaned["ip_dns"] = validators.validate_dns_list(config["ip_dns"])
    return cleaned


def apply_wifi(config: Dict[str, str], rollback_seconds: int = 0) -> Tuple[bool, str]:
    """Save the current config, write the new one, restart WiFi, arm rollback.

    Returns ``(ok, message)``. On any validation or write failure nothing is
    changed. On success a background watcher is armed that reverts unless
    :func:`confirm` is called before the deadline.
    """
    try:
        cleaned = _validate_config(config)
    except ValueError as exc:
        return False, str(exc)

    seconds = rollback_seconds or network_store.DEFAULT_ROLLBACK_SECONDS
    # 1. Keep the current config as *.prev (and remember the prior static env).
    _atomic_write(WPA_PREV, _read(WPA_CONF), 0o600)
    _atomic_write(WPA_PREV + ".static", _read(network_store.STATIC_IP_FILE), 0o600)

    # 2. Write the new config + static-IP handoff + country handoff.
    _atomic_write(WPA_CONF, _render_wpa(cleaned["ssid"], cleaned["psk"]), 0o600)
    static_env = _render_static_env(cleaned)
    if static_env:
        _atomic_write(network_store.STATIC_IP_FILE, static_env, 0o644)
    else:
        _remove(network_store.STATIC_IP_FILE)
    if cleaned["country"]:
        _atomic_write(network_store.WIFI_COUNTRY_FILE, cleaned["country"] + "\n", 0o644)

    # 3. Drop the pending marker with a deadline, restart WiFi, arm the watcher.
    deadline = time.time() + seconds
    _atomic_write(network_store.ROLLBACK_MARKER, f"deadline={deadline}\n", 0o644)
    if not _restart_wifi():
        rollback()
        return False, "Could not restart WiFi; reverted to the previous network."
    _arm_watcher(seconds)
    logger.info("apply_wifi: new config on trial for %ss", seconds)
    return True, (
        f"Trying the new WiFi settings. If the radio does not reconnect within "
        f"{seconds} seconds it will automatically revert."
    )


def confirm() -> Tuple[bool, str]:
    """Promote the pending change: drop ``*.prev`` and clear the marker."""
    _remove(network_store.ROLLBACK_MARKER)
    _remove(WPA_PREV)
    _remove(WPA_PREV + ".static")
    logger.info("wifi change confirmed")
    return True, "WiFi settings confirmed."


def rollback() -> Tuple[bool, str]:
    """Restore the previous WiFi config now and restart WiFi."""
    prev = _read(WPA_PREV)
    if prev:
        _atomic_write(WPA_CONF, prev, 0o600)
    prev_static = _read(WPA_PREV + ".static")
    if prev_static:
        _atomic_write(network_store.STATIC_IP_FILE, prev_static, 0o644)
    else:
        _remove(network_store.STATIC_IP_FILE)
    _remove(network_store.ROLLBACK_MARKER)
    _remove(WPA_PREV)
    _remove(WPA_PREV + ".static")
    _restart_wifi()
    logger.info("wifi change rolled back")
    return True, "Reverted to the previous WiFi settings."


def _has_ip() -> bool:
    """Return True iff wlan0 currently has a global IPv4 address."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            ["ip", "-o", "-4", "addr", "show", "dev", "wlan0"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "inet " in out


def _arm_watcher(seconds: int) -> None:
    """Spawn a daemon thread that reverts unless confirmed before the deadline."""
    thread = threading.Thread(target=_watch, args=(seconds,), daemon=True, name="wifi-rollback")
    thread.start()


def _watch(seconds: int) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        # Confirmed (marker gone) -> nothing to do.
        if not os.path.isfile(network_store.ROLLBACK_MARKER):
            return
        time.sleep(_WATCH_POLL_SECONDS)
    # Deadline passed. If still pending and we have no IP, revert.
    if os.path.isfile(network_store.ROLLBACK_MARKER) and not _has_ip():
        logger.warning("wifi trial timed out without an IP; rolling back")
        rollback()
