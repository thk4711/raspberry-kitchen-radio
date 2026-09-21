"""Host tests for fixed read-only slot inspection and manual trial selection."""

import json
from pathlib import Path

import pytest

from radio_web import firmware_slots as slots


class Result:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _environment(active="A", trial="0"):
    return {
        "active_slot": active,
        "previous_slot": "B" if active == "A" else "A",
        "upgrade_available": trial,
        "bootcount": "0",
        "bootlimit": "3",
        "rollback_from": "none",
    }


def _release(version="0.0.9", reader=1):
    return {
        "schema": 1,
        "version": version,
        "hardware_revision": slots.HARDWARE_REVISION,
        "persistent_schema": 1,
        "persistent_reader_schema": reader,
        "minimum_rollback_reader_schema": 1,
    }


def test_release_metadata_rejects_symlink_and_incompatible_hardware(tmp_path):
    target = tmp_path / "release.json"
    target.write_text(
        json.dumps({"schema": 1, "version": "1.2.3", "hardware_revision": "wrong"}),
        encoding="utf-8",
    )
    with pytest.raises(slots.SlotError, match="not compatible"):
        slots._release_metadata(target)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(slots.SlotError, match="unsafe"):
        slots._release_metadata(link)


def test_environment_script_uses_libubootenv_assignment_syntax(monkeypatch, tmp_path):
    monkeypatch.setattr(slots, "ENV_TMP_DIR", tmp_path)
    scripts = []

    def fake_run(argv, timeout=30):
        scripts.append(Path(argv[-1]).read_text(encoding="ascii"))
        return Result()

    monkeypatch.setattr(slots, "_run", fake_run)
    slots._set_environment({"active_slot": "B", "upgrade_available": "1"})
    assert scripts == ["active_slot=B\nupgrade_available=1\n"]


def test_inspection_uses_fixed_read_only_mount_and_always_unmounts(monkeypatch, tmp_path):
    mountpoint = tmp_path / "other"
    release = mountpoint / slots.RELEASE_PATH
    calls = []
    monkeypatch.setattr(slots, "OTHER_MOUNT", mountpoint)
    monkeypatch.setattr(
        slots.firmware_installer,
        "_read_cmdline",
        lambda: ("A", "B", "/dev/mmcblk0p3", "stable,slot-b"),
    )

    def fake_run(argv, timeout=30):
        calls.append(argv)
        if argv[0] == slots.MOUNT:
            release.parent.mkdir(parents=True)
            release.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "version": "0.0.9",
                        "hardware_revision": slots.HARDWARE_REVISION,
                    }
                ),
                encoding="utf-8",
            )
        return Result()

    monkeypatch.setattr(slots, "_run", fake_run)
    metadata = slots._inspect_other_slot_unlocked()
    assert metadata["version"] == "0.0.9"
    assert calls[0] == [
        slots.MOUNT,
        "-t",
        "ext4",
        "-o",
        "ro,noload,nodev,nosuid,noexec",
        "/dev/mmcblk0p3",
        str(mountpoint),
    ]
    assert calls[-1] == [slots.UMOUNT, str(mountpoint)]


def test_inspection_unmounts_after_invalid_metadata(monkeypatch, tmp_path):
    mountpoint = tmp_path / "other"
    release = mountpoint / slots.RELEASE_PATH
    calls = []
    monkeypatch.setattr(slots, "OTHER_MOUNT", mountpoint)
    monkeypatch.setattr(
        slots.firmware_installer,
        "_read_cmdline",
        lambda: ("B", "A", "/dev/mmcblk0p2", "stable,slot-a"),
    )

    def fake_run(argv, timeout=30):
        calls.append(argv)
        if argv[0] == slots.MOUNT:
            release.parent.mkdir(parents=True)
            release.write_text("not json", encoding="utf-8")
        return Result()

    monkeypatch.setattr(slots, "_run", fake_run)
    with pytest.raises(slots.SlotError, match="unavailable"):
        slots._inspect_other_slot_unlocked()
    assert calls[-1] == [slots.UMOUNT, str(mountpoint)]


def test_switch_status_labels_only_older_firmware_as_rollback(monkeypatch):
    monkeypatch.setattr(slots.firmware_installer, "installation_active", lambda: False)
    monkeypatch.setattr(slots, "_environment", lambda: _environment())
    monkeypatch.setattr(
        slots,
        "inspect_other_slot",
        lambda: {"running_slot": "A", "other_slot": "B", "version": "0.0.9"},
    )
    status = slots.switch_status()
    assert status["switch_allowed"] is True
    assert status["switch_label"] == "Roll back to 0.0.9"
    assert status["other_version"] == "0.0.9"
    monkeypatch.setattr(
        slots,
        "inspect_other_slot",
        lambda: {"running_slot": "A", "other_slot": "B", "version": slots.__version__},
    )
    assert slots.switch_status()["switch_label"] == (
        f"Switch to other firmware ({slots.__version__})"
    )


def test_switch_status_blocks_an_existing_trial(monkeypatch):
    monkeypatch.setattr(slots.firmware_installer, "installation_active", lambda: False)
    monkeypatch.setattr(slots, "_environment", lambda: _environment(trial="1"))
    status = slots.switch_status()
    assert status["switch_allowed"] is False
    assert "trial" in status["switch_reason"]


def test_other_slot_is_blocked_when_it_cannot_read_persistent_data(monkeypatch, tmp_path):
    mountpoint = tmp_path / "other"
    release = mountpoint / slots.RELEASE_PATH
    monkeypatch.setattr(slots, "OTHER_MOUNT", mountpoint)
    monkeypatch.setattr(
        slots.firmware_installer,
        "_read_cmdline",
        lambda: ("A", "B", "/dev/mmcblk0p3", "stable,slot-b"),
    )
    monkeypatch.setattr(
        slots.persistent_config,
        "compatibility_state",
        lambda: {"writer_schema": 2, "minimum_reader_schema": 2},
    )

    def fake_run(argv, timeout=30):
        if argv[0] == slots.MOUNT:
            release.parent.mkdir(parents=True)
            release.write_text(json.dumps(_release(reader=1)), encoding="utf-8")
        return Result()

    monkeypatch.setattr(slots, "_run", fake_run)
    with pytest.raises(slots.SlotError, match="cannot read"):
        slots._inspect_other_slot_unlocked()


def test_prepare_rollback_commits_verifies_then_reboots(monkeypatch, tmp_path):
    monkeypatch.setattr(slots.firmware_installer, "LOCK_PATH", tmp_path / "install.lock")
    monkeypatch.setattr(slots.firmware_installer, "RUNTIME_DIR", tmp_path)
    environments = iter((_environment(), {**_environment("B", "1"), "previous_slot": "A"}))
    writes = []
    calls = []
    monkeypatch.setattr(slots, "_environment", lambda: next(environments))
    monkeypatch.setattr(
        slots,
        "inspect_other_slot",
        lambda: {"running_slot": "A", "other_slot": "B", "version": "0.0.9"},
    )
    monkeypatch.setattr(slots, "_set_environment", lambda values: writes.append(values))
    monkeypatch.setattr(slots.firmware_installer, "_append_history", lambda metadata, result: None)
    monkeypatch.setattr(slots.os, "sync", lambda: None)
    monkeypatch.setattr(slots, "_run", lambda argv, timeout=30: calls.append(argv) or Result())
    ok, _message = slots.prepare_rollback()
    assert ok is True
    assert writes == [
        {
            "active_slot": "B",
            "bootcount": "0",
            "previous_slot": "A",
            "rollback_from": "none",
            "upgrade_available": "1",
        }
    ]
    assert calls == [[slots.REBOOT]]


def test_prepare_rollback_does_not_reboot_when_commit_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(slots.firmware_installer, "LOCK_PATH", tmp_path / "install.lock")
    monkeypatch.setattr(slots.firmware_installer, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(slots, "_environment", lambda: _environment())
    monkeypatch.setattr(
        slots,
        "inspect_other_slot",
        lambda: {"running_slot": "A", "other_slot": "B", "version": "0.0.9"},
    )
    monkeypatch.setattr(
        slots, "_set_environment", lambda values: (_ for _ in ()).throw(slots.SlotError("failed"))
    )
    calls = []
    monkeypatch.setattr(slots, "_run", lambda argv, timeout=30: calls.append(argv) or Result())
    ok, _message = slots.prepare_rollback()
    assert ok is False
    assert calls == []


@pytest.mark.parametrize(("running", "other"), [("A", "B"), ("B", "A")])
def test_prepare_rollback_sets_a_verified_trial_in_both_directions(
    monkeypatch, tmp_path, running, other
):
    monkeypatch.setattr(slots.firmware_installer, "LOCK_PATH", tmp_path / "install.lock")
    monkeypatch.setattr(slots.firmware_installer, "RUNTIME_DIR", tmp_path)
    initial = _environment(running)
    committed = {
        **_environment(other, "1"),
        "previous_slot": running,
        "rollback_from": "none",
    }
    environments = iter((initial, committed))
    writes = []
    reboots = []
    monkeypatch.setattr(slots, "_environment", lambda: next(environments))
    monkeypatch.setattr(
        slots,
        "inspect_other_slot",
        lambda: {"running_slot": running, "other_slot": other, "version": "0.0.9"},
    )
    monkeypatch.setattr(slots, "_set_environment", lambda values: writes.append(values))
    monkeypatch.setattr(slots.firmware_installer, "_append_history", lambda *_args: None)
    monkeypatch.setattr(slots.os, "sync", lambda: None)
    monkeypatch.setattr(slots, "_run", lambda argv, timeout=30: reboots.append(argv) or Result())
    assert slots.prepare_rollback()[0] is True
    assert writes == [
        {
            "active_slot": other,
            "bootcount": "0",
            "previous_slot": running,
            "rollback_from": "none",
            "upgrade_available": "1",
        }
    ]
    assert reboots == [[slots.REBOOT]]


def test_prepare_rollback_does_not_reboot_when_verification_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(slots.firmware_installer, "LOCK_PATH", tmp_path / "install.lock")
    monkeypatch.setattr(slots.firmware_installer, "RUNTIME_DIR", tmp_path)
    environments = iter((_environment(), _environment()))
    monkeypatch.setattr(slots, "_environment", lambda: next(environments))
    monkeypatch.setattr(
        slots,
        "inspect_other_slot",
        lambda: {"running_slot": "A", "other_slot": "B", "version": "0.0.9"},
    )
    monkeypatch.setattr(slots, "_set_environment", lambda _values: None)
    calls = []
    monkeypatch.setattr(slots, "_run", lambda argv, timeout=30: calls.append(argv) or Result())
    ok, message = slots.prepare_rollback()
    assert ok is False
    assert "verified" in message
    assert calls == []


def test_prepare_rollback_is_rejected_while_installation_lock_is_active(monkeypatch, tmp_path):
    lock = tmp_path / "install.lock"
    lock.write_text(f"{__import__('os').getpid()}\n", encoding="ascii")
    monkeypatch.setattr(slots.firmware_installer, "LOCK_PATH", lock)
    monkeypatch.setattr(slots.firmware_installer, "RUNTIME_DIR", tmp_path)
    inspected = []
    monkeypatch.setattr(slots, "inspect_other_slot", lambda: inspected.append(True))
    ok, message = slots.prepare_rollback()
    assert ok is False
    assert "active" in message
    assert inspected == []
