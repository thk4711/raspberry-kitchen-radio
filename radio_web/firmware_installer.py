"""Root-only firmware inspection, queueing, installation, status, and history.

The web process may ask the privileged helper to perform one of three fixed
operations, but it never supplies a pathname, slot, device, or command.  This
module owns those values and derives the inactive slot from the trusted kernel
command line immediately before SWUpdate is run.
"""

import argparse
import hashlib
import json
import logging
import os
import select
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from lib import __version__

from . import firmware_tracking

logger = logging.getLogger("radio_web.firmware_installer")

DATA_MOUNT = Path("/data")
UPLOAD_PATH = DATA_MOUNT / "update" / "upload" / "firmware.swu.ready"
UPLOAD_METADATA_PATH = DATA_MOUNT / "update" / "upload" / "firmware.swu.json"
QUEUE_PATH = DATA_MOUNT / "update" / "queue" / "firmware.swu"
HISTORY_PATH = DATA_MOUNT / "update" / "history.json"
RUNTIME_DIR = Path("/run/firmware-update")
SWUPDATE_RUNTIME_DIR = Path("/run/swupdate")
STATUS_PATH = RUNTIME_DIR / "status.json"
SWUPDATE_LOG_PATH = RUNTIME_DIR / "swupdate.log"
LOCK_PATH = RUNTIME_DIR / "install.lock"
CMDLINE_PATH = Path("/proc/cmdline")
MOUNTS_PATH = Path("/proc/mounts")

SWUPDATE = "/usr/bin/swupdate"
INACTIVE_INSTALLER = "/usr/sbin/radio-swupdate-inactive"
PROGRESS_BRIDGE = "/usr/bin/radio-swupdate-progress"
PYTHON = "/usr/bin/python3"
REBOOT = "/sbin/reboot"

WEB_UID = 601
WEB_GID = 601
MAX_FIRMWARE_BYTES = 768 * 1024 * 1024
MAX_HISTORY_ENTRIES = 20

_SLOT_INFO = {
    "A": ("B", "/dev/mmcblk0p3", "stable,slot-b"),
    "B": ("A", "/dev/mmcblk0p2", "stable,slot-a"),
}


def _atomic_json(path: Path, value: Any, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = ""
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
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


def write_status(state: str, **values: Any) -> None:
    """Atomically publish bounded, non-secret installer status."""
    status: Dict[str, Any] = {
        "schema": 1,
        "state": state,
        "updated_at": int(time.time()),
    }
    allowed = {
        "version",
        "sha256",
        "active_slot",
        "target_slot",
        "started_at",
        "finished_at",
        "step",
        "steps",
        "percent",
        "image",
        "result",
    }
    for key in allowed:
        if key in values and values[key] is not None:
            status[key] = values[key]
    _atomic_json(STATUS_PATH, status, 0o644)
    firmware_tracking.update(state, **values)


def _append_history(metadata: Dict[str, Any], result: str) -> None:
    try:
        value = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = []
    if not isinstance(value, list):
        value = []
    entry = {
        "version": str(metadata.get("version", "unknown"))[:64],
        "target_slot": str(metadata.get("target_slot", "unknown"))[:8],
        "sha256": str(metadata.get("sha256", ""))[:64],
        "started_at": int(metadata.get("started_at", time.time())),
        "finished_at": int(time.time()),
        "result": result[:64],
    }
    value.append(entry)
    _atomic_json(HISTORY_PATH, value[-MAX_HISTORY_ENTRIES:], 0o600)


def _read_bounded_json(path: Path, maximum: int) -> Any:
    try:
        if path.stat().st_size > maximum:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def installation_active() -> bool:
    """Return whether the runtime lock belongs to a live installation worker."""
    try:
        pid = int(LOCK_PATH.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    return _pid_is_running(pid)


def firmware_status() -> Dict[str, Any]:
    """Return bounded, non-secret state suitable for the authenticated web UI."""
    state: Dict[str, Any] = {
        "schema": 1,
        "running_version": __version__,
        "running_slot": "unknown",
        "other_slot": "unknown",
        "other_version": "unknown",
        "other_health": "unknown",
        "active": installation_active(),
        "phase": "idle",
    }
    try:
        active, target, _device, _selection = _read_cmdline()
        state["running_slot"] = active
        state["other_slot"] = target
    except FirmwareError:
        pass

    # Imported lazily to keep the installer and slot inspector dependency acyclic.
    from . import firmware_slots

    state.update(firmware_slots.switch_status())

    runtime = _read_bounded_json(STATUS_PATH, 16 * 1024)
    if isinstance(runtime, dict):
        allowed = {
            "state",
            "version",
            "sha256",
            "active_slot",
            "target_slot",
            "started_at",
            "finished_at",
            "step",
            "steps",
            "percent",
            "image",
            "result",
            "updated_at",
        }
        safe_runtime = {key: runtime[key] for key in allowed if key in runtime}
        state["installation"] = safe_runtime
        state["phase"] = str(runtime.get("state", "idle"))[:32]

    history = _read_bounded_json(HISTORY_PATH, 64 * 1024)
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        latest = history[-1]
        allowed_history = {
            "version",
            "target_slot",
            "sha256",
            "started_at",
            "finished_at",
            "result",
            "failure_reason",
        }
        state["last_update"] = {key: latest[key] for key in allowed_history if key in latest}
        result = latest.get("result")
        if result in {"accepted", "automatic_rollback", "trial_failed"}:
            state["phase"] = result
            state["other_health"] = {
                "accepted": "previous firmware",
                "automatic_rollback": "failed trial",
                "trial_failed": "trial failed; retry pending",
            }[str(result)]

    if UPLOAD_PATH.exists():
        staged: Dict[str, Any] = {}
        try:
            info = _validate_staged_stat(UPLOAD_PATH)
            archive = _inspect_archive(UPLOAD_PATH)
            staged.update(
                version=archive["version"],
                size=info.st_size,
                sha256=_sha256(UPLOAD_PATH),
                filename="firmware.swu",
            )
            metadata = _read_bounded_json(UPLOAD_METADATA_PATH, 4096)
            if isinstance(metadata, dict):
                filename = metadata.get("filename")
                if isinstance(filename, str) and 0 < len(filename) <= 128:
                    staged["filename"] = filename
            state["staged"] = staged
            if state["phase"] == "idle":
                state["phase"] = "staged"
        except (FirmwareError, OSError) as exc:
            state["staged_error"] = str(exc)[:160]
    return state


def _read_cmdline() -> Tuple[str, str, str, str]:
    try:
        arguments = CMDLINE_PATH.read_text(encoding="utf-8").split()
    except OSError as exc:
        raise FirmwareError("The active firmware slot cannot be determined.") from exc
    slots = [item.split("=", 1)[1] for item in arguments if item.startswith("radio.slot=")]
    roots = [item.split("=", 1)[1] for item in arguments if item.startswith("root=")]
    if len(slots) != 1 or slots[0] not in _SLOT_INFO:
        raise FirmwareError("The kernel command line must identify exactly one firmware slot.")
    active = slots[0]
    target, expected_target_device, selection = _SLOT_INFO[active]
    expected_root = "/dev/mmcblk0p2" if active == "A" else "/dev/mmcblk0p3"
    if len(roots) != 1 or roots[0] != expected_root:
        raise FirmwareError("The active root filesystem does not match the firmware slot.")
    return active, target, expected_target_device, selection


def _data_is_mounted() -> bool:
    try:
        for line in MOUNTS_PATH.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[1] == str(DATA_MOUNT) and fields[2] == "ext4":
                return True
    except OSError:
        pass
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Archive parsing is isolated because it is a security boundary independent of
# queue ownership, worker lifecycle, and status publication. Keep these aliases
# for the established installer API and tests.
from .firmware_archive import FirmwareError
from .firmware_archive import inspect_archive as _inspect_archive


def _validate_staged_stat(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise FirmwareError("No firmware package is staged.") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise FirmwareError("The staged firmware file is not a safe regular file.")
    if info.st_uid != WEB_UID or info.st_gid != WEB_GID:
        raise FirmwareError("The staged firmware file has unexpected ownership.")
    if info.st_size <= 0 or info.st_size > MAX_FIRMWARE_BYTES:
        raise FirmwareError("The staged firmware file has an invalid size.")
    return info


def inspect_firmware() -> Tuple[bool, str]:
    try:
        info = _validate_staged_stat(UPLOAD_PATH)
        metadata = _inspect_archive(UPLOAD_PATH)
        digest = _sha256(UPLOAD_PATH)
    except (FirmwareError, OSError) as exc:
        return False, str(exc)
    return (
        True,
        f"Firmware {metadata['version']} is staged ({info.st_size} bytes, SHA-256 {digest}).",
    )


def _pid_is_running(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _acquire_lock() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(str(LOCK_PATH), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                pid = int(LOCK_PATH.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                pid = -1
            if _pid_is_running(pid):
                raise FirmwareError("Another firmware update is already active.") from None
            try:
                LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        return
    raise FirmwareError("Another firmware update is already active.")


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def _claim_staged() -> None:
    if not _data_is_mounted():
        raise FirmwareError("The persistent data partition is not mounted.")
    before = _validate_staged_stat(UPLOAD_PATH)
    if QUEUE_PATH.exists() or QUEUE_PATH.is_symlink():
        raise FirmwareError("A queued firmware package must be cancelled first.")
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.rename(UPLOAD_PATH, QUEUE_PATH)
    after = QUEUE_PATH.lstat()
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        try:
            QUEUE_PATH.unlink()
        finally:
            raise FirmwareError("The staged firmware changed while it was being queued.")
    os.chown(QUEUE_PATH, 0, 0)
    os.chmod(QUEUE_PATH, 0o600)
    try:
        UPLOAD_METADATA_PATH.unlink()
    except FileNotFoundError:
        pass


def install_firmware() -> Tuple[bool, str]:
    try:
        _acquire_lock()
        try:
            _claim_staged()
            write_status("queued", started_at=int(time.time()))
            worker = subprocess.Popen(  # noqa: S603 - fixed interpreter/module/operation
                [PYTHON, "-m", "radio_web.firmware_installer", "worker"],
                cwd="/opt/raspberry-kitchen-radio",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            LOCK_PATH.write_text(f"{worker.pid}\n", encoding="ascii")
        except Exception:
            _release_lock()
            raise
    except (FirmwareError, OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return True, "Firmware installation started in the background."


def cancel_staged_firmware() -> Tuple[bool, str]:
    if LOCK_PATH.exists():
        try:
            pid = int(LOCK_PATH.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = -1
        if _pid_is_running(pid):
            return False, "A firmware installation is active and cannot be cancelled."
        _release_lock()
    removed = False
    for path in (UPLOAD_PATH, UPLOAD_METADATA_PATH, QUEUE_PATH):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            pass
        except OSError as exc:
            return False, f"The staged firmware could not be removed: {exc}"
    return True, "Staged firmware removed." if removed else "No firmware package was staged."


def _progress_values(line: str) -> Optional[Dict[str, Any]]:
    fields = line.rstrip("\n").split("\t")
    if len(fields) != 7 or fields[0] != "PROGRESS":
        return None
    try:
        status, step, steps, percent = (int(value) for value in fields[1:5])
    except ValueError:
        return None
    image = os.path.basename(fields[5])[:128]
    return {
        "swupdate_status": status,
        "step": max(0, step),
        "steps": max(0, steps),
        "percent": min(100, max(0, percent)),
        "image": image,
        "source": fields[6][:32],
    }


def _run_worker() -> int:
    metadata: Dict[str, Any] = {"started_at": int(time.time())}
    progress = None
    installer = None
    try:
        LOCK_PATH.write_text(f"{os.getpid()}\n", encoding="ascii")
        if not _data_is_mounted():
            raise FirmwareError("The persistent data partition is not mounted.")
        # SWUpdate needs its IPC socket directory during check mode, before the
        # inactive-slot wrapper gets a chance to create it.
        SWUPDATE_RUNTIME_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
        active, target, expected_device, selection = _read_cmdline()
        write_status("inspecting", active_slot=active, target_slot=target, **metadata)
        package = QUEUE_PATH.lstat()
        if not stat.S_ISREG(package.st_mode) or package.st_nlink != 1:
            raise FirmwareError("The queued firmware is not a safe regular file.")
        if package.st_uid != 0 or package.st_gid != 0 or stat.S_IMODE(package.st_mode) != 0o600:
            raise FirmwareError("The queued firmware is not secured for installation.")
        archive = _inspect_archive(QUEUE_PATH, expected_device)
        metadata.update(
            version=archive["version"],
            sha256=_sha256(QUEUE_PATH),
            active_slot=active,
            target_slot=target,
        )
        write_status("validating", **metadata)
        checked = subprocess.run(  # noqa: S603 - fixed executable and fixed path
            [SWUPDATE, "-c", "-i", str(QUEUE_PATH), "-e", selection],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=900,
            check=False,
        )
        if checked.returncode != 0:
            raise FirmwareError("SWUpdate rejected the firmware package before installation.")

        write_status("installing", step=0, steps=0, percent=0, **metadata)
        # Start SWUpdate first so it creates its progress socket before the
        # bridge attempts to connect. Starting the bridge first races socket
        # creation and can make a successful update look like a failure.
        SWUPDATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_handle = SWUPDATE_LOG_PATH.open("w", encoding="utf-8")
        installer = subprocess.Popen(  # noqa: S603 - fixed inactive-slot installer
            [INACTIVE_INSTALLER],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        progress = subprocess.Popen(  # noqa: S603 - fixed progress bridge
            [PROGRESS_BRIDGE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert progress.stdout is not None
        progress_fd = progress.stdout.fileno()
        os.set_blocking(progress_fd, False)
        buffered = b""
        terminal_status = None
        deadline = time.monotonic() + 1800
        progress_open = True
        while time.monotonic() < deadline and installer.poll() is None:
            readable, _writable, _errors = select.select([progress_fd], [], [], 1.0)
            if not readable:
                continue
            chunk = os.read(progress_fd, 4096)
            if not chunk:
                progress_open = False
                break
            buffered += chunk
            while b"\n" in buffered:
                raw_line, buffered = buffered.split(b"\n", 1)
                try:
                    line = raw_line.decode("utf-8") + "\n"
                except UnicodeDecodeError:
                    continue
                values = _progress_values(line)
                if values is None:
                    continue
                terminal_status = values.pop("swupdate_status")
                write_status("installing", **metadata, **values)
                if terminal_status in (3, 4):
                    break
            if terminal_status in (3, 4):  # SUCCESS, FAILURE in SWUpdate 2025.05
                break
        try:
            returncode = installer.wait(timeout=1800)
        except subprocess.TimeoutExpired as exc:
            raise FirmwareError(
                "SWUpdate did not terminate after its progress stream ended."
            ) from exc
        log_handle.close()
        metadata.update(
            installer_returncode=returncode,
            progress_returncode=progress.poll(),
            progress_open=progress_open,
        )
        write_status("installing", **metadata)
        # The installer exit status is authoritative. The progress bridge is
        # telemetry and may miss or misreport the terminal message during
        # SWUpdate shutdown.
        if returncode != 0:
            raise FirmwareError("SWUpdate did not complete the firmware installation successfully.")
        write_status("syncing", percent=100, **metadata)
        os.sync()
        write_status("preparing_trial_boot", percent=100, **metadata)
        finished = int(time.time())
        write_status("success", finished_at=finished, result="installed", **metadata)
        _append_history(metadata, "installed")
        firmware_tracking.update(
            "rebooting",
            message="Firmware installed. The radio is rebooting into the new trial firmware.",
            percent=100,
            **metadata,
        )
        os.sync()
        rebooted = subprocess.run([REBOOT], timeout=10, check=False)
        if rebooted.returncode != 0:
            write_status(
                "reboot_failed",
                finished_at=finished,
                result="Firmware installed, but automatic reboot failed.",
                **metadata,
            )
            return 1
        return 0
    except (FirmwareError, OSError, subprocess.SubprocessError) as exc:
        logger.error("firmware installation failed: %s", exc)
        finished = int(time.time())
        try:
            write_status("failed", finished_at=finished, result=str(exc)[:160], **metadata)
            _append_history(metadata, "failed")
        except OSError:
            logger.exception("could not record firmware installation failure")
        return 1
    finally:
        if installer is not None and installer.poll() is None:
            installer.send_signal(signal.SIGTERM)
        if progress is not None and progress.poll() is None:
            progress.send_signal(signal.SIGTERM)
        try:
            QUEUE_PATH.unlink()
        except FileNotFoundError:
            pass
        _release_lock()


def recover_interrupted_install() -> None:
    """Mark a stale runtime lock from an interrupted helper/worker as failed."""
    if not LOCK_PATH.exists():
        return
    try:
        pid = int(LOCK_PATH.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pid = -1
    if _pid_is_running(pid):
        return
    _release_lock()
    try:
        write_status("failed", finished_at=int(time.time()), result="installation interrupted")
    except OSError:
        logger.exception("could not publish interrupted firmware status")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("worker",))
    args = parser.parse_args()
    if args.operation == "worker":
        return _run_worker()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
