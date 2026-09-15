"""Tests for the bounded, privacy-safe operational summary."""

import json

import pytest

from radio_web import operational_summary as summary


@pytest.fixture
def paths(monkeypatch, tmp_path):
    summary_path = tmp_path / "data" / "operations" / "summary.json"
    monkeypatch.setattr(summary, "SUMMARY_PATH", summary_path)
    monkeypatch.setattr(summary, "LOCK_PATH", tmp_path / "run" / "summary.lock")
    monkeypatch.setattr(summary.time, "time", lambda: 1_700_000_000)
    return summary_path


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_first_boot_records_fixed_identity_slot_and_initial_reason(paths):
    summary.record_boot("11111111-1111-1111-1111-111111111111", "A")
    value = _read(paths)
    assert value["schema"] == 1
    assert value["current_boot"] == {
        "boot_id": "11111111-1111-1111-1111-111111111111",
        "clean_shutdown": False,
        "radio_restarts": {"heartbeat_timeout": 0, "process_exit": 0},
        "reboot_reason": "initial",
        "slot": "A",
        "started_at": 1_700_000_000,
    }


def test_next_boot_distinguishes_clean_and_unclean_shutdown(paths):
    summary.record_boot("11111111-1111-1111-1111-111111111111", "A")
    summary.record_clean_shutdown()
    summary.record_boot("22222222-2222-2222-2222-222222222222", "B")
    assert _read(paths)["current_boot"]["reboot_reason"] == "clean"
    summary.record_boot("33333333-3333-3333-3333-333333333333", "A")
    assert _read(paths)["current_boot"]["reboot_reason"] == "unclean"


def test_repeated_start_for_same_kernel_boot_is_idempotent(paths):
    boot_id = "11111111-1111-1111-1111-111111111111"
    summary.record_boot(boot_id, "A")
    summary.record_restart("process_exit")
    summary.record_boot(boot_id, "A")
    value = _read(paths)
    assert value["current_boot"]["radio_restarts"]["process_exit"] == 1
    assert [event["type"] for event in value["events"]].count("boot") == 1


def test_restart_counters_are_separate_and_events_are_bounded(paths):
    summary.record_boot("11111111-1111-1111-1111-111111111111", "A")
    for _index in range(summary.MAX_EVENTS + 10):
        summary.record_restart("process_exit")
    summary.record_restart("heartbeat_timeout")
    value = _read(paths)
    assert value["current_boot"]["radio_restarts"] == {
        "heartbeat_timeout": 1,
        "process_exit": summary.MAX_EVENTS + 10,
    }
    assert len(value["events"]) == summary.MAX_EVENTS
    assert paths.stat().st_size <= summary.MAX_SUMMARY_BYTES


def test_health_is_allowlisted_and_contains_no_free_text(paths):
    summary.record_health("trial_failed", "B")
    assert _read(paths)["last_health"] == {
        "at": 1_700_000_000,
        "result": "trial_failed",
        "slot": "B",
    }
    with pytest.raises(ValueError):
        summary.record_health("failed for https://secret.example", "B")
    with pytest.raises(ValueError):
        summary.record_restart("arbitrary message")


def test_corrupt_or_oversized_state_is_replaced(paths):
    paths.parent.mkdir(parents=True)
    paths.write_text("not json", encoding="utf-8")
    summary.record_boot("11111111-1111-1111-1111-111111111111", "A")
    assert _read(paths)["current_boot"]["reboot_reason"] == "initial"
    paths.write_text("x" * (summary.MAX_SUMMARY_BYTES + 1), encoding="utf-8")
    summary.record_boot("22222222-2222-2222-2222-222222222222", "B")
    assert _read(paths)["current_boot"]["reboot_reason"] == "initial"


def test_boot_id_and_slot_are_read_from_kernel_files(monkeypatch, paths, tmp_path):
    boot_id = tmp_path / "boot_id"
    boot_id.write_text("ABCDEFAB-CDEF-ABCD-EFAB-CDEFABCDEFAB\n", encoding="ascii")
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("quiet radio.slot=B root=/dev/mmcblk0p3 optional=secret\n", encoding="ascii")
    monkeypatch.setattr(summary, "BOOT_ID_PATH", boot_id)
    monkeypatch.setattr(summary, "CMDLINE_PATH", cmdline)
    summary.record_boot()
    current = _read(paths)["current_boot"]
    assert current["boot_id"] == "abcdefab-cdef-abcd-efab-cdefabcdefab"
    assert current["slot"] == "B"
    assert "optional" not in paths.read_text(encoding="utf-8")
