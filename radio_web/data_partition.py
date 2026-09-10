"""Safely expand the final persistent-data partition on the appliance SD card."""

import ctypes
import errno
import fcntl
import logging
import os
import re
import struct
import threading
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger("radio_web.data_partition")

MOUNTINFO = "/proc/self/mountinfo"
SYS_CLASS_BLOCK = "/sys/class/block"
DATA_MOUNT = "/data"
STATE_DIR = "/data/update/data-resize"
PENDING_MARKER = os.path.join(STATE_DIR, "pending")
MBR_BACKUP = os.path.join(STATE_DIR, "mbr.backup")

SECTOR_SIZE = 512
MBR_SIZE = 512
MBR_PARTITION_OFFSET = 446
MBR_ENTRY_SIZE = 16
MBR_SIGNATURE = b"\x55\xaa"
DATA_PARTITION_NUMBER = 4
MIN_GROW_SECTORS = 2048
BLKPG = 0x1269
BLKPG_RESIZE_PARTITION = 3
EXT4_IOC_RESIZE_FS = 0x40086610
_EXPAND_LOCK = threading.Lock()


@dataclass(frozen=True)
class Layout:
    disk: str
    partition: str
    disk_sectors: int
    partition_start: int
    partition_sectors: int

    @property
    def target_sectors(self) -> int:
        return self.disk_sectors - self.partition_start

    @property
    def available_sectors(self) -> int:
        return max(0, self.target_sectors - self.partition_sectors)


class _BlkpgPartition(ctypes.Structure):
    _fields_ = [
        ("start", ctypes.c_longlong),
        ("length", ctypes.c_longlong),
        ("pno", ctypes.c_int),
        ("devname", ctypes.c_char * 64),
        ("volname", ctypes.c_char * 64),
    ]


class _BlkpgIoctlArg(ctypes.Structure):
    _fields_ = [
        ("op", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("datalen", ctypes.c_int),
        ("data", ctypes.c_void_p),
    ]


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _data_source(mountinfo: str = MOUNTINFO) -> str:
    with open(mountinfo, "r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip("\n").split()
            if len(fields) < 10 or fields[4] != DATA_MOUNT or "-" not in fields:
                continue
            separator = fields.index("-")
            if fields[separator + 1] != "ext4":
                raise ValueError("The data filesystem is not ext4.")
            return os.path.realpath(fields[separator + 2])
    raise ValueError("Could not identify the data filesystem.")


def discover_layout(
    mountinfo: str = MOUNTINFO,
    sys_class_block: str = SYS_CLASS_BLOCK,
) -> Layout:
    partition = _data_source(mountinfo)
    name = os.path.basename(partition)
    match = re.fullmatch(r"(mmcblk[0-9]+)p4", name)
    if not match:
        raise ValueError("Data is not on SD-card partition 4.")
    disk_name = match.group(1)
    disk_dir = os.path.join(sys_class_block, disk_name)
    partition_dir = os.path.join(sys_class_block, name)
    logical_sector = int(_read_text(os.path.join(disk_dir, "queue/logical_block_size")))
    if logical_sector != SECTOR_SIZE:
        raise ValueError("The SD card does not use 512-byte logical sectors.")
    return Layout(
        disk=os.path.join(os.path.dirname(partition), disk_name),
        partition=partition,
        disk_sectors=int(_read_text(os.path.join(disk_dir, "size"))),
        partition_start=int(_read_text(os.path.join(partition_dir, "start"))),
        partition_sectors=int(_read_text(os.path.join(partition_dir, "size"))),
    )


def _read_mbr(disk: str) -> bytes:
    with open(disk, "rb", buffering=0) as handle:
        data = handle.read(MBR_SIZE)
    if len(data) != MBR_SIZE or data[510:512] != MBR_SIGNATURE:
        raise ValueError("The SD card does not have a valid DOS/MBR partition table.")
    return data


def _entry(mbr: bytes, number: int) -> Tuple[int, int, int]:
    offset = MBR_PARTITION_OFFSET + (number - 1) * MBR_ENTRY_SIZE
    partition_type = mbr[offset + 4]
    start, sectors = struct.unpack_from("<II", mbr, offset + 8)
    return partition_type, start, sectors


def validate_mbr(layout: Layout, mbr: bytes) -> None:
    partition_type, start, sectors = _entry(mbr, DATA_PARTITION_NUMBER)
    if partition_type != 0x83:
        raise ValueError("Data partition is not an MBR Linux partition.")
    if start != layout.partition_start:
        raise ValueError("Data partition start differs between the kernel and MBR.")
    if sectors not in (layout.partition_sectors, layout.target_sectors):
        raise ValueError("Data partition size differs between the kernel and MBR.")
    if start <= 0 or start >= layout.disk_sectors:
        raise ValueError("Data partition boundaries are invalid.")
    previous_end = 0
    for number in range(1, DATA_PARTITION_NUMBER):
        kind, other_start, other_sectors = _entry(mbr, number)
        if kind == 0 or other_sectors == 0:
            raise ValueError("The installation image must contain partitions 1 through 4.")
        if other_start < previous_end or other_start + other_sectors > start:
            raise ValueError("Partitions overlap or are out of order.")
        previous_end = other_start + other_sectors


def expanded_mbr(layout: Layout, mbr: bytes) -> bytes:
    """Grow only p4; p1-p3 and the p4 start remain byte-for-byte unchanged."""
    validate_mbr(layout, mbr)
    if layout.target_sectors > 0xFFFFFFFF:
        raise ValueError("SD card is too large for the MBR partition table.")
    result = bytearray(mbr)
    offset = MBR_PARTITION_OFFSET + (DATA_PARTITION_NUMBER - 1) * MBR_ENTRY_SIZE
    result[offset + 5 : offset + 8] = b"\xfe\xff\xff"
    struct.pack_into("<I", result, offset + 12, layout.target_sectors)
    return bytes(result)


def _atomic_write(path: str, data: bytes, mode: int = 0o600) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "wb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_mbr(disk: str, mbr: bytes) -> None:
    with open(disk, "r+b", buffering=0) as handle:
        handle.write(mbr)
        handle.flush()
        os.fsync(handle.fileno())


def _resize_kernel_partition(layout: Layout) -> None:
    partition = _BlkpgPartition(
        start=layout.partition_start * SECTOR_SIZE,
        length=layout.target_sectors * SECTOR_SIZE,
        pno=DATA_PARTITION_NUMBER,
    )
    argument = _BlkpgIoctlArg(
        op=BLKPG_RESIZE_PARTITION,
        datalen=ctypes.sizeof(partition),
        data=ctypes.addressof(partition),
    )
    disk_fd = os.open(layout.disk, os.O_RDONLY)
    try:
        result = ctypes.CDLL(None, use_errno=True).ioctl(disk_fd, BLKPG, ctypes.byref(argument))
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
    finally:
        os.close(disk_fd)


def _ext4_geometry(partition: str) -> Tuple[int, int]:
    with open(partition, "rb", buffering=0) as handle:
        handle.seek(1024)
        superblock = handle.read(1024)
    if len(superblock) != 1024 or struct.unpack_from("<H", superblock, 56)[0] != 0xEF53:
        raise ValueError("Data partition does not contain an ext4 filesystem.")
    block_size = 1024 << struct.unpack_from("<I", superblock, 24)[0]
    blocks = struct.unpack_from("<I", superblock, 4)[0]
    if struct.unpack_from("<I", superblock, 96)[0] & 0x80:
        blocks |= struct.unpack_from("<I", superblock, 336)[0] << 32
    return block_size, blocks


def _finish(layout: Layout) -> None:
    block_size, current_blocks = _ext4_geometry(layout.partition)
    target_blocks = (layout.target_sectors * SECTOR_SIZE) // block_size
    if current_blocks < target_blocks:
        data_fd = os.open(DATA_MOUNT, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            fcntl.ioctl(data_fd, EXT4_IOC_RESIZE_FS, struct.pack("=Q", target_blocks))
        finally:
            os.close(data_fd)
    _block_size, blocks = _ext4_geometry(layout.partition)
    if blocks < target_blocks:
        raise OSError(errno.EIO, "data filesystem did not reach the requested size")
    try:
        os.unlink(PENDING_MARKER)
    except FileNotFoundError:
        pass


def _expand_locked() -> Tuple[bool, str]:
    try:
        layout = discover_layout()
        mbr = _read_mbr(layout.disk)
        validate_mbr(layout, mbr)
        if layout.available_sectors < MIN_GROW_SECTORS:
            _finish(layout)
            return True, "The data partition already uses the available SD-card space."
        _atomic_write(PENDING_MARKER, b"pending\n", mode=0o600)
        if not os.path.exists(MBR_BACKUP):
            _atomic_write(MBR_BACKUP, mbr)
        updated = expanded_mbr(layout, mbr)
        if (
            updated[MBR_PARTITION_OFFSET : MBR_PARTITION_OFFSET + 3 * MBR_ENTRY_SIZE]
            != mbr[MBR_PARTITION_OFFSET : MBR_PARTITION_OFFSET + 3 * MBR_ENTRY_SIZE]
        ):
            raise ValueError("Refusing to modify firmware partitions.")
        _write_mbr(layout.disk, updated)
        try:
            _resize_kernel_partition(layout)
            _finish(layout)
        except (OSError, ValueError) as exc:
            logger.warning("live data expansion deferred until reboot: %s", exc)
            return True, "Data partition expanded; reboot required to finish."
        return True, "Data partition expanded successfully."
    except (OSError, ValueError) as exc:
        logger.error("data partition expansion refused: %s", exc)
        return False, "Could not safely expand the data partition: " + str(exc)


def expand() -> Tuple[bool, str]:
    with _EXPAND_LOCK:
        return _expand_locked()


def main() -> int:
    ok, message = expand()
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
