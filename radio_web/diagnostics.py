"""Diagnostics bundle: a bounded, secret-free archive of volatile logs.

The maintenance page offers a one-click download of a diagnostics bundle
It is a ``tar.gz`` built entirely in memory
from:

* the **volatile** ``/tmp`` logs (labelled as such — they are cleared on
  reboot), and
* read-only status snapshots captured at download time via safe, ``shell=False``
  commands (``uname``, ``/proc/cmdline``, ``free``, ``df``, ``dmesg | tail``,
  app version, per-source status, heartbeat age).

Hard rules (§13): the bundle is capped at 1 MiB (oldest/largest logs are
truncated first to stay under the cap) and **never** includes ``admin.secret``
or ``wpa_supplicant.conf``. Standard library only.
"""
import io
import logging
import os
import subprocess
import tarfile
import time
from typing import List, Tuple

from . import system_status

logger = logging.getLogger("radio_web.diagnostics")

# 1 MiB cap for the whole (uncompressed member) payload.
MAX_BUNDLE_BYTES = 1 * 1024 * 1024

# Explicit include-list of volatile /tmp logs. Globs are
# expanded at build time; missing files are simply skipped.
LOG_GLOBS: Tuple[str, ...] = (
    "/tmp/radio*.log*",
    "/tmp/provision-from-boot.log",
    "/tmp/S41wlan.log",
    "/tmp/S42bluetooth.log",
    "/tmp/radio-boot-splash.log",
)

# Never include these, even if a glob or future edit would otherwise match.
FORBIDDEN_BASENAMES = frozenset({"admin.secret", "wpa_supplicant.conf"})

# Read-only snapshot commands (argument lists, shell=False, short timeout).
_SNAPSHOT_TIMEOUT = 3.0
_SNAPSHOT_COMMANDS: Tuple[Tuple[str, List[str]], ...] = (
    ("uname.txt", ["uname", "-a"]),
    ("free.txt", ["free"]),
    ("df.txt", ["df", "-h"]),
)


def _run(cmd: List[str]) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            cmd,
            capture_output=True,
            text=True,
            timeout=_SNAPSHOT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(command failed: {exc})\n"
    return result.stdout or result.stderr or ""


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _dmesg_tail(lines: int = 200) -> str:
    text = _run(["dmesg"])
    if not text:
        return ""
    return "\n".join(text.splitlines()[-lines:]) + "\n"


def _iter_log_files() -> List[str]:
    import glob

    found: List[str] = []
    for pattern in LOG_GLOBS:
        for path in sorted(glob.glob(pattern)):
            base = os.path.basename(path)
            if base in FORBIDDEN_BASENAMES:
                continue
            if os.path.isfile(path) and path not in found:
                found.append(path)
    return found


def _snapshot_text() -> str:
    """Assemble the read-only status snapshots into one text blob."""
    parts = ["=== Diagnostics snapshot (volatile) ===\n"]
    parts.append(f"generated_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    parts.append(f"app_version: {system_status.app_version()}\n")
    parts.append(f"cmdline: {_read_file('/proc/cmdline')}")
    heartbeat = system_status.heartbeat_age_seconds()
    parts.append(f"heartbeat_age_seconds: {heartbeat}\n")
    player = system_status.player_status()
    parts.append(f"player_available: {player.get('available')}\n")
    parts.append(f"active_source: {player.get('active_source')}\n")
    parts.append(f"sources: {player.get('sources')}\n")
    return "".join(parts)


def build_bundle(now: float = 0.0) -> Tuple[str, bytes]:
    """Build the diagnostics ``tar.gz`` in memory; return ``(filename, data)``.

    Truncates the largest logs first so the archive's member payload stays at
    or below :data:`MAX_BUNDLE_BYTES`. Never raises for a missing log or a
    failing command — those degrade to an absent/short member.
    """
    now = now or time.time()
    # (arcname, bytes) members. Snapshots first (small, always wanted).
    members: List[Tuple[str, bytes]] = []
    for arcname, cmd in _SNAPSHOT_COMMANDS:
        members.append((f"snapshots/{arcname}", _run(cmd).encode("utf-8", "replace")))
    members.append(("snapshots/dmesg-tail.txt", _dmesg_tail().encode("utf-8", "replace")))
    members.append(("snapshots/status.txt", _snapshot_text().encode("utf-8", "replace")))

    budget = MAX_BUNDLE_BYTES - sum(len(data) for _name, data in members)
    # Add logs largest-first so, if we run out of budget, the biggest offenders
    # are the ones truncated.
    log_files = _iter_log_files()
    log_files.sort(key=lambda p: os.path.getsize(p), reverse=True)
    for path in log_files:
        if budget <= 0:
            break
        data = _read_file(path).encode("utf-8", "replace")
        if len(data) > budget:
            # Keep the tail (most recent) and note the truncation.
            note = b"... [truncated to fit the 1 MiB diagnostics cap] ...\n"
            keep = max(0, budget - len(note))
            data = note + data[len(data) - keep:] if keep else note
        budget -= len(data)
        members.append((f"logs/{os.path.basename(path)}", data))

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for arcname, data in members:
            if os.path.basename(arcname) in FORBIDDEN_BASENAMES:
                continue
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mtime = int(now)
            tar.addfile(info, io.BytesIO(data))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return f"radio-diagnostics-{stamp}.tar.gz", buffer.getvalue()
