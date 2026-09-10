"""Security and round-trip tests for persistent-data backup and restore."""

import io
import json
import tarfile

import pytest

from radio_web import data_backup


@pytest.fixture
def data_tree(monkeypatch, tmp_path):
    data = tmp_path / "data"
    for name in data_backup.INCLUDED_ROOTS:
        (data / name).mkdir(parents=True)
    (data / "update").mkdir()
    (data / "radio" / "schema-version").write_text("1\n")
    (data / "radio" / "stations.ini").write_text("[one]\nname = Test\n")
    (data / "radio" / "equalizer.ini").write_text(
        "[equalizer]\nenabled = true\npreamp_db = -3\n"
    )
    (data / "network" / "wpa_supplicant.conf").write_text("secret-network\n")
    (data / "identity" / "root-password.hash").write_text("secret-hash\n")
    (data / "bluetooth" / "info").write_text("pairing-secret\n")
    (data / "update" / "firmware.swu").write_text("must-not-export\n")
    state = data / "update" / "backup-restore"
    monkeypatch.setattr(data_backup, "DATA_DIR", data)
    monkeypatch.setattr(data_backup, "STATE_DIR", state)
    monkeypatch.setattr(data_backup, "BACKUP_PATH", state / "persistent-data.tar.gz")
    monkeypatch.setattr(data_backup, "RESTORE_PATH", state / "restore.tar.gz")
    monkeypatch.setattr(
        data_backup.persistent_config,
        "compatibility_state",
        lambda: {"writer_schema": 1, "minimum_reader_schema": 1},
    )
    return data


def test_backup_includes_selected_roots_and_excludes_update(data_tree):
    ok, message = data_backup.create_backup()
    assert ok, message
    with tarfile.open(data_backup.BACKUP_PATH, "r:gz") as archive:
        names = set(archive.getnames())
        assert "data/radio/stations.ini" in names
        assert "data/radio/equalizer.ini" in names
        assert "data/network/wpa_supplicant.conf" in names
        assert "data/identity/root-password.hash" in names
        assert "data/bluetooth/info" in names
        assert not any(name.startswith("data/update") for name in names)
        manifest = json.load(archive.extractfile("manifest.json"))
    assert manifest["included_roots"] == list(data_backup.INCLUDED_ROOTS)


def test_created_backup_passes_restore_validation(data_tree):
    assert data_backup.create_backup()[0]
    data_backup.stage_restore(data_backup.BACKUP_PATH.read_bytes())
    ok, message, metadata = data_backup.inspect_restore()
    assert ok, message
    assert metadata["files"] == 6


def _archive(member: tarfile.TarInfo, content: bytes = b"") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.addfile(member, io.BytesIO(content) if member.isreg() else None)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["/data/radio/x", "data/radio/../../update/x"])
def test_restore_rejects_unsafe_paths(data_tree, name):
    member = tarfile.TarInfo(name)
    member.size = 1
    data_backup.stage_restore(_archive(member, b"x"))
    ok, message, _metadata = data_backup.inspect_restore()
    assert not ok
    assert "unsafe" in message.lower()


def test_restore_rejects_symlink(data_tree):
    member = tarfile.TarInfo("data/radio/link")
    member.type = tarfile.SYMTYPE
    member.linkname = "/etc/shadow"
    data_backup.stage_restore(_archive(member))
    ok, message, _metadata = data_backup.inspect_restore()
    assert not ok
    assert "unsupported" in message.lower()


def test_stage_restore_rejects_oversized_data(data_tree, monkeypatch):
    monkeypatch.setattr(data_backup, "MAX_ARCHIVE_BYTES", 3)
    with pytest.raises(data_backup.BackupError, match="too large"):
        data_backup.stage_restore(b"1234")
