"""Host tests for trial boot health confirmation and rollback reconciliation."""

import json
import os
from pathlib import Path

from radio_web import firmware_health as health
from radio_web import firmware_installer


def _paths(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "radio").mkdir(parents=True)
    (data / "update").mkdir()
    (data / "radio" / "schema-version").write_text("1\n", encoding="ascii")
    (data / "update" / "history.json").write_text(
        json.dumps(
            [
                {
                    "version": "1.2.3",
                    "target_slot": "B",
                    "sha256": "a" * 64,
                    "result": "installed",
                }
            ]
        ),
        encoding="utf-8",
    )
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("root=/dev/mmcblk0p3 radio.slot=B\n", encoding="utf-8")
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/mmcblk0p4 /data ext4 rw,relatime 0 0\n", encoding="utf-8")
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
    monkeypatch.setattr(firmware_installer, "HISTORY_PATH", data / "update" / "history.json")


def _trial(slot: str = "B"):
    return {
        "active_slot": slot,
        "previous_slot": "A" if slot == "B" else "B",
        "upgrade_available": "1",
        "bootcount": "1",
        "bootlimit": "3",
        "rollback_from": "none",
    }


def test_healthy_trial_is_accepted_atomically(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    environment = _trial()
    writes = []
    monkeypatch.setattr(health, "_read_environment", lambda: environment)
    monkeypatch.setattr(health, "_web_healthy", lambda: True)
    monkeypatch.setattr(health.sources_store, "load_sources", lambda: {"internet_radio": True})
    monkeypatch.setattr(health, "_set_environment", lambda values: writes.append(values))
    accepted = []
    monkeypatch.setattr(
        health.persistent_config, "mark_migration_accepted", lambda: accepted.append(True)
    )
    health.accept_trial(environment)
    assert writes == [{"bootcount": "0", "upgrade_available": "0"}]
    history = json.loads(firmware_installer.HISTORY_PATH.read_text(encoding="utf-8"))
    assert history[-1]["result"] == "accepted"
    assert accepted == [True]


def test_environment_script_uses_libubootenv_assignment_syntax(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "ENV_TMP_DIR", tmp_path)
    scripts = []

    class Result:
        returncode = 0

    def fake_run(argv, **_kwargs):
        scripts.append(Path(argv[-1]).read_text(encoding="ascii"))
        return Result()

    monkeypatch.setattr(health.subprocess, "run", fake_run)
    health._set_environment({"upgrade_available": "0", "bootcount": "0"})
    assert scripts == ["bootcount=0\nupgrade_available=0\n"]


def test_health_checks_are_local_and_mpd_is_conditional(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(health, "_web_healthy", lambda: True)
    monkeypatch.setattr(health.sources_store, "load_sources", lambda: {"internet_radio": True})
    assert health.health_failures(_trial()) == []
    health.MPD_PID_PATH.write_text("999999999\n", encoding="ascii")
    assert health.health_failures(_trial()) == ["enabled MPD service is not running"]
    monkeypatch.setattr(health.sources_store, "load_sources", lambda: {"internet_radio": False})
    assert health.health_failures(_trial()) == []


def test_slot_root_mismatch_and_read_only_data_fail(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    health.CMDLINE_PATH.write_text("root=/dev/mmcblk0p2 radio.slot=B\n", encoding="utf-8")
    health.MOUNTS_PATH.write_text("/dev/mmcblk0p4 /data ext4 ro,relatime 0 0\n", encoding="utf-8")
    monkeypatch.setattr(health, "_web_healthy", lambda: True)
    monkeypatch.setattr(health.sources_store, "load_sources", lambda: {"internet_radio": False})
    failures = health.health_failures(_trial())
    assert "Kernel root device does not match its slot" in failures
    assert "persistent data is not mounted read-write" in failures


def test_unmigrated_schema_fails_health_check(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    health.SCHEMA_PATH.write_text("0\n", encoding="ascii")
    monkeypatch.setattr(health, "_web_healthy", lambda: True)
    monkeypatch.setattr(health.sources_store, "load_sources", lambda: {"internet_radio": False})
    assert "persistent schema migration is incomplete" in health.health_failures(_trial())


def test_unhealthy_deadline_records_reason_and_reboots(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    environment = _trial()
    ticks = iter((0.0, 0.0, 2.0))
    calls = []
    monkeypatch.setattr(health, "DEADLINE_SECONDS", 1)
    monkeypatch.setattr(health.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(health, "_read_environment", lambda: environment)
    monkeypatch.setattr(health, "reconcile_rollback", lambda _values: False)
    monkeypatch.setattr(health, "health_failures", lambda _values: ["web unavailable"])
    monkeypatch.setattr(health.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(health.os, "sync", lambda: None)
    monkeypatch.setattr(
        health.subprocess,
        "run",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )
    assert health.run_trial() == 1
    assert calls == [([health.REBOOT], {"timeout": 10, "check": False})]
    history = json.loads(firmware_installer.HISTORY_PATH.read_text(encoding="utf-8"))
    assert history[-1]["result"] == "trial_failed"
    assert history[-1]["failure_reason"] == "web unavailable"


def test_reconcile_records_automatic_rollback_once(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    health.CMDLINE_PATH.write_text("root=/dev/mmcblk0p2 radio.slot=A\n", encoding="utf-8")
    environment = _trial()
    environment.update(upgrade_available="0", rollback_from="B")
    writes = []
    monkeypatch.setattr(health, "_set_environment", lambda values: writes.append(values))
    assert health.reconcile_rollback(environment) is True
    assert writes == [{"rollback_from": "none"}]
    history = json.loads(firmware_installer.HISTORY_PATH.read_text(encoding="utf-8"))
    assert history[-1]["result"] == "automatic_rollback"


def test_reconcile_acceptance_after_interrupted_history_write(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    environment = _trial()
    environment.update(upgrade_available="0", rollback_from="none")
    assert health.reconcile_acceptance(environment) is True
    history = json.loads(firmware_installer.HISTORY_PATH.read_text(encoding="utf-8"))
    assert history[-1]["result"] == "accepted"
    assert health.reconcile_acceptance(environment) is False


def test_manual_switch_history_is_reconciled_by_normal_trial_health(monkeypatch, tmp_path):
    _paths(monkeypatch, tmp_path)
    history = json.loads(firmware_installer.HISTORY_PATH.read_text(encoding="utf-8"))
    history[-1]["result"] = "manual_switch_prepared"
    firmware_installer.HISTORY_PATH.write_text(json.dumps(history), encoding="utf-8")
    assert health._record_result("B", "accepted") is True
    history = json.loads(firmware_installer.HISTORY_PATH.read_text(encoding="utf-8"))
    assert history[-1]["result"] == "accepted"


def test_environment_parser_rejects_incomplete_state(monkeypatch):
    class Result:
        stdout = "active_slot=A\nupgrade_available=1\n"

    monkeypatch.setattr(health.subprocess, "run", lambda *args, **kwargs: Result())
    try:
        health._read_environment()
    except health.HealthError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("incomplete environment was accepted")
