"""End-to-end integration tests for the A/B firmware update flow (Phase B2).

Where the per-module tests (``test_firmware_installer.py``,
``test_firmware_slots.py``, ``test_firmware_health.py``) each exercise one trust
boundary in isolation, this suite wires ``firmware_installer`` +
``firmware_slots`` + ``firmware_health`` together against a single shared temp
"image layout" fixture so a whole update lifecycle is covered as one story:

    stage a real ``.swu``  ->  inspect it  ->  read installer status
      ->  read slot-switch eligibility  ->  simulate a trial boot
      ->  accept a healthy trial   (and, separately, reject an unhealthy one)

The fixture uses *fake block-device files* inside ``tmp_path`` (plus fake
``/proc/cmdline`` and ``/proc/mounts`` files and a stubbed U-Boot env), so the
flow runs unprivileged on any developer/CI machine without loopback devices.
"""

import json
import os
from pathlib import Path

import pytest

from radio_web import firmware_health as health
from radio_web import firmware_installer as installer
from radio_web import firmware_slots as slots
from scripts import build_firmware_swu as swu

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "buildroot" / "external" / "board" / "radio"
TEMPLATE = BOARD / "sw-description.in"

SHA_LEN = 64


def _make_swu(tmp_path: Path, version: str = "1.2.3") -> Path:
    """Build a valid firmware archive targeting the inactive slot device."""
    payload = tmp_path / "rootfs.ext4.gz"
    payload.write_bytes(b"compressed-rootfs-image")
    manifest = swu.render_manifest(TEMPLATE, version, payload)
    description = tmp_path / "sw-description"
    description.write_text(manifest, encoding="utf-8")
    package = tmp_path / "firmware.swu"
    swu.write_cpio(package, [("sw-description", description), (swu.PAYLOAD_NAME, payload)])
    return package


def _stage_upload(package: Path) -> None:
    """Copy the built package to the web-writable staged upload path."""
    installer.UPLOAD_PATH.write_bytes(package.read_bytes())
    os.chmod(installer.UPLOAD_PATH, 0o600)


def _installed_history(image_layout, target_slot="B", version="1.2.3"):
    image_layout.history.write_text(
        json.dumps(
            [
                {
                    "version": version,
                    "target_slot": target_slot,
                    "sha256": "a" * SHA_LEN,
                    "result": "installed",
                }
            ]
        ),
        encoding="utf-8",
    )


def _trial(active="B"):
    return {
        "active_slot": active,
        "previous_slot": "A" if active == "B" else "B",
        "upgrade_available": "1",
        "bootcount": "1",
        "bootlimit": "3",
        "rollback_from": "none",
    }


@pytest.fixture
def image_layout(monkeypatch, tmp_path):
    """A shared fake image layout wiring all three firmware modules together.

    Active slot is A (root ``/dev/mmcblk0p2``), so the update targets slot B
    (device ``/dev/mmcblk0p3``). Returns a small namespace of useful paths.
    """
    data = tmp_path / "data"
    upload = data / "update" / "upload"
    queue = data / "update" / "queue"
    runtime = tmp_path / "run"
    upload.mkdir(parents=True)
    queue.mkdir(parents=True)
    (data / "radio").mkdir(parents=True)
    (data / "radio" / "schema-version").write_text("1\n", encoding="ascii")

    monkeypatch.setattr(installer, "DATA_MOUNT", data)
    monkeypatch.setattr(installer, "UPLOAD_PATH", upload / "firmware.swu.ready")
    monkeypatch.setattr(installer, "UPLOAD_METADATA_PATH", upload / "firmware.swu.json")
    monkeypatch.setattr(installer, "QUEUE_PATH", queue / "firmware.swu")
    monkeypatch.setattr(installer, "HISTORY_PATH", data / "update" / "history.json")
    monkeypatch.setattr(installer, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(installer, "SWUPDATE_RUNTIME_DIR", runtime / "swupdate")
    monkeypatch.setattr(installer, "STATUS_PATH", runtime / "status.json")
    monkeypatch.setattr(installer, "SWUPDATE_LOG_PATH", runtime / "swupdate.log")
    monkeypatch.setattr(installer, "LOCK_PATH", runtime / "install.lock")
    monkeypatch.setattr(installer, "WEB_UID", os.getuid())
    monkeypatch.setattr(installer, "WEB_GID", os.getgid())

    cmdline = tmp_path / "cmdline"
    cmdline.write_text("root=/dev/mmcblk0p2 radio.slot=A\n", encoding="utf-8")
    mounts = tmp_path / "mounts"
    mounts.write_text(
        f"/dev/mmcblk0p4 {data} ext4 rw,relatime 0 0\n"
        "/dev/mmcblk0p4 /data ext4 rw,relatime 0 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(installer, "CMDLINE_PATH", cmdline)
    monkeypatch.setattr(installer, "MOUNTS_PATH", mounts)

    other_mount = tmp_path / "other"
    monkeypatch.setattr(slots, "OTHER_MOUNT", other_mount)
    monkeypatch.setattr(slots, "SLOT_LOCK", runtime / "slot.lock")
    monkeypatch.setattr(slots, "ENV_TMP_DIR", runtime)

    def fake_mount_run(argv, timeout=30):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if argv[0] == slots.MOUNT:
            release = other_mount / slots.RELEASE_PATH
            release.parent.mkdir(parents=True, exist_ok=True)
            release.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "version": "1.2.3",
                        "hardware_revision": slots.HARDWARE_REVISION,
                        "persistent_schema": 1,
                        "persistent_reader_schema": 1,
                        "minimum_rollback_reader_schema": 1,
                    }
                ),
                encoding="utf-8",
            )
        return R()

    monkeypatch.setattr(slots, "_run", fake_mount_run)

    environment = {
        "active_slot": "A",
        "previous_slot": "B",
        "upgrade_available": "0",
        "bootcount": "0",
        "bootlimit": "3",
        "rollback_from": "none",
    }
    monkeypatch.setattr(slots, "_environment", lambda: dict(environment))

    heartbeat = tmp_path / "radio.alive"
    heartbeat.touch()
    mpd_pid = tmp_path / "mpd.pid"
    mpd_pid.write_text(f"{os.getpid()}\n", encoding="ascii")
    monkeypatch.setattr(health, "CMDLINE_PATH", cmdline)
    monkeypatch.setattr(health, "MOUNTS_PATH", mounts)
    monkeypatch.setattr(health, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(health, "MPD_PID_PATH", mpd_pid)
    monkeypatch.setattr(health, "SCHEMA_PATH", data / "radio" / "schema-version")
    monkeypatch.setattr(health, "ENV_TMP_DIR", tmp_path)

    class Layout:
        pass

    layout = Layout()
    layout.data = data
    layout.history = data / "update" / "history.json"
    layout.environment = environment
    return layout


def test_inspect_then_status_reports_staged_firmware(image_layout, tmp_path):
    package = _make_swu(tmp_path)
    _stage_upload(package)

    ok, message = installer.inspect_firmware()
    assert ok, message
    assert "1.2.3" in message
    digest = message.split("SHA-256 ")[1].rstrip(").")
    assert len(digest) == SHA_LEN
    assert digest == installer._sha256(installer.UPLOAD_PATH)

    state = installer.firmware_status()
    assert state["running_slot"] == "A"
    assert state["other_slot"] == "B"
    assert state["staged"]["version"] == "1.2.3"
    assert state["phase"] == "staged"


def test_slot_switch_eligible_when_other_slot_compatible(image_layout, tmp_path):
    package = _make_swu(tmp_path)
    _stage_upload(package)
    status = slots.switch_status()
    # The stubbed other slot advertises a compatible 1.2.3 release.
    assert status["switch_allowed"] is True
    assert status["other_slot"] == "B"
    assert status["other_version"] == "1.2.3"


def test_slot_switch_blocked_during_active_trial(image_layout, monkeypatch):
    monkeypatch.setattr(
        slots, "_environment", lambda: {**image_layout.environment, "upgrade_available": "1"}
    )
    status = slots.switch_status()
    assert status["switch_allowed"] is False
    assert "trial" in status["switch_reason"].lower()


def test_healthy_trial_boot_is_accepted_end_to_end(image_layout, monkeypatch):
    _installed_history(image_layout)
    # Trial boot now runs from slot B (root p3).
    health.CMDLINE_PATH.write_text("root=/dev/mmcblk0p3 radio.slot=B\n", encoding="utf-8")
    environment = _trial("B")
    writes = []
    monkeypatch.setattr(health, "_read_environment", lambda: environment)
    monkeypatch.setattr(health, "_web_healthy", lambda: True)
    monkeypatch.setattr(health.sources_store, "load_sources", lambda: {"internet_radio": False})
    monkeypatch.setattr(health, "_set_environment", lambda values: writes.append(values))
    monkeypatch.setattr(health.operational_summary, "best_effort_health", lambda *a: None)
    monkeypatch.setattr(health.persistent_config, "mark_migration_accepted", lambda: None)

    assert health.health_failures(environment) == []
    health.accept_trial(environment)

    assert writes == [{"bootcount": "0", "upgrade_available": "0"}]
    entry = json.loads(image_layout.history.read_text(encoding="utf-8"))[-1]
    assert entry["result"] == "accepted"

    # Installer status now reflects the accepted trial as the running firmware.
    state = installer.firmware_status()
    assert state["last_update"]["result"] == "accepted"


def test_unhealthy_trial_boot_is_reported_as_failure(image_layout, monkeypatch):
    _installed_history(image_layout)
    health.CMDLINE_PATH.write_text("root=/dev/mmcblk0p3 radio.slot=B\n", encoding="utf-8")
    environment = _trial("B")
    # Make the local web health check fail so the trial is not healthy.
    monkeypatch.setattr(health, "_web_healthy", lambda: False)
    monkeypatch.setattr(health.sources_store, "load_sources", lambda: {"internet_radio": False})

    failures = health.health_failures(environment)
    assert "local web health check is unavailable" in failures
