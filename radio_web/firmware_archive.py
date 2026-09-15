"""Streaming validation for the project's restricted SWUpdate archive format."""

import hashlib
import re
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple

from . import persistent_config

MAX_MANIFEST_BYTES = 256 * 1024
HARDWARE_REVISION = "1"
PAYLOAD_NAME = "rootfs.ext4.gz"

_VERSION_RE = re.compile(r'\bversion\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*;')
_SIZE_RE = re.compile(r"\bsize\s*=\s*([0-9]+)\s*;")
_HASH_RE = re.compile(r'\bsha256\s*=\s*"([0-9a-f]{64})"\s*;')
_DEVICE_RE = re.compile(r'\bdevice\s*=\s*"([^"]+)"\s*;')
_PERSISTENT_SCHEMA_RE = re.compile(r"\bpersistent_schema\s*=\s*([0-9]+)\s*;")
_PERSISTENT_READER_RE = re.compile(r"\bpersistent_reader_schema\s*=\s*([0-9]+)\s*;")
_ROLLBACK_READER_RE = re.compile(r"\bminimum_rollback_reader_schema\s*=\s*([0-9]+)\s*;")


class FirmwareError(Exception):
    """Expected, safely displayable firmware-operation failure."""


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise FirmwareError("The firmware archive is truncated.")
    return data


def _stream_payload(stream: BinaryIO, length: int) -> Tuple[str, int]:
    digest = hashlib.sha256()
    checksum = 0
    remaining = length
    while remaining:
        chunk = _read_exact(stream, min(1024 * 1024, remaining))
        digest.update(chunk)
        checksum = (checksum + sum(chunk)) & 0xFFFFFFFF
        remaining -= len(chunk)
    return digest.hexdigest(), checksum


def inspect_archive(path: Path, expected_device: Optional[str] = None) -> Dict[str, Any]:
    """Stream and validate the project's deliberately restricted SWU format."""
    names = []
    manifest = ""
    payload_size = 0
    payload_hash = ""
    with path.open("rb") as archive:
        while True:
            header = _read_exact(archive, 110)
            if header[:6] not in (b"070701", b"070702"):
                raise FirmwareError("The firmware archive has an invalid CPIO header.")
            try:
                fields = [int(header[pos : pos + 8], 16) for pos in range(6, 110, 8)]
            except ValueError as exc:
                raise FirmwareError("The firmware archive has invalid CPIO metadata.") from exc
            size, name_size, expected_sum = fields[6], fields[11], fields[12]
            if name_size < 2 or name_size > 256:
                raise FirmwareError("The firmware archive has an invalid member name.")
            name_bytes = _read_exact(archive, name_size)
            if not name_bytes.endswith(b"\0"):
                raise FirmwareError("The firmware archive has an invalid member name.")
            try:
                name = name_bytes[:-1].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FirmwareError("The firmware archive has an invalid member name.") from exc
            _read_exact(archive, (-(110 + name_size)) % 4)
            if name == "TRAILER!!!":
                if size != 0:
                    raise FirmwareError("The firmware archive has an invalid trailer.")
                break
            if name.startswith("/") or ".." in Path(name).parts or name in names:
                raise FirmwareError("The firmware archive contains an unsafe member name.")
            names.append(name)
            if name == "sw-description" and len(names) == 1:
                if size > MAX_MANIFEST_BYTES:
                    raise FirmwareError("The firmware manifest is too large.")
                data = _read_exact(archive, size)
                actual_sum = sum(data) & 0xFFFFFFFF
                try:
                    manifest = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise FirmwareError("The firmware manifest is not valid UTF-8.") from exc
            elif name == PAYLOAD_NAME and len(names) == 2:
                payload_size = size
                payload_hash, actual_sum = _stream_payload(archive, size)
            else:
                raise FirmwareError("The firmware archive contains an unexpected member.")
            if header[:6] == b"070702" and actual_sum != expected_sum:
                raise FirmwareError("The firmware archive member checksum is invalid.")
            _read_exact(archive, (-size) % 4)
        if names != ["sw-description", PAYLOAD_NAME]:
            raise FirmwareError("The firmware archive has unexpected contents.")
        remainder = archive.read()
        if any(remainder):
            raise FirmwareError("The firmware archive has data after its trailer.")

    versions = _VERSION_RE.findall(manifest)
    sizes = _SIZE_RE.findall(manifest)
    hashes = _HASH_RE.findall(manifest)
    devices = _DEVICE_RE.findall(manifest)
    persistent_schemas = _PERSISTENT_SCHEMA_RE.findall(manifest)
    persistent_readers = _PERSISTENT_READER_RE.findall(manifest)
    rollback_readers = _ROLLBACK_READER_RE.findall(manifest)
    if len(versions) != 1:
        raise FirmwareError("The firmware manifest has an invalid version.")
    if sizes != [str(payload_size), str(payload_size)]:
        raise FirmwareError("The firmware payload size does not match its manifest.")
    if hashes != [payload_hash, payload_hash]:
        raise FirmwareError("The firmware payload digest does not match its manifest.")
    if manifest.count(f'hardware-compatibility: [ "{HARDWARE_REVISION}" ];') != 1:
        raise FirmwareError("The firmware is not compatible with this radio.")
    if not (len(persistent_schemas) == len(persistent_readers) == len(rollback_readers) == 1):
        raise FirmwareError("The firmware persistent-data contract is invalid.")
    target_schema = int(persistent_schemas[0])
    target_reader = int(persistent_readers[0])
    rollback_reader = int(rollback_readers[0])
    if target_schema < 1 or target_reader < 1 or rollback_reader > target_schema:
        raise FirmwareError("The firmware persistent-data contract is invalid.")
    try:
        current = persistent_config.compatibility_state()
    except ValueError as exc:
        raise FirmwareError(str(exc)) from exc
    if target_reader < current["minimum_reader_schema"]:
        raise FirmwareError("The firmware cannot read the current persistent configuration.")
    if persistent_config.CURRENT_SCHEMA < rollback_reader:
        raise FirmwareError(
            "This update requires an irreversible configuration migration; rollback would be unsafe."
        )
    if devices != ["/dev/mmcblk0p2", "/dev/mmcblk0p3"]:
        raise FirmwareError("The firmware manifest contains an unapproved target device.")
    slot_parts = manifest.split("slot-b:", 1)
    if len(slot_parts) != 2:
        raise FirmwareError("The firmware manifest does not define both firmware slots.")
    slot_a, slot_b = slot_parts
    if _DEVICE_RE.findall(slot_a) != ["/dev/mmcblk0p2"]:
        raise FirmwareError("The slot A selection does not target firmware slot A.")
    if _DEVICE_RE.findall(slot_b) != ["/dev/mmcblk0p3"]:
        raise FirmwareError("The slot B selection does not target firmware slot B.")
    required_boot_state = {
        "A": (
            '{ name = "previous_slot"; value = "B"; }',
            '{ name = "active_slot"; value = "A"; }',
        ),
        "B": (
            '{ name = "previous_slot"; value = "A"; }',
            '{ name = "active_slot"; value = "B"; }',
        ),
    }
    for marker in required_boot_state["A"]:
        if slot_a.count(marker) != 1:
            raise FirmwareError("The slot A trial-boot state is invalid.")
    for marker in required_boot_state["B"]:
        if slot_b.count(marker) != 1:
            raise FirmwareError("The slot B trial-boot state is invalid.")
    for marker in (
        '{ name = "bootcount"; value = "0"; }',
        '{ name = "bootlimit"; value = "3"; }',
        '{ name = "upgrade_available"; value = "1"; }',
    ):
        if manifest.count(marker) != 2:
            raise FirmwareError("The firmware trial-boot state is incomplete.")
    if manifest.count(f'filename = "{PAYLOAD_NAME}";') != 2:
        raise FirmwareError("The firmware manifest has invalid payload references.")
    for forbidden in ("scripts:", "files:", "partitions:", "bootloader:"):
        if forbidden in manifest:
            raise FirmwareError("The firmware manifest requests an unsupported operation.")
    if expected_device is not None and expected_device not in devices:
        raise FirmwareError("The firmware does not contain the inactive slot target.")
    return {
        "version": versions[0],
        "payload_size": payload_size,
        "payload_sha256": payload_hash,
        "persistent_schema": target_schema,
        "persistent_reader_schema": target_reader,
        "minimum_rollback_reader_schema": rollback_reader,
    }
