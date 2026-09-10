"""Bounded, atomic staging of firmware uploads on the persistent data partition.

Firmware bytes are read directly from the HTTP request stream in fixed-size
chunks.  A partial upload is never installable: it is written to ``.part``,
flushed to stable storage, and atomically renamed to ``.ready`` only after the
declared byte count has been received.
"""

import hashlib
import json
import os
import shutil
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Optional, Protocol

UPLOAD_DIR = "/data/update/upload"
INSTALL_LOCK_PATH = "/run/firmware-update/install.lock"
PART_FILENAME = "firmware.swu.part"
READY_FILENAME = "firmware.swu.ready"
METADATA_FILENAME = "firmware.swu.json"

# The update package contains one compressed image for a 768 MiB root slot.  It
# must never need to be larger than the slot it will eventually populate.
MAX_FIRMWARE_BYTES = 768 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
FREE_SPACE_MARGIN_BYTES = 64 * 1024 * 1024
UPLOAD_IDLE_TIMEOUT_SECONDS = 120.0

_UPLOAD_LOCK = threading.Lock()


class ReadableStream(Protocol):
    """Minimal request-stream interface needed by the staging loop."""

    def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class UploadResult:
    """Successful staged-upload metadata returned to the HTTP layer."""

    bytes_received: int
    sha256: str


class UploadFailure(Exception):
    """A safe, structured upload failure suitable for an HTTP response."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _paths(upload_dir: str) -> tuple[str, str, str]:
    return (
        os.path.join(upload_dir, PART_FILENAME),
        os.path.join(upload_dir, READY_FILENAME),
        os.path.join(upload_dir, METADATA_FILENAME),
    )


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _open_partial(path: str):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    return os.fdopen(fd, "wb")


def _sync_directory(path: str) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _safe_filename(filename: str) -> str:
    """Return a bounded display-only filename with path/control data removed."""
    name = os.path.basename(filename.replace("\\", "/")).strip()
    name = "".join(character for character in name if character.isprintable())[:128]
    return name if name.lower().endswith(".swu") else "firmware.swu"


def _write_metadata(path: str, filename: str, length: int, sha256: str) -> None:
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".firmware-meta-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "filename": _safe_filename(filename),
                    "size": length,
                    "sha256": sha256,
                    "uploaded_at": int(time.time()),
                },
                handle,
                separators=(",", ":"),
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        _remove(temporary)


def _check_space(upload_dir: str, length: int) -> None:
    try:
        free = shutil.disk_usage(upload_dir).free
    except OSError as exc:
        raise UploadFailure(500, "storage_unavailable", "Firmware storage is unavailable.") from exc
    if free < length + FREE_SPACE_MARGIN_BYTES:
        raise UploadFailure(
            507,
            "insufficient_space",
            "There is not enough free space to stage this firmware file.",
        )


def stage_upload(
    stream: ReadableStream,
    length: int,
    *,
    filename: str = "firmware.swu",
    upload_dir: str = UPLOAD_DIR,
    lock: Optional[threading.Lock] = None,
) -> UploadResult:
    """Stream exactly ``length`` bytes into the fixed firmware staging path.

    At most :data:`CHUNK_BYTES` is requested from ``stream`` at once.  The
    caller remains responsible for validating HTTP headers and setting the
    socket's idle timeout.
    """
    if length <= 0:
        raise UploadFailure(400, "invalid_length", "A positive Content-Length is required.")
    if length > MAX_FIRMWARE_BYTES:
        raise UploadFailure(413, "firmware_too_large", "The firmware file is too large.")
    if not os.path.isdir(upload_dir):
        raise UploadFailure(500, "storage_unavailable", "Firmware storage is unavailable.")
    if os.path.exists(INSTALL_LOCK_PATH):
        raise UploadFailure(409, "installation_active", "A firmware installation is active.")

    active_lock = lock or _UPLOAD_LOCK
    if not active_lock.acquire(blocking=False):
        raise UploadFailure(409, "upload_active", "Another firmware upload is already active.")

    partial_path, ready_path, metadata_path = _paths(upload_dir)
    try:
        _check_space(upload_dir, length)
        _remove(partial_path)
        # A replacement attempt invalidates the previously staged package.  A
        # failed replacement can therefore never be mistaken for the new file.
        _remove(ready_path)
        _remove(metadata_path)

        digest = hashlib.sha256()
        received = 0
        try:
            with _open_partial(partial_path) as output:
                while received < length:
                    wanted = min(CHUNK_BYTES, length - received)
                    chunk = stream.read(wanted)
                    if not chunk:
                        raise UploadFailure(
                            400,
                            "short_upload",
                            "The firmware upload ended before all bytes were received.",
                        )
                    if len(chunk) > wanted:
                        raise UploadFailure(
                            400, "invalid_body", "The firmware request body is invalid."
                        )
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(partial_path, ready_path)
            result_sha256 = digest.hexdigest()
            _write_metadata(metadata_path, filename, received, result_sha256)
            try:
                _sync_directory(upload_dir)
            except OSError:
                # Do not report success with a rename that was not durably
                # committed. Remove the package so it cannot be installed.
                _remove(ready_path)
                _remove(metadata_path)
                raise
        except UploadFailure:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise UploadFailure(408, "upload_timeout", "The firmware upload timed out.") from exc
        except (ConnectionError, BrokenPipeError) as exc:
            raise UploadFailure(
                400, "upload_interrupted", "The firmware upload was interrupted."
            ) from exc
        except OSError as exc:
            _remove(ready_path)
            _remove(metadata_path)
            raise UploadFailure(
                500, "storage_error", "The firmware file could not be stored."
            ) from exc

        return UploadResult(bytes_received=received, sha256=result_sha256)
    finally:
        _remove(partial_path)
        active_lock.release()
