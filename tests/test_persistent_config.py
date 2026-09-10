"""Phase 2 persistent configuration, migration and identity tests."""

import json
import shutil
import stat
from pathlib import Path

import pytest

from radio_web import config_store, persistent_config

FIXTURES = Path(__file__).parent / "fixtures" / "persistent"


@pytest.fixture()
def persistent_paths(monkeypatch, tmp_path):
    data = tmp_path / "data"
    radio = data / "radio"
    identity = data / "identity"
    backups = data / "update" / "config-backups"
    shadow = tmp_path / "shadow"
    lock = tmp_path / "run" / "config.lock"
    monkeypatch.setattr(persistent_config, "DATA_DIR", data)
    monkeypatch.setattr(persistent_config, "RADIO_DIR", radio)
    monkeypatch.setattr(persistent_config, "IDENTITY_DIR", identity)
    monkeypatch.setattr(persistent_config, "BACKUP_DIR", backups)
    monkeypatch.setattr(persistent_config, "SCHEMA_FILE", radio / "schema-version")
    monkeypatch.setattr(
        persistent_config, "COMPATIBILITY_FILE", radio / "schema-compatibility.json"
    )
    monkeypatch.setattr(persistent_config, "ROOT_HASH_FILE", identity / "root-password.hash")
    monkeypatch.setattr(persistent_config, "SHADOW_FILE", shadow)
    monkeypatch.setattr(persistent_config, "LOCK_FILE", lock)
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(radio))
    return data, shadow


def test_migration_is_idempotent_and_schema_is_written_last(persistent_paths):
    data, _shadow = persistent_paths
    (data / "radio").mkdir(parents=True)
    (data / "radio" / "device.ini").write_text("[device]\nname = radio\n")
    persistent_config.migrate()
    assert (data / "radio" / "schema-version").read_text() == "1\n"
    backups = list((data / "update" / "config-backups").iterdir())
    assert len(backups) == 1
    compatibility = json.loads(
        (data / "radio" / "schema-compatibility.json").read_text(encoding="utf-8")
    )
    assert compatibility == {
        "minimum_reader_schema": 1,
        "schema": 1,
        "writer_schema": 1,
    }
    persistent_config.migrate()
    assert list((data / "update" / "config-backups").iterdir()) == backups


def test_additive_future_schema_is_readable_only_with_compatibility_marker(persistent_paths):
    data, _shadow = persistent_paths
    radio = data / "radio"
    radio.mkdir(parents=True)
    (radio / "schema-version").write_text("2\n", encoding="ascii")
    (radio / "schema-compatibility.json").write_text(
        json.dumps({"schema": 1, "writer_schema": 2, "minimum_reader_schema": 1}),
        encoding="utf-8",
    )
    assert persistent_config.can_read_persistent_state(1) is True
    persistent_config.migrate()
    assert (radio / "schema-version").read_text(encoding="ascii") == "2\n"


def test_incompatible_future_schema_is_rejected(persistent_paths):
    data, _shadow = persistent_paths
    radio = data / "radio"
    radio.mkdir(parents=True)
    (radio / "schema-version").write_text("2\n", encoding="ascii")
    (radio / "schema-compatibility.json").write_text(
        json.dumps({"schema": 1, "writer_schema": 2, "minimum_reader_schema": 2}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="newer firmware reader"):
        persistent_config.migrate()


def test_failed_migration_keeps_old_schema_and_complete_backup(persistent_paths, monkeypatch):
    data, _shadow = persistent_paths
    radio = data / "radio"
    radio.mkdir(parents=True)
    (radio / "schema-version").write_text("0\n", encoding="ascii")
    (radio / "device.ini").write_text("[device]\nname = old-radio\n", encoding="utf-8")

    def fail():
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(persistent_config, "MIGRATIONS", {1: fail})
    with pytest.raises(RuntimeError, match="interruption"):
        persistent_config.migrate()
    assert (radio / "schema-version").read_text(encoding="ascii") == "0\n"
    backups = list((data / "update" / "config-backups").iterdir())
    assert len(backups) == 1
    assert not backups[0].name.startswith(".")
    assert (backups[0] / "radio" / "device.ini").read_text(encoding="utf-8").endswith("old-radio\n")


def test_backup_is_accepted_only_after_mark_good(persistent_paths):
    data, _shadow = persistent_paths
    (data / "radio").mkdir(parents=True)
    persistent_config.migrate()
    backup = next((data / "update" / "config-backups").iterdir())
    assert json.loads((backup / "backup.json").read_text(encoding="utf-8"))["accepted"] is False
    persistent_config.mark_migration_accepted()
    metadata = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    assert metadata["accepted"] is True
    assert "accepted_at" in metadata


def test_current_code_reads_previous_release_fixture(persistent_paths):
    data, _shadow = persistent_paths
    shutil.copytree(FIXTURES / "schema-1", data, dirs_exist_ok=True)
    persistent_config.migrate()
    assert persistent_config.device_store.load_device()["name"] == "previous-radio"
    assert persistent_config.audio_hardware_store.load_profile() == "headphones"


def test_previous_reader_accepts_additive_new_release_fixture(persistent_paths):
    data, _shadow = persistent_paths
    shutil.copytree(FIXTURES / "additive-schema-2", data, dirs_exist_ok=True)
    assert persistent_config.can_read_persistent_state(1) is True
    assert persistent_config.device_store.load_device()["name"] == "future-radio"


def test_root_hash_round_trip_preserves_other_accounts(persistent_paths):
    data, shadow = persistent_paths
    shadow.write_text("root:$6$old$hash:1:2:3:4:5:6:7\nradio:!:8:9:10:11:12:13:14\n")
    persistent_config.capture_root_password()
    saved = data / "identity" / "root-password.hash"
    assert saved.read_text() == "$6$old$hash\n"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    shadow.write_text("root:!:1:2:3:4:5:6:7\nradio:!:8:9:10:11:12:13:14\n")
    persistent_config.apply_root_password()
    assert "root:$6$old$hash:1:2:3:4:5:6:7" in shadow.read_text()
    assert "radio:!:8:9:10:11:12:13:14" in shadow.read_text()


def test_invalid_persistent_root_hash_is_rejected(persistent_paths):
    data, shadow = persistent_paths
    shadow.write_text("root:!:1:2:3:4:5:6:7\n")
    (data / "identity").mkdir(parents=True)
    (data / "identity" / "root-password.hash").write_text("bad:field\n")
    with pytest.raises(ValueError, match="unsafe format"):
        persistent_config.apply_root_password()
    assert shadow.read_text() == "root:!:1:2:3:4:5:6:7\n"


def test_persistence_surface_keeps_adc_calibration_but_excludes_runtime_state():
    root = Path(__file__).resolve().parents[1]
    script = (
        root / "buildroot/external/board/radio/rootfs-overlay/usr/sbin/radio-persistent-paths"
    ).read_text(encoding="utf-8")
    assert 'link_path "$DATA/radio" /etc/radio' in script
    assert "adc.ini" not in script  # Covered by the canonical /etc/radio directory.
    for transient in (
        "radio-adc.json",
        "calibration-capture",
        "mpd/tag_cache",
        "mpd/state",
        "sticker_file",
        "asound.state",
    ):
        assert transient not in script
