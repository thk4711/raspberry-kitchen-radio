"""Bounded, privacy-safe crash and reboot summary for the appliance.

Routine logs remain volatile.  This module persists only fixed-schema operational
facts needed to explain recovery: boot identity/slot, clean versus unclean reboot,
radio restart counters, and the last firmware-health outcome.  It deliberately
accepts no arbitrary message text, URLs, credentials, or user metadata.
"""

import argparse
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

SUMMARY_PATH = Path("/data/operations/summary.json")
LOCK_PATH = Path("/run/operational-summary.lock")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
CMDLINE_PATH = Path("/proc/cmdline")

SCHEMA_VERSION = 1
MAX_EVENTS = 32
MAX_SUMMARY_BYTES = 16 * 1024
RESTART_REASONS = frozenset({"process_exit", "heartbeat_timeout"})
HEALTH_RESULTS = frozenset({"accepted", "trial_failed", "automatic_rollback"})


def _now() -> int:
    return int(time.time())


def _read_boot_id() -> str:
    try:
        value = BOOT_ID_PATH.read_text(encoding="ascii").strip().lower()
    except OSError:
        return "unknown"
    if len(value) == 36 and all(character in "0123456789abcdef-" for character in value):
        return value
    return "unknown"


def _running_slot() -> str:
    try:
        fields = CMDLINE_PATH.read_text(encoding="ascii").split()
    except OSError:
        return "unknown"
    for field in fields:
        if field.startswith("radio.slot="):
            slot = field.split("=", 1)[1]
            return slot if slot in ("A", "B") else "unknown"
    return "unknown"


def _empty_summary() -> Dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "events": []}


def _load() -> Dict[str, Any]:
    try:
        if SUMMARY_PATH.stat().st_size > MAX_SUMMARY_BYTES:
            return _empty_summary()
        value = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_summary()
    if not isinstance(value, dict) or value.get("schema") != SCHEMA_VERSION:
        return _empty_summary()
    events = value.get("events")
    if not isinstance(events, list):
        value["events"] = []
    else:
        value["events"] = [event for event in events if isinstance(event, dict)][-MAX_EVENTS:]
    return value


def _atomic_write(value: Dict[str, Any]) -> None:
    value["schema"] = SCHEMA_VERSION
    value["events"] = value.get("events", [])[-MAX_EVENTS:]
    payload = json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n"
    while len(payload.encode("utf-8")) > MAX_SUMMARY_BYTES and value["events"]:
        value["events"].pop(0)
        payload = json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise OSError("operational summary exceeds its fixed size bound")

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        fd, temporary = tempfile.mkstemp(dir=str(SUMMARY_PATH.parent), prefix=".summary.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, SUMMARY_PATH)
        temporary = ""
        directory_fd = os.open(str(SUMMARY_PATH.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _event(summary: Dict[str, Any], kind: str, **fields: Any) -> None:
    event = {"at": _now(), "type": kind}
    event.update(fields)
    summary.setdefault("events", []).append(event)
    summary["events"] = summary["events"][-MAX_EVENTS:]


def _update(operation: Any) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a", encoding="ascii") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        summary = _load()
        operation(summary)
        _atomic_write(summary)


def record_boot(boot_id: Optional[str] = None, slot: Optional[str] = None) -> None:
    """Start a boot record and classify the preceding shutdown."""
    boot_id = boot_id or _read_boot_id()
    slot = slot or _running_slot()

    def apply(summary: Dict[str, Any]) -> None:
        previous = summary.get("current_boot")
        # SysV service restarts must not manufacture another boot record for the
        # same kernel boot ID or reset its counters.
        if isinstance(previous, dict) and previous.get("boot_id") == boot_id:
            return
        if not isinstance(previous, dict):
            reboot_reason = "initial"
        elif previous.get("clean_shutdown") is True:
            reboot_reason = "clean"
        else:
            reboot_reason = "unclean"
        current = {
            "boot_id": boot_id,
            "clean_shutdown": False,
            "radio_restarts": {"heartbeat_timeout": 0, "process_exit": 0},
            "reboot_reason": reboot_reason,
            "slot": slot,
            "started_at": _now(),
        }
        summary["current_boot"] = current
        _event(summary, "boot", boot_id=boot_id, reason=reboot_reason, slot=slot)

    _update(apply)


def record_clean_shutdown() -> None:
    def apply(summary: Dict[str, Any]) -> None:
        current = summary.get("current_boot")
        if not isinstance(current, dict) or current.get("clean_shutdown") is True:
            return
        current["clean_shutdown"] = True
        current["shutdown_at"] = _now()
        _event(summary, "shutdown", boot_id=current.get("boot_id", "unknown"), reason="clean")

    _update(apply)


def record_restart(reason: str) -> None:
    if reason not in RESTART_REASONS:
        raise ValueError("unsupported restart reason")

    def apply(summary: Dict[str, Any]) -> None:
        current = summary.get("current_boot")
        if not isinstance(current, dict):
            return
        counters = current.setdefault("radio_restarts", {})
        counters[reason] = min(int(counters.get(reason, 0)) + 1, 999999)
        _event(summary, "radio_restart", boot_id=current.get("boot_id", "unknown"), reason=reason)

    _update(apply)


def record_health(result: str, slot: str) -> None:
    if result not in HEALTH_RESULTS or slot not in ("A", "B"):
        raise ValueError("unsupported firmware-health outcome")

    def apply(summary: Dict[str, Any]) -> None:
        summary["last_health"] = {"at": _now(), "result": result, "slot": slot}
        _event(summary, "firmware_health", result=result, slot=slot)

    _update(apply)


def best_effort_health(result: str, slot: str) -> None:
    """Record health without ever affecting acceptance or rollback behavior."""
    try:
        record_health(result, slot)
    except (OSError, ValueError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("boot")
    subparsers.add_parser("clean-shutdown")
    restart = subparsers.add_parser("restart")
    restart.add_argument("--reason", required=True, choices=sorted(RESTART_REASONS))
    health = subparsers.add_parser("health")
    health.add_argument("--result", required=True, choices=sorted(HEALTH_RESULTS))
    health.add_argument("--slot", required=True, choices=("A", "B"))
    args = parser.parse_args()
    try:
        if args.operation == "boot":
            record_boot()
        elif args.operation == "clean-shutdown":
            record_clean_shutdown()
        elif args.operation == "restart":
            record_restart(args.reason)
        else:
            record_health(args.result, args.slot)
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
