"""Root-only inspection and trial selection of the fixed other firmware slot."""

import fcntl
import json
import logging
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Tuple

from lib import __version__

from . import firmware_installer, persistent_config

logger = logging.getLogger("radio_web.firmware_slots")

FW_PRINTENV = "/usr/bin/fw_printenv"
FW_SETENV = "/usr/bin/fw_setenv"
MOUNT = "/bin/mount"
UMOUNT = "/bin/umount"
REBOOT = "/sbin/reboot"
OTHER_MOUNT = Path("/mnt/firmware-other-ro")
RELEASE_PATH = Path("etc/radio-release.json")
ENV_TMP_DIR = Path("/run")
SLOT_LOCK = Path("/run/firmware-slot.lock")
HARDWARE_REVISION = "1"
MAX_RELEASE_BYTES = 4096
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ENV_KEYS = (
    "active_slot",
    "previous_slot",
    "upgrade_available",
    "bootcount",
    "bootlimit",
    "rollback_from",
)


class SlotError(Exception):
    """Expected, safely displayable slot-inspection or selection failure."""


def _run(argv: list, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(  # noqa: S603 - every caller supplies fixed argv
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SlotError("Firmware slot operation failed.") from exc


def _environment() -> Dict[str, str]:
    result = _run([FW_PRINTENV, *_ENV_KEYS], timeout=10)
    values: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in _ENV_KEYS and len(value) <= 16:
            values[key] = value
    if any(key not in values for key in _ENV_KEYS):
        raise SlotError("U-Boot firmware state is incomplete.")
    if values["active_slot"] not in ("A", "B") or values["previous_slot"] not in ("A", "B"):
        raise SlotError("U-Boot firmware slots are invalid.")
    if values["upgrade_available"] not in ("0", "1"):
        raise SlotError("U-Boot trial state is invalid.")
    if values["rollback_from"] not in ("A", "B", "none"):
        raise SlotError("U-Boot rollback state is invalid.")
    try:
        bootcount = int(values["bootcount"])
        bootlimit = int(values["bootlimit"])
    except ValueError as exc:
        raise SlotError("U-Boot boot counter is invalid.") from exc
    if bootcount < 0 or not 1 <= bootlimit <= 10:
        raise SlotError("U-Boot boot counter is invalid.")
    return values


def _release_metadata(path: Path) -> Dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_RELEASE_BYTES:
            raise SlotError("Other-slot release metadata is unsafe.")
        value = json.loads(path.read_text(encoding="utf-8"))
    except SlotError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise SlotError("Other-slot release metadata is unavailable.") from exc
    legacy = {"schema", "version", "hardware_revision"}
    expected = {
        "schema",
        "version",
        "hardware_revision",
        "persistent_schema",
        "persistent_reader_schema",
        "minimum_rollback_reader_schema",
    }
    if not isinstance(value, dict) or set(value) not in (legacy, expected):
        raise SlotError("Other-slot release metadata is invalid.")
    version = value.get("version")
    if (
        value.get("schema") != 1
        or not isinstance(version, str)
        or not _VERSION_RE.fullmatch(version)
    ):
        raise SlotError("Other-slot release metadata is invalid.")
    if value.get("hardware_revision") != HARDWARE_REVISION:
        raise SlotError("The other firmware is not compatible with this radio.")
    if set(value) == legacy:
        # Phase-9 slots predate explicit schema capabilities. They implement
        # schema 1 and remain the immediately previous rollback generation.
        value.update(
            persistent_schema=1,
            persistent_reader_schema=1,
            minimum_rollback_reader_schema=1,
        )
    schema = value.get("persistent_schema")
    reader = value.get("persistent_reader_schema")
    rollback_reader = value.get("minimum_rollback_reader_schema")
    if (
        not isinstance(schema, int)
        or not isinstance(reader, int)
        or not isinstance(rollback_reader, int)
        or schema < 1
        or reader < 1
        or rollback_reader < 0
        or rollback_reader > schema
    ):
        raise SlotError("Other-slot release metadata is invalid.")
    return value


def _inspect_other_slot_unlocked() -> Dict[str, Any]:
    running, other, device, _selection = firmware_installer._read_cmdline()
    OTHER_MOUNT.mkdir(parents=True, exist_ok=True)
    mounted = False
    try:
        result = _run(
            [MOUNT, "-t", "ext4", "-o", "ro,noload,nodev,nosuid,noexec", device, str(OTHER_MOUNT)]
        )
        if result.returncode != 0:
            raise SlotError("The other firmware slot could not be inspected.")
        mounted = True
        release = _release_metadata(OTHER_MOUNT / RELEASE_PATH)
        try:
            compatibility = persistent_config.compatibility_state()
        except ValueError as exc:
            raise SlotError(str(exc)) from exc
        if release["persistent_reader_schema"] < compatibility["minimum_reader_schema"]:
            raise SlotError("The other firmware cannot read the current persistent configuration.")
        return {
            "running_slot": running,
            "other_slot": other,
            "version": release["version"],
            "compatible": True,
            "persistent_reader_schema": release["persistent_reader_schema"],
        }
    finally:
        if mounted:
            result = _run([UMOUNT, str(OTHER_MOUNT)])
            if result.returncode != 0:
                raise SlotError("The other firmware slot could not be unmounted safely.")


def inspect_other_slot() -> Dict[str, Any]:
    """Serialize a fixed read-only mount and return safe other-slot metadata."""
    SLOT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with SLOT_LOCK.open("a", encoding="ascii") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SlotError("The other firmware slot is currently being inspected.") from None
        return _inspect_other_slot_unlocked()


def _version_tuple(version: str) -> Tuple[int, int, int]:
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def switch_status() -> Dict[str, Any]:
    """Return bounded helper-authoritative eligibility and other-slot metadata."""
    result: Dict[str, Any] = {"switch_allowed": False}
    if firmware_installer.installation_active():
        result["switch_reason"] = "Firmware installation is active."
        return result
    try:
        environment = _environment()
        if environment["upgrade_available"] == "1":
            raise SlotError("A firmware trial is already active.")
        metadata = inspect_other_slot()
        if environment["active_slot"] != metadata["running_slot"]:
            raise SlotError("U-Boot selection does not match the running firmware.")
        version = str(metadata["version"])
        result.update(
            other_slot=metadata["other_slot"],
            other_version=version,
            other_compatible=True,
            running_slot=metadata["running_slot"],
        )
        result["switch_allowed"] = True
        if _version_tuple(version) < _version_tuple(__version__):
            result["switch_label"] = f"Roll back to {version}"
        else:
            result["switch_label"] = f"Switch to other firmware ({version})"
    except (firmware_installer.FirmwareError, SlotError) as exc:
        result["switch_reason"] = str(exc)[:160]
    return result


def _set_environment(values: Dict[str, str]) -> None:
    temporary = ""
    try:
        fd, temporary = tempfile.mkstemp(prefix="firmware-switch-env-", dir=str(ENV_TMP_DIR))
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            for key in sorted(values):
                value = values[key]
                if not key.replace("_", "").isalnum() or not value.isalnum():
                    raise SlotError("Refusing an unsafe U-Boot environment value.")
                handle.write(f"{key}={value}\n")
            handle.flush()
            os.fsync(handle.fileno())
        result = _run([FW_SETENV, "-s", temporary], timeout=15)
        if result.returncode != 0:
            raise SlotError("Could not prepare the firmware trial.")
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def prepare_rollback() -> Tuple[bool, str]:
    """Select the validated other slot as a trial and reboot after verified commit."""
    acquired = False
    try:
        firmware_installer._acquire_lock()
        acquired = True
        environment = _environment()
        if environment["upgrade_available"] == "1":
            raise SlotError("A firmware trial is already active.")
        metadata = inspect_other_slot()
        running = str(metadata["running_slot"])
        other = str(metadata["other_slot"])
        if environment["active_slot"] != running:
            raise SlotError("U-Boot selection does not match the running firmware.")
        desired = {
            "active_slot": other,
            "bootcount": "0",
            "previous_slot": running,
            "rollback_from": "none",
            "upgrade_available": "1",
        }
        _set_environment(desired)
        committed = _environment()
        if any(committed.get(key) != value for key, value in desired.items()):
            raise SlotError("The firmware trial state could not be verified.")
        firmware_installer._append_history(
            {
                "version": metadata["version"],
                "target_slot": other,
                "started_at": int(time.time()),
            },
            "manual_switch_prepared",
        )
        os.sync()
        result = _run([REBOOT], timeout=10)
        if result.returncode != 0:
            raise SlotError("The firmware trial was prepared, but reboot failed.")
        return True, "Switching to the other firmware for a trial boot."
    except (firmware_installer.FirmwareError, SlotError, OSError) as exc:
        logger.error("prepare rollback: %s", exc)
        return False, str(exc)
    finally:
        if acquired:
            firmware_installer._release_lock()
