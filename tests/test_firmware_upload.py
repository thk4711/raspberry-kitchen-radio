"""Tests for bounded, atomic firmware upload staging."""

import hashlib
import io
import json
import os
import socket
import threading

import pytest

from radio_web import firmware_upload


class RecordingStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def _allow_space(monkeypatch, free=10**9):
    usage = os.statvfs_result((0, 0, 0, 0, 0, 0, free, free, 0, 255))
    monkeypatch.setattr(
        firmware_upload.shutil,
        "disk_usage",
        lambda _path: type("Usage", (), {"free": free})(),
    )
    return usage


def test_success_streams_in_bounded_chunks_and_publishes_ready(monkeypatch, tmp_path):
    _allow_space(monkeypatch)
    monkeypatch.setattr(firmware_upload, "CHUNK_BYTES", 4)
    payload = b"firmware-payload"
    stream = RecordingStream(payload)

    result = firmware_upload.stage_upload(
        stream, len(payload), filename="../Kitchen Radio 1.2.3.swu", upload_dir=str(tmp_path)
    )

    assert result.bytes_received == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / firmware_upload.READY_FILENAME).read_bytes() == payload
    assert not (tmp_path / firmware_upload.PART_FILENAME).exists()
    metadata = json.loads((tmp_path / firmware_upload.METADATA_FILENAME).read_text())
    assert metadata["filename"] == "Kitchen Radio 1.2.3.swu"
    assert metadata["sha256"] == result.sha256
    assert stream.read_sizes
    assert max(stream.read_sizes) <= 4


def test_exact_limit_is_accepted(monkeypatch, tmp_path):
    _allow_space(monkeypatch)
    monkeypatch.setattr(firmware_upload, "MAX_FIRMWARE_BYTES", 4)
    result = firmware_upload.stage_upload(io.BytesIO(b"1234"), 4, upload_dir=str(tmp_path))
    assert result.bytes_received == 4


def test_over_limit_is_rejected_without_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(firmware_upload, "MAX_FIRMWARE_BYTES", 4)
    stream = RecordingStream(b"12345")
    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(stream, 5, upload_dir=str(tmp_path))
    assert caught.value.status == 413
    assert stream.read_sizes == []


@pytest.mark.parametrize("length", [0, -1])
def test_non_positive_length_is_rejected(tmp_path, length):
    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(io.BytesIO(), length, upload_dir=str(tmp_path))
    assert caught.value.code == "invalid_length"


def test_insufficient_space_preserves_existing_ready(monkeypatch, tmp_path):
    ready = tmp_path / firmware_upload.READY_FILENAME
    ready.write_bytes(b"previous")
    _allow_space(monkeypatch, free=1)
    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(io.BytesIO(b"new"), 3, upload_dir=str(tmp_path))
    assert caught.value.status == 507
    assert ready.read_bytes() == b"previous"


def test_short_upload_removes_partial_and_invalidates_old_ready(monkeypatch, tmp_path):
    _allow_space(monkeypatch)
    (tmp_path / firmware_upload.PART_FILENAME).write_bytes(b"stale-part")
    (tmp_path / firmware_upload.READY_FILENAME).write_bytes(b"stale-ready")

    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(io.BytesIO(b"short"), 10, upload_dir=str(tmp_path))

    assert caught.value.code == "short_upload"
    assert not (tmp_path / firmware_upload.PART_FILENAME).exists()
    assert not (tmp_path / firmware_upload.READY_FILENAME).exists()


@pytest.mark.parametrize(
    ("error", "code"),
    [(socket.timeout(), "upload_timeout"), (ConnectionResetError(), "upload_interrupted")],
)
def test_read_failure_removes_partial(monkeypatch, tmp_path, error, code):
    _allow_space(monkeypatch)

    class BrokenStream:
        def read(self, _size):
            raise error

    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(BrokenStream(), 10, upload_dir=str(tmp_path))
    assert caught.value.code == code
    assert not (tmp_path / firmware_upload.PART_FILENAME).exists()


def test_storage_failure_removes_partial(monkeypatch, tmp_path):
    _allow_space(monkeypatch)
    monkeypatch.setattr(firmware_upload.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError()))
    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(io.BytesIO(b"data"), 4, upload_dir=str(tmp_path))
    assert caught.value.code == "storage_error"
    assert not (tmp_path / firmware_upload.PART_FILENAME).exists()
    assert not (tmp_path / firmware_upload.READY_FILENAME).exists()


def test_directory_sync_failure_removes_ready(monkeypatch, tmp_path):
    _allow_space(monkeypatch)
    real_fsync = firmware_upload.os.fsync
    calls = 0

    def fail_second_sync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(firmware_upload.os, "fsync", fail_second_sync)
    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(io.BytesIO(b"data"), 4, upload_dir=str(tmp_path))
    assert caught.value.code == "storage_error"
    assert not (tmp_path / firmware_upload.READY_FILENAME).exists()


def test_concurrent_upload_is_rejected(monkeypatch, tmp_path):
    _allow_space(monkeypatch)
    lock = threading.Lock()
    lock.acquire()
    try:
        with pytest.raises(firmware_upload.UploadFailure) as caught:
            firmware_upload.stage_upload(
                io.BytesIO(b"data"), 4, upload_dir=str(tmp_path), lock=lock
            )
    finally:
        lock.release()
    assert caught.value.status == 409


def test_missing_upload_directory_is_rejected(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(io.BytesIO(b"data"), 4, upload_dir=str(missing))
    assert caught.value.code == "storage_unavailable"


def test_active_install_rejects_upload_without_reading(monkeypatch, tmp_path):
    lock = tmp_path / "install.lock"
    lock.write_text("123\n")
    monkeypatch.setattr(firmware_upload, "INSTALL_LOCK_PATH", str(lock))
    stream = RecordingStream(b"data")
    with pytest.raises(firmware_upload.UploadFailure) as caught:
        firmware_upload.stage_upload(stream, 4, upload_dir=str(tmp_path))
    assert caught.value.code == "installation_active"
    assert stream.read_sizes == []
