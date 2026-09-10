"""Host tests for the root-only firmware installer trust boundary."""

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from radio_web import firmware_installer as installer
from scripts import build_firmware_swu as swu

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "buildroot" / "external" / "board" / "radio" / "sw-description.in"


def _package(tmp_path: Path, manifest_text: str = "") -> Path:
    payload = tmp_path / "rootfs.ext4.gz"
    payload.write_bytes(b"compressed-rootfs")
    manifest = manifest_text or swu.render_manifest(TEMPLATE, "1.2.3", payload)
    description = tmp_path / "sw-description"
    description.write_text(manifest, encoding="utf-8")
    package = tmp_path / "firmware.swu"
    swu.write_cpio(package, [("sw-description", description), (swu.PAYLOAD_NAME, payload)])
    return package


def _paths(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    upload = data / "update" / "upload"
    queue = data / "update" / "queue"
    runtime = tmp_path / "run"
    swupdate_runtime = runtime / "swupdate"
    upload.mkdir(parents=True)
    queue.mkdir(parents=True)
    monkeypatch.setattr(installer, "DATA_MOUNT", data)
    monkeypatch.setattr(installer, "UPLOAD_PATH", upload / "firmware.swu.ready")
    monkeypatch.setattr(installer, "UPLOAD_METADATA_PATH", upload / "firmware.swu.json")
    monkeypatch.setattr(installer, "QUEUE_PATH", queue / "firmware.swu")
    monkeypatch.setattr(installer, "HISTORY_PATH", data / "update" / "history.json")
    monkeypatch.setattr(installer, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(installer, "SWUPDATE_RUNTIME_DIR", swupdate_runtime)
    monkeypatch.setattr(installer, "STATUS_PATH", runtime / "status.json")
    monkeypatch.setattr(installer, "SWUPDATE_LOG_PATH", runtime / "swupdate.log")
    monkeypatch.setattr(installer, "LOCK_PATH", runtime / "install.lock")
    monkeypatch.setattr(installer, "WEB_UID", os.getuid())
    monkeypatch.setattr(installer, "WEB_GID", os.getgid())


def test_archive_inspection_streams_and_returns_metadata(tmp_path):
    package = _package(tmp_path)
    result = installer._inspect_archive(package, "/dev/mmcblk0p3")
    assert result["version"] == "1.2.3"
    assert result["payload_size"] == len(b"compressed-rootfs")
    assert result["payload_sha256"] == hashlib.sha256(b"compressed-rootfs").hexdigest()
    assert result["persistent_schema"] == 1
    assert result["persistent_reader_schema"] == 1


def test_archive_rejects_unapproved_target(tmp_path):
    package = _package(tmp_path)
    members = swu.read_cpio(package)
    bad = members[0][1].decode().replace("/dev/mmcblk0p3", "/dev/mmcblk0p4")
    with pytest.raises(installer.FirmwareError, match="unapproved"):
        installer._inspect_archive(_package(tmp_path, bad), "/dev/mmcblk0p3")


def test_archive_rejects_payload_digest_mismatch(tmp_path):
    package = _package(tmp_path)
    members = swu.read_cpio(package)
    digest = hashlib.sha256(b"compressed-rootfs").hexdigest()
    bad = members[0][1].decode().replace(digest, "0" * 64)
    with pytest.raises(installer.FirmwareError, match="digest"):
        installer._inspect_archive(_package(tmp_path, bad))


def test_archive_rejects_irreversible_configuration_migration(tmp_path):
    package = _package(tmp_path)
    members = swu.read_cpio(package)
    bad = (
        members[0][1]
        .decode()
        .replace(
            "minimum_rollback_reader_schema = 1;",
            "minimum_rollback_reader_schema = 2;",
        )
        .replace("persistent_schema = 1;", "persistent_schema = 2;")
    )
    with pytest.raises(installer.FirmwareError, match="irreversible"):
        installer._inspect_archive(_package(tmp_path, bad))


def test_staged_validation_rejects_symlink(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    target = tmp_path / "target"
    target.write_bytes(b"firmware")
    installer.UPLOAD_PATH.symlink_to(target)
    with pytest.raises(installer.FirmwareError, match="regular"):
        installer._validate_staged_stat(installer.UPLOAD_PATH)


def test_staged_validation_rejects_multiple_links(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.UPLOAD_PATH.write_bytes(b"firmware")
    os.link(installer.UPLOAD_PATH, tmp_path / "second-link")
    with pytest.raises(installer.FirmwareError, match="regular"):
        installer._validate_staged_stat(installer.UPLOAD_PATH)


def test_claim_moves_only_fixed_file_and_secures_it(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.UPLOAD_PATH.write_bytes(b"firmware")
    monkeypatch.setattr(installer, "_data_is_mounted", lambda: True)
    monkeypatch.setattr(installer.os, "chown", lambda path, uid, gid: None)
    installer._claim_staged()
    assert not installer.UPLOAD_PATH.exists()
    assert installer.QUEUE_PATH.read_bytes() == b"firmware"
    assert stat.S_IMODE(installer.QUEUE_PATH.stat().st_mode) == 0o600


def test_cmdline_requires_one_consistent_slot(monkeypatch, tmp_path):
    cmdline = tmp_path / "cmdline"
    monkeypatch.setattr(installer, "CMDLINE_PATH", cmdline)
    cmdline.write_text("root=/dev/mmcblk0p2 radio.slot=A\n", encoding="utf-8")
    assert installer._read_cmdline() == (
        "A",
        "B",
        "/dev/mmcblk0p3",
        "stable,slot-b",
    )
    cmdline.write_text("root=/dev/mmcblk0p2 radio.slot=A radio.slot=B\n", encoding="utf-8")
    with pytest.raises(installer.FirmwareError, match="exactly one"):
        installer._read_cmdline()
    cmdline.write_text("root=/dev/mmcblk0p3 radio.slot=A\n", encoding="utf-8")
    with pytest.raises(installer.FirmwareError, match="does not match"):
        installer._read_cmdline()


def test_status_and_history_are_atomic_and_bounded(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.write_status("installing", percent=25, secret="not-written")
    status = json.loads(installer.STATUS_PATH.read_text(encoding="utf-8"))
    assert status["state"] == "installing"
    assert status["percent"] == 25
    assert "secret" not in status
    for number in range(installer.MAX_HISTORY_ENTRIES + 3):
        installer._append_history(
            {"version": "1.2.3", "target_slot": "B", "sha256": str(number)},
            "failed",
        )
    history = json.loads(installer.HISTORY_PATH.read_text(encoding="utf-8"))
    assert len(history) == installer.MAX_HISTORY_ENTRIES
    assert stat.S_IMODE(installer.HISTORY_PATH.stat().st_mode) == 0o600


def test_firmware_status_exposes_only_bounded_safe_state(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    package = _package(tmp_path)
    installer.UPLOAD_PATH.write_bytes(package.read_bytes())
    installer.UPLOAD_METADATA_PATH.write_text(
        json.dumps({"filename": "release.swu", "secret": "not exposed"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        installer,
        "_read_cmdline",
        lambda: ("A", "B", "/dev/mmcblk0p3", "stable,slot-b"),
    )
    state = installer.firmware_status()
    assert state["running_slot"] == "A"
    assert state["other_slot"] == "B"
    assert state["staged"]["filename"] == "release.swu"
    assert state["staged"]["version"] == "1.2.3"
    assert "secret" not in json.dumps(state)


def test_progress_parser_accepts_only_fixed_machine_protocol():
    values = installer._progress_values("PROGRESS\t2\t1\t3\t45\trootfs.ext4.gz\tlocal\n")
    assert values == {
        "swupdate_status": 2,
        "step": 1,
        "steps": 3,
        "percent": 45,
        "image": "rootfs.ext4.gz",
        "source": "local",
    }
    assert installer._progress_values("[=====] localized console output") is None


def test_cancel_refuses_active_install(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.RUNTIME_DIR.mkdir()
    installer.LOCK_PATH.write_text(str(os.getpid()), encoding="ascii")
    installer.UPLOAD_PATH.write_bytes(b"firmware")
    ok, message = installer.cancel_staged_firmware()
    assert ok is False
    assert "active" in message
    assert installer.UPLOAD_PATH.exists()


def test_install_spawns_detached_worker_and_transfers_lock(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(installer, "_claim_staged", lambda: None)
    seen = {}

    class Worker:
        pid = 4242

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return Worker()

    monkeypatch.setattr(installer.subprocess, "Popen", fake_popen)
    ok, message = installer.install_firmware()
    assert ok is True
    assert "background" in message
    assert seen["argv"][-1] == "worker"
    assert seen["kwargs"]["start_new_session"] is True
    assert installer.LOCK_PATH.read_text(encoding="ascii") == "4242\n"


def test_cancel_removes_stale_lock_and_fixed_staged_file(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.RUNTIME_DIR.mkdir()
    installer.LOCK_PATH.write_text("999999999\n", encoding="ascii")
    installer.UPLOAD_PATH.write_bytes(b"firmware")
    ok, message = installer.cancel_staged_firmware()
    assert ok is True
    assert "removed" in message
    assert not installer.LOCK_PATH.exists()
    assert not installer.UPLOAD_PATH.exists()


def test_cmdline_derives_the_opposite_slot_in_both_directions(monkeypatch, tmp_path):
    cmdline = tmp_path / "cmdline"
    monkeypatch.setattr(installer, "CMDLINE_PATH", cmdline)
    cmdline.write_text("root=/dev/mmcblk0p2 radio.slot=A\n", encoding="utf-8")
    assert installer._read_cmdline() == ("A", "B", "/dev/mmcblk0p3", "stable,slot-b")
    cmdline.write_text("root=/dev/mmcblk0p3 radio.slot=B\n", encoding="utf-8")
    assert installer._read_cmdline() == ("B", "A", "/dev/mmcblk0p2", "stable,slot-a")


def test_worker_rejection_never_invokes_inactive_installer(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.RUNTIME_DIR.mkdir()
    installer.QUEUE_PATH.write_bytes(b"invalid")
    installer.QUEUE_PATH.chmod(0o600)
    real_queue = installer.QUEUE_PATH

    class RootOwnedQueue:
        def __str__(self):
            return str(real_queue)

        def lstat(self):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=0, st_gid=0)

        def unlink(self):
            real_queue.unlink()

    monkeypatch.setattr(installer, "QUEUE_PATH", RootOwnedQueue())
    monkeypatch.setattr(installer, "_data_is_mounted", lambda: True)
    monkeypatch.setattr(
        installer,
        "_read_cmdline",
        lambda: ("A", "B", "/dev/mmcblk0p3", "stable,slot-b"),
    )
    monkeypatch.setattr(installer, "_inspect_archive", lambda *_args: {"version": "1.2.3"})
    monkeypatch.setattr(installer, "_sha256", lambda _path: "a" * 64)
    calls = []

    class CheckResult:
        returncode = 1

    monkeypatch.setattr(
        installer.subprocess, "run", lambda argv, **_kwargs: calls.append(argv) or CheckResult()
    )
    monkeypatch.setattr(
        installer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("installer started")),
    )
    assert installer._run_worker() == 1
    assert calls == [
        [installer.SWUPDATE, "-c", "-i", str(installer.QUEUE_PATH), "-e", "stable,slot-b"]
    ]
    status = json.loads(installer.STATUS_PATH.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert not real_queue.exists()
    assert not installer.LOCK_PATH.exists()


def test_worker_success_requires_swupdate_success_progress(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.RUNTIME_DIR.mkdir()
    installer.QUEUE_PATH.write_bytes(b"valid")
    installer.QUEUE_PATH.chmod(0o600)
    real_queue = installer.QUEUE_PATH

    class RootOwnedQueue:
        def __str__(self):
            return str(real_queue)

        def lstat(self):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=0, st_gid=0)

        def unlink(self):
            real_queue.unlink()

    monkeypatch.setattr(installer, "QUEUE_PATH", RootOwnedQueue())
    monkeypatch.setattr(installer, "_data_is_mounted", lambda: True)
    monkeypatch.setattr(
        installer,
        "_read_cmdline",
        lambda: ("B", "A", "/dev/mmcblk0p2", "stable,slot-a"),
    )
    monkeypatch.setattr(installer, "_inspect_archive", lambda *_args: {"version": "1.2.3"})
    monkeypatch.setattr(installer, "_sha256", lambda _path: "b" * 64)
    monkeypatch.setattr(installer.os, "sync", lambda: None)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"PROGRESS\t3\t1\t1\t100\trootfs.ext4.gz\tlocal\n")
    os.close(write_fd)
    calls = []

    class CheckResult:
        returncode = 0

    class Process:
        def __init__(self, stdout=None):
            self.stdout = stdout
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=30):
            self.returncode = 0
            return 0

        def send_signal(self, _signal):
            self.returncode = -1

    progress_file = os.fdopen(read_fd, "rb", buffering=0)

    def popen(argv, **_kwargs):
        calls.append(argv)
        return Process(progress_file if argv == [installer.PROGRESS_BRIDGE] else None)

    run_calls = []
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda argv, **_kwargs: run_calls.append(argv) or CheckResult(),
    )
    monkeypatch.setattr(installer.subprocess, "Popen", popen)
    try:
        assert installer._run_worker() == 0
    finally:
        progress_file.close()
    assert calls == [[installer.INACTIVE_INSTALLER], [installer.PROGRESS_BRIDGE]]
    assert run_calls == [
        [installer.SWUPDATE, "-c", "-i", str(installer.QUEUE_PATH), "-e", "stable,slot-a"],
        [installer.REBOOT],
    ]
    status = json.loads(installer.STATUS_PATH.read_text(encoding="utf-8"))
    assert status["state"] == "success"
    assert status["active_slot"] == "B"
    assert status["target_slot"] == "A"
    history = json.loads(installer.HISTORY_PATH.read_text(encoding="utf-8"))
    assert history[-1]["result"] == "installed"


def test_worker_uses_installer_exit_code_when_progress_reports_failure(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    installer.RUNTIME_DIR.mkdir()
    installer.QUEUE_PATH.write_bytes(b"valid")
    installer.QUEUE_PATH.chmod(0o600)
    real_queue = installer.QUEUE_PATH

    class RootOwnedQueue:
        def __str__(self):
            return str(real_queue)

        def lstat(self):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=0, st_gid=0)

        def unlink(self):
            real_queue.unlink()

    monkeypatch.setattr(installer, "QUEUE_PATH", RootOwnedQueue())
    monkeypatch.setattr(installer, "_data_is_mounted", lambda: True)
    monkeypatch.setattr(
        installer,
        "_read_cmdline",
        lambda: ("A", "B", "/dev/mmcblk0p3", "stable,slot-b"),
    )
    monkeypatch.setattr(installer, "_inspect_archive", lambda *_args: {"version": "1.2.3"})
    monkeypatch.setattr(installer, "_sha256", lambda _path: "c" * 64)
    monkeypatch.setattr(installer.os, "sync", lambda: None)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"PROGRESS\t4\t1\t1\t100\trootfs.ext4.gz\tlocal\n")
    os.close(write_fd)

    class CheckResult:
        returncode = 0

    class Process:
        def __init__(self, stdout=None):
            self.stdout = stdout
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=1800):
            self.returncode = 0
            return 0

        def send_signal(self, _signal):
            self.returncode = -1

    progress_file = os.fdopen(read_fd, "rb", buffering=0)

    def popen(argv, **_kwargs):
        return Process(progress_file if argv == [installer.PROGRESS_BRIDGE] else None)

    monkeypatch.setattr(installer.subprocess, "run", lambda *_args, **_kwargs: CheckResult())
    monkeypatch.setattr(installer.subprocess, "Popen", popen)
    try:
        assert installer._run_worker() == 0
    finally:
        progress_file.close()
    status = json.loads(installer.STATUS_PATH.read_text(encoding="utf-8"))
    assert status["state"] == "success"
