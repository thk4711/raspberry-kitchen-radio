"""Read-only system and player status collection for the dashboard.

Everything here is *out-of-process* and read-only: ``radio_web`` runs as a
separate, unprivileged process from the player (``radio.py``), so it cannot see
the live :class:`MusicSource` objects. Instead it reads:

* volatile ``/proc`` and ``/sys`` files and a couple of read-only commands for
  the system metrics (uptime, memory, CPU temperature, WiFi, disk usage), and
* the JSON status snapshot the player publishes atomically (see
  ``radio.py._write_status_snapshot``) for the
  live now-playing / per-source state.

All filesystem paths and command lists are module-level constants so tests can
point them at fakes. Every collector is defensive: a missing file or a failing
command degrades to ``None``/empty rather than raising, because the dashboard
must render even when the player is down or a metric is unavailable.
"""
import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from . import bluetooth_store

# ---------------------------------------------------------------------------
# Paths / commands (module-level so tests can monkeypatch them).
# ---------------------------------------------------------------------------
PROC_UPTIME = "/proc/uptime"
PROC_MEMINFO = "/proc/meminfo"
PROC_NET_WIRELESS = "/proc/net/wireless"
CPU_TEMP_FILE = "/sys/class/thermal/thermal_zone0/temp"
HOSTNAME_FILE = "/etc/hostname"
ROOTFS_PATH = "/"
WLAN_INTERFACE = "wlan0"

# Status snapshot written by the player. Overridable via RADIO_STATUS_FILE so
# the web service and the player always agree on the path (mirrors the player's
# own resolution order).
STATUS_FILE = os.environ.get("RADIO_STATUS_FILE") or "/tmp/radio-status.json"

# Consider the player snapshot stale after this many seconds without an update
# (~10x the default 3s metadata interval, matching the freeze-watcher slack).
STATUS_STALE_SECONDS = 30

# Read-only external commands. Argument lists, shell=False, short timeouts.
_IP_ADDR_CMD = ["ip", "-o", "-4", "addr", "show", "dev", WLAN_INTERFACE]
_IW_LINK_CMD = ["iw", "dev", WLAN_INTERFACE, "link"]
_COMMAND_TIMEOUT = 2.0

# The canonical source order shown on the dashboard, with display labels.
SOURCE_LABELS = [
    ("mpd", "Internet Radio"),
    ("airplay", "AirPlay"),
    ("spotify", "Spotify"),
    ("bluetooth", "Bluetooth"),
    ("usb", "USB Audio"),
]


def _read_text(path: str) -> Optional[str]:
    """Return the contents of ``path`` or ``None`` on any error."""
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _run(cmd: List[str]) -> Optional[str]:
    """Run a read-only command (``shell=False``); return stdout or ``None``."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def app_version() -> str:
    """Return the application version from ``lib/_version.py``.

    Uses the same single source of truth as ``radio.py --version``. Falls back
    to ``"unknown"`` if the module cannot be located/imported.
    """
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        version_file = os.path.join(repo_root, "lib", "_version.py")
        namespace: Dict[str, Any] = {}
        with open(version_file) as fh:
            exec(compile(fh.read(), version_file, "exec"), namespace)  # noqa: S102
        return str(namespace.get("__version__", "unknown"))
    except Exception:
        return "unknown"


def hostname() -> str:
    """Return the configured hostname (``/etc/hostname``), else ``socket``."""
    text = _read_text(HOSTNAME_FILE)
    if text and text.strip():
        return text.strip()
    try:
        import socket

        return socket.gethostname()
    except OSError:
        return ""


def uptime_seconds() -> Optional[float]:
    """Return system uptime in seconds from ``/proc/uptime`` or ``None``."""
    text = _read_text(PROC_UPTIME)
    if not text:
        return None
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return None


def cpu_temperature_c() -> Optional[float]:
    """Return CPU temperature in °C from the thermal zone, or ``None``."""
    text = _read_text(CPU_TEMP_FILE)
    if not text:
        return None
    try:
        # The value is in millidegrees Celsius.
        return int(text.strip()) / 1000.0
    except ValueError:
        return None


def memory_info() -> Dict[str, Optional[int]]:
    """Return total/available memory in kibibytes from ``/proc/meminfo``.

    Missing fields are reported as ``None`` rather than raising.
    """
    result: Dict[str, Optional[int]] = {"total_kib": None, "available_kib": None}
    text = _read_text(PROC_MEMINFO)
    if not text:
        return result
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "MemTotal:":
            try:
                result["total_kib"] = int(parts[1])
            except ValueError:
                pass
        elif len(parts) >= 2 and parts[0] == "MemAvailable:":
            try:
                result["available_kib"] = int(parts[1])
            except ValueError:
                pass
    return result


def rootfs_usage() -> Dict[str, Optional[int]]:
    """Return total/used/free bytes for the root filesystem."""
    try:
        usage = shutil.disk_usage(ROOTFS_PATH)
        return {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        return {"total": None, "used": None, "free": None}


def wifi_info() -> Dict[str, Optional[Any]]:
    """Return best-effort WiFi/IP info for ``wlan0``.

    Reads the IPv4 address (``ip``), SSID/signal (``iw dev … link``), and signal
    quality from ``/proc/net/wireless``. Any unavailable field is ``None`` so
    the dashboard still renders when WiFi is down or the tools are absent.
    """
    info: Dict[str, Optional[Any]] = {
        "interface": WLAN_INTERFACE,
        "ip": None,
        "ssid": None,
        "signal_dbm": None,
    }

    addr = _run(_IP_ADDR_CMD)
    if addr:
        # e.g. "2: wlan0    inet 192.168.1.42/24 brd ... scope global wlan0"
        parts = addr.split()
        if "inet" in parts:
            cidr = parts[parts.index("inet") + 1]
            info["ip"] = cidr.split("/", 1)[0]

    link = _run(_IW_LINK_CMD)
    if link:
        for line in link.splitlines():
            stripped = line.strip()
            if stripped.startswith("SSID:"):
                info["ssid"] = stripped[len("SSID:"):].strip() or None
            elif stripped.startswith("signal:"):
                # e.g. "signal: -51 dBm"
                tokens = stripped.split()
                if len(tokens) >= 2:
                    try:
                        info["signal_dbm"] = int(float(tokens[1]))
                    except ValueError:
                        pass

    # Fall back to /proc/net/wireless for the signal level if iw was unavailable.
    if info["signal_dbm"] is None:
        wireless = _read_text(PROC_NET_WIRELESS)
        if wireless:
            for line in wireless.splitlines():
                if line.strip().startswith(f"{WLAN_INTERFACE}:"):
                    fields = line.split()
                    # Column 4 is the signal level (dBm), often with a trailing '.'.
                    if len(fields) >= 4:
                        try:
                            info["signal_dbm"] = int(float(fields[3].rstrip(".")))
                        except ValueError:
                            pass
    return info


def player_status(now: Optional[float] = None) -> Dict[str, Any]:
    """Read the player's JSON status snapshot.

    Returns a dict with ``available`` (the snapshot exists and parsed),
    ``stale`` (older than :data:`STATUS_STALE_SECONDS`), ``age_seconds``, and
    the snapshot payload fields when present. A missing or malformed file yields
    ``available=False`` so the dashboard can show "player status unavailable".
    """
    now = time.time() if now is None else now
    result: Dict[str, Any] = {
        "available": False,
        "stale": True,
        "age_seconds": None,
        "power": None,
        "active_source": None,
        "now_playing": {},
        "sources": {},
    }
    text = _read_text(STATUS_FILE)
    if not text:
        return result
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return result
    if not isinstance(data, dict):
        return result

    result["available"] = True
    updated_at = data.get("updated_at")
    if isinstance(updated_at, (int, float)):
        age = max(0.0, now - float(updated_at))
        result["age_seconds"] = age
        result["stale"] = age > STATUS_STALE_SECONDS
    result["power"] = data.get("power")
    result["active_source"] = data.get("active_source")
    if isinstance(data.get("now_playing"), dict):
        result["now_playing"] = data["now_playing"]
    if isinstance(data.get("sources"), dict):
        result["sources"] = data["sources"]
    return result


def heartbeat_age_seconds(
    heartbeat_file: str = "/tmp/radio.alive", now: Optional[float] = None
) -> Optional[float]:
    """Return the age in seconds of the player's liveness heartbeat, or ``None``."""
    now = time.time() if now is None else now
    try:
        mtime = os.path.getmtime(heartbeat_file)
    except OSError:
        return None
    return max(0.0, now - mtime)


def collect(now: Optional[float] = None) -> Dict[str, Any]:
    """Collect the full read-only dashboard status in one dict.

    This is the single entry point the templates render from. Each field is
    independently defensive, so a failure in one collector never blanks the
    whole dashboard.
    """
    now = time.time() if now is None else now
    return {
        "hostname": hostname(),
        "version": app_version(),
        "uptime_seconds": uptime_seconds(),
        "cpu_temperature_c": cpu_temperature_c(),
        "memory": memory_info(),
        "rootfs": rootfs_usage(),
        "wifi": wifi_info(),
        "heartbeat_age_seconds": heartbeat_age_seconds(now=now),
        "player": player_status(now=now),
        "bluetooth": bluetooth_store.adapter_info(),
        "source_labels": SOURCE_LABELS,
    }
