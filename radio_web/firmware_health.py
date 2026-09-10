"""Boot-health confirmation and automatic-rollback result reconciliation.

This module runs as root late in BusyBox startup.  It accepts a trial slot only
after local appliance services are healthy; external network services are
deliberately excluded.  U-Boot owns attempt counting and slot fallback.
"""

import argparse
import fcntl
import http.client
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import firmware_installer, persistent_config, sources_store

logger = logging.getLogger("radio_web.firmware_health")

FW_PRINTENV = "/usr/bin/fw_printenv"
FW_SETENV = "/usr/bin/fw_setenv"
REBOOT = "/sbin/reboot"
CMDLINE_PATH = Path("/proc/cmdline")
MOUNTS_PATH = Path("/proc/mounts")
HEARTBEAT_PATH = Path("/tmp/radio.alive")
MPD_PID_PATH = Path("/run/mpd/mpd.pid")
SCHEMA_PATH = Path("/data/radio/schema-version")
LOCK_PATH = Path("/run/firmware-health.lock")
ENV_TMP_DIR = Path("/run")
DEADLINE_SECONDS = 120
POLL_SECONDS = 3.0
HEARTBEAT_MAX_AGE = 30
ENV_KEYS = (
    "active_slot",
    "previous_slot",
    "upgrade_available",
    "bootcount",
    "bootlimit",
    "rollback_from",
)


class HealthError(Exception):
    """A bounded, safely persistable boot-health failure."""


def _read_environment() -> Dict[str, str]:
    try:
        result = subprocess.run(
            [FW_PRINTENV, *ENV_KEYS],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HealthError("U-Boot environment unavailable") from exc
    # Missing optional rollback_from may make fw_printenv return non-zero.  Parse
    # all valid output, then validate the required fields below.
    values: Dict[str, str] = {}
    for raw in result.stdout.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key in ENV_KEYS and len(value) <= 64:
            values[key] = value
    for key in ENV_KEYS[:5]:
        if key not in values:
            raise HealthError("U-Boot trial state is incomplete")
    if values["active_slot"] not in ("A", "B"):
        raise HealthError("U-Boot active slot is invalid")
    if values["previous_slot"] not in ("A", "B"):
        raise HealthError("U-Boot previous slot is invalid")
    if values["upgrade_available"] not in ("0", "1"):
        raise HealthError("U-Boot trial flag is invalid")
    try:
        bootcount = int(values["bootcount"])
        bootlimit = int(values["bootlimit"])
    except ValueError as exc:
        raise HealthError("U-Boot boot counter is invalid") from exc
    if bootcount < 0 or bootlimit < 1 or bootlimit > 10:
        raise HealthError("U-Boot boot counter is invalid")
    return values


def _set_environment(values: Dict[str, str]) -> None:
    allowed = {"upgrade_available", "bootcount", "rollback_from"}
    if not values or not set(values).issubset(allowed):
        raise HealthError("Refusing an unsafe environment update")
    temporary = ""
    try:
        fd, temporary = tempfile.mkstemp(prefix="firmware-health-env-", dir=str(ENV_TMP_DIR))
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            for key in sorted(values):
                value = values[key]
                if not value.isalnum():
                    raise HealthError("Refusing an unsafe environment value")
                handle.write(f"{key}={value}\n")
            handle.flush()
            os.fsync(handle.fileno())
        result = subprocess.run(
            [FW_SETENV, "-s", temporary],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            raise HealthError("Could not commit U-Boot trial state")
    except subprocess.TimeoutExpired as exc:
        raise HealthError("U-Boot environment update timed out") from exc
    except OSError as exc:
        raise HealthError("Could not commit U-Boot trial state") from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _running_slot() -> str:
    try:
        arguments = CMDLINE_PATH.read_text(encoding="utf-8").split()
    except OSError as exc:
        raise HealthError("Kernel slot information unavailable") from exc
    slots = [item.split("=", 1)[1] for item in arguments if item.startswith("radio.slot=")]
    roots = [item.split("=", 1)[1] for item in arguments if item.startswith("root=")]
    if len(slots) != 1 or len(roots) != 1 or slots[0] not in ("A", "B"):
        raise HealthError("Kernel slot information is invalid")
    expected = "/dev/mmcblk0p2" if slots[0] == "A" else "/dev/mmcblk0p3"
    if roots[0] != expected:
        raise HealthError("Kernel root device does not match its slot")
    return slots[0]


def _data_read_write() -> bool:
    try:
        lines = MOUNTS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "/data":
            return "rw" in fields[3].split(",")
    return False


def _schema_current() -> bool:
    try:
        writer_schema = int(SCHEMA_PATH.read_text(encoding="ascii").strip())
        return writer_schema >= persistent_config.CURRENT_SCHEMA and (
            persistent_config.can_read_persistent_state(persistent_config.CURRENT_SCHEMA)
        )
    except (OSError, ValueError):
        return False


def _web_healthy() -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2)
    try:
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        return response.status == 200 and response.read(16).strip() == b"ok"
    except OSError:
        return False
    finally:
        connection.close()


def _heartbeat_fresh(now: Optional[float] = None) -> bool:
    try:
        age = (time.time() if now is None else now) - HEARTBEAT_PATH.stat().st_mtime
    except OSError:
        return False
    return 0 <= age <= HEARTBEAT_MAX_AGE


def _pid_running(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        return pid > 1
    except (OSError, ValueError):
        return False


def health_failures(environment: Dict[str, str]) -> List[str]:
    """Return bounded local health failures for the current trial boot."""
    failures: List[str] = []
    try:
        slot = _running_slot()
        if environment.get("active_slot") != slot:
            failures.append("running slot does not match U-Boot selection")
    except HealthError as exc:
        failures.append(str(exc))
    if not _data_read_write():
        failures.append("persistent data is not mounted read-write")
    if not _schema_current():
        failures.append("persistent schema migration is incomplete")
    if not _web_healthy():
        failures.append("local web health check is unavailable")
    if not _heartbeat_fresh():
        failures.append("radio heartbeat is missing or stale")
    if sources_store.load_sources().get("internet_radio", True) and not _pid_running(MPD_PID_PATH):
        failures.append("enabled MPD service is not running")
    return failures


def _history() -> List[Dict[str, object]]:
    value = firmware_installer._read_bounded_json(firmware_installer.HISTORY_PATH, 64 * 1024)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)][
        -firmware_installer.MAX_HISTORY_ENTRIES :
    ]


def _record_result(target_slot: str, result: str, reason: str = "") -> bool:
    history = _history()
    for entry in reversed(history):
        if entry.get("target_slot") == target_slot and entry.get("result") in {
            "installed",
            "manual_switch_prepared",
            "trial_failed",
        }:
            entry["result"] = result[:64]
            entry["finished_at"] = int(time.time())
            if reason:
                entry["failure_reason"] = reason[:160]
            else:
                entry.pop("failure_reason", None)
            firmware_installer._atomic_json(
                firmware_installer.HISTORY_PATH,
                history,
                0o600,
            )
            message = {
                "accepted": "The new firmware passed health checks and is now active.",
                "trial_failed": "The trial firmware failed health checks; automatic recovery continues.",
                "automatic_rollback": "The device recovered using the previous firmware.",
            }.get(result, result)
            firmware_installer.firmware_tracking.update(
                result,
                message=message,
                result=result,
                failure_reason=reason or None,
                target_slot=target_slot,
            )
            return True
    return False


def reconcile_rollback(environment: Optional[Dict[str, str]] = None) -> bool:
    """Record U-Boot fallback once, then clear its one-shot marker."""
    values = _read_environment() if environment is None else environment
    failed_slot = values.get("rollback_from", "")
    if values["upgrade_available"] != "0" or failed_slot not in ("A", "B"):
        return False
    running = _running_slot()
    if running == failed_slot:
        return False
    if not _record_result(failed_slot, "automatic_rollback", "trial boot limit exceeded"):
        raise HealthError("Automatic rollback history is unavailable")
    _set_environment({"rollback_from": "none"})
    return True


def reconcile_acceptance(environment: Optional[Dict[str, str]] = None) -> bool:
    """Finish history after power loss following a successful mark-good commit."""
    values = _read_environment() if environment is None else environment
    if values["upgrade_available"] != "0" or values.get("rollback_from", "none") != "none":
        return False
    recorded = _record_result(_running_slot(), "accepted")
    if recorded:
        persistent_config.mark_migration_accepted()
    return recorded


def accept_trial(environment: Dict[str, str]) -> None:
    slot = _running_slot()
    current = _read_environment()
    if current["upgrade_available"] != "1" or current["active_slot"] != slot:
        raise HealthError("Trial state changed before acceptance")
    _set_environment({"bootcount": "0", "upgrade_available": "0"})
    _record_result(slot, "accepted")
    persistent_config.mark_migration_accepted()


def run_trial() -> int:
    """Evaluate one trial until healthy or the bounded deadline expires."""
    environment = _read_environment()
    rolled_back = reconcile_rollback(environment)
    if environment["upgrade_available"] != "1":
        if not rolled_back:
            reconcile_acceptance(environment)
        return 0
    firmware_installer.firmware_tracking.update(
        "checking_health",
        message="The new firmware is running and completing local health checks.",
        target_slot=environment["active_slot"],
        percent=100,
    )
    deadline = time.monotonic() + DEADLINE_SECONDS
    failures: List[str] = ["health checks have not completed"]
    while time.monotonic() < deadline:
        failures = health_failures(environment)
        if not failures:
            accept_trial(environment)
            logger.info("accepted healthy firmware trial in slot %s", environment["active_slot"])
            return 0
        time.sleep(POLL_SECONDS)
    reason = failures[0][:160]
    _record_result(environment["active_slot"], "trial_failed", reason)
    logger.error("firmware trial failed: %s", reason)
    os.sync()
    subprocess.run([REBOOT], timeout=10, check=False)
    return 1


def _locked_run() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "a", encoding="ascii") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return run_trial()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("run", "reconcile"))
    args = parser.parse_args()
    try:
        if args.operation == "reconcile":
            environment = _read_environment()
            if not reconcile_rollback(environment):
                reconcile_acceptance(environment)
            return 0
        return _locked_run()
    except HealthError as exc:
        logger.error("firmware health: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
