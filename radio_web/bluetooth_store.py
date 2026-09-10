"""Read-only Bluetooth adapter status for the dashboard.

Out-of-process and read-only, mirroring the rest of :mod:`radio_web.system_status`:
the web UI cannot touch the live BlueZ objects the player reads, so it shells out
to a single ``bluetoothctl show`` subcommand (``shell=False``, short timeout,
defensive parsing) to surface the adapter alias/power/discoverable state on the
dashboard as **informational only**.

**No mutating action lives here** — the web UI exposes no Bluetooth controls. The
adapter alias is read-only and follows the system hostname. This module only
*reads*, and every collector degrades to ``available=False`` / ``None`` rather
than raising, because the dashboard must render even when Bluetooth is disabled,
the adapter is missing, or ``bluetoothctl`` is unavailable.
"""
import subprocess
from typing import Any, Dict, List, Optional

# Read-only bluetoothctl subcommand (argument list, shell=False, short timeout).
# Overridable module-level so tests can point at fakes.
BLUETOOTHCTL = "bluetoothctl"
_COMMAND_TIMEOUT = 3.0

_SHOW_CMD = [BLUETOOTHCTL, "show"]


def _run(cmd: List[str]) -> Optional[str]:
    """Run a read-only command (``shell=False``); return stdout or ``None``."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            cmd, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _parse_show(text: str) -> Dict[str, Any]:
    """Parse ``bluetoothctl show`` output into adapter fields."""
    info: Dict[str, Any] = {
        "alias": None,
        "powered": None,
        "discoverable": None,
        "pairable": None,
    }
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Alias:"):
            info["alias"] = line.split(":", 1)[1].strip()
        elif line.startswith("Powered:"):
            info["powered"] = line.split(":", 1)[1].strip() == "yes"
        elif line.startswith("Discoverable:"):
            info["discoverable"] = line.split(":", 1)[1].strip() == "yes"
        elif line.startswith("Pairable:"):
            info["pairable"] = line.split(":", 1)[1].strip() == "yes"
    return info


def adapter_info() -> Dict[str, Any]:
    """Return the adapter alias / power / discoverable / pairable fields.

    The single entry point the dashboard renders from. A missing adapter or an
    unavailable ``bluetoothctl`` yields ``available=False`` so the dashboard can
    show a graceful placeholder instead of blanking.
    """
    out = _run(_SHOW_CMD)
    if not out:
        return {
            "available": False,
            "alias": None,
            "powered": None,
            "discoverable": None,
            "pairable": None,
        }
    info = _parse_show(out)
    info["available"] = True
    return info
