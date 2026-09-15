"""Unit tests for the safe persistent-data partition expansion helpers.

These exercise the pure decision/parse logic in ``radio_web.data_partition``
without touching a real block device: the sysfs/mountinfo discovery, the
MBR read/validate/expand boundary rules, the atomic-write helper, and the
``_expand_locked`` orchestration branches (already-full, refuse-to-modify,
live-resize-deferred, and hard failure). Kernel ioctls and raw disk writes are
monkeypatched so the whole flow runs on any developer/CI machine.
"""

import struct

import pytest

from radio_web import data_partition as dp

# --- Fake sysfs / mountinfo layout ------------------------------------------


def _build_sysfs(
    tmp_path,
    *,
    disk="mmcblk0",
    logical=512,
    disk_sectors=20_000,
    part_start=5000,
    part_sectors=1000,
):
    """Create a fake ``/sys/class/block`` tree and a matching mountinfo file."""
    sys_block = tmp_path / "sys" / "class" / "block"
    disk_dir = sys_block / disk
    (disk_dir / "queue").mkdir(parents=True)
    (disk_dir / "queue" / "logical_block_size").write_text(f"{logical}\n")
    (disk_dir / "size").write_text(f"{disk_sectors}\n")
    part_dir = sys_block / f"{disk}p4"
    part_dir.mkdir(parents=True)
    (part_dir / "start").write_text(f"{part_start}\n")
    (part_dir / "size").write_text(f"{part_sectors}\n")

    dev = tmp_path / "dev"
    dev.mkdir()
    part_device = dev / f"{disk}p4"
    part_device.write_bytes(b"")
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "36 35 0:32 / /data rw,relatime shared:1 - ext4 " f"{part_device} rw,data=ordered\n"
    )
    return str(mountinfo), str(sys_block)


def _mbr(entries, signature=dp.MBR_SIGNATURE):
    data = bytearray(dp.MBR_SIZE)
    data[510:512] = signature
    for number, kind, start, sectors in entries:
        offset = dp.MBR_PARTITION_OFFSET + (number - 1) * dp.MBR_ENTRY_SIZE
        data[offset + 4] = kind
        struct.pack_into("<II", data, offset + 8, start, sectors)
    return bytes(data)


def _valid_entries(part_start=5000, part_sectors=1000):
    return [
        (1, 0x0C, 100, 900),
        (2, 0x83, 1000, 2000),
        (3, 0x83, 3000, 2000),
        (4, 0x83, part_start, part_sectors),
    ]


def _layout(part_start=5000, part_sectors=1000, disk_sectors=20_000):
    return dp.Layout(
        disk="/dev/mmcblk0",
        partition="/dev/mmcblk0p4",
        disk_sectors=disk_sectors,
        partition_start=part_start,
        partition_sectors=part_sectors,
    )


# --- Layout properties -------------------------------------------------------


def test_layout_available_and_target_sectors():
    layout = _layout(part_start=5000, part_sectors=1000, disk_sectors=20_000)
    assert layout.target_sectors == 15_000
    assert layout.available_sectors == 14_000
    full = _layout(part_start=5000, part_sectors=15_000, disk_sectors=20_000)
    assert full.available_sectors == 0
    over = _layout(part_start=5000, part_sectors=16_000, disk_sectors=20_000)
    assert over.available_sectors == 0  # clamped at zero, never negative


# --- discover_layout ---------------------------------------------------------


def test_discover_layout_reads_sysfs(tmp_path):
    mountinfo, sys_block = _build_sysfs(tmp_path)
    layout = dp.discover_layout(mountinfo=mountinfo, sys_class_block=sys_block)
    assert layout.partition.endswith("mmcblk0p4")
    assert layout.disk.endswith("mmcblk0")
    assert layout.disk_sectors == 20_000
    assert layout.partition_start == 5000
    assert layout.partition_sectors == 1000


def test_discover_layout_rejects_non_partition_four(tmp_path):
    mountinfo, sys_block = _build_sysfs(tmp_path, disk="mmcblk0")
    # Point the mount at p2 instead of p4 so the fullmatch fails.
    text = (tmp_path / "mountinfo").read_text().replace("mmcblk0p4", "mmcblk0p2")
    (tmp_path / "mountinfo").write_text(text)
    with pytest.raises(ValueError, match="partition 4"):
        dp.discover_layout(mountinfo=mountinfo, sys_class_block=sys_block)


def test_discover_layout_rejects_non_512_sectors(tmp_path):
    mountinfo, sys_block = _build_sysfs(tmp_path, logical=4096)
    with pytest.raises(ValueError, match="512-byte"):
        dp.discover_layout(mountinfo=mountinfo, sys_class_block=sys_block)


def test_data_source_rejects_non_ext4(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("36 35 0:32 / /data rw - vfat /dev/mmcblk0p4 rw\n")
    with pytest.raises(ValueError, match="not ext4"):
        dp._data_source(str(mountinfo))


def test_data_source_when_data_not_present(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("36 35 0:32 / / rw - ext4 /dev/mmcblk0p2 rw\n")
    with pytest.raises(ValueError, match="Could not identify"):
        dp._data_source(str(mountinfo))


# --- MBR read / validate / expand -------------------------------------------


def test_read_mbr_rejects_bad_signature(tmp_path):
    disk = tmp_path / "disk.img"
    disk.write_bytes(_mbr(_valid_entries(), signature=b"\x00\x00"))
    with pytest.raises(ValueError, match="valid DOS/MBR"):
        dp._read_mbr(str(disk))


def test_read_mbr_reads_valid_table(tmp_path):
    disk = tmp_path / "disk.img"
    disk.write_bytes(_mbr(_valid_entries()))
    assert dp._read_mbr(str(disk))[510:512] == dp.MBR_SIGNATURE


def test_validate_mbr_accepts_consistent_table():
    dp.validate_mbr(_layout(), _mbr(_valid_entries()))  # does not raise


def test_validate_mbr_rejects_non_linux_data_partition():
    entries = _valid_entries()
    entries[3] = (4, 0x0C, 5000, 1000)  # not 0x83
    with pytest.raises(ValueError, match="Linux partition"):
        dp.validate_mbr(_layout(), _mbr(entries))


def test_validate_mbr_rejects_start_mismatch():
    entries = _valid_entries(part_start=6000)
    with pytest.raises(ValueError, match="start differs"):
        dp.validate_mbr(_layout(part_start=5000), _mbr(entries))


def test_validate_mbr_rejects_size_mismatch():
    entries = _valid_entries(part_start=5000, part_sectors=1234)
    with pytest.raises(ValueError, match="size differs"):
        dp.validate_mbr(_layout(part_start=5000, part_sectors=1000), _mbr(entries))


def test_validate_mbr_rejects_missing_leading_partition():
    entries = _valid_entries()
    entries[0] = (1, 0, 0, 0)  # empty partition 1
    with pytest.raises(ValueError, match="partitions 1 through 4"):
        dp.validate_mbr(_layout(), _mbr(entries))


def test_validate_mbr_rejects_overlap():
    entries = _valid_entries()
    entries[2] = (3, 0x83, 3000, 5000)  # p3 overruns into p4 start
    with pytest.raises(ValueError, match="overlap"):
        dp.validate_mbr(_layout(), _mbr(entries))


def test_expanded_mbr_grows_only_partition_four():
    layout = _layout(part_start=5000, part_sectors=1000, disk_sectors=20_000)
    original = _mbr(_valid_entries())
    updated = dp.expanded_mbr(layout, original)
    head = dp.MBR_PARTITION_OFFSET
    end = head + 3 * dp.MBR_ENTRY_SIZE
    assert updated[head:end] == original[head:end]  # p1-p3 untouched
    _kind, start, sectors = dp._entry(updated, 4)
    assert start == 5000
    assert sectors == layout.target_sectors


def test_expanded_mbr_rejects_oversized_disk():
    # target_sectors = disk_sectors - partition_start must exceed 0xFFFFFFFF,
    # while the MBR still validates (p1-p3 in order, p4 start/size consistent).
    part_start = 5000
    disk_sectors = part_start + 0x1_0000_0001
    layout = _layout(part_start=part_start, part_sectors=1000, disk_sectors=disk_sectors)
    with pytest.raises(ValueError, match="too large"):
        dp.expanded_mbr(layout, _mbr(_valid_entries(part_start=part_start, part_sectors=1000)))


# --- _atomic_write -----------------------------------------------------------


def test_atomic_write_creates_file_with_mode(tmp_path):
    target = tmp_path / "state" / "marker"
    dp._atomic_write(str(target), b"pending\n", mode=0o600)
    assert target.read_bytes() == b"pending\n"
    assert (target.stat().st_mode & 0o777) == 0o600
    assert not (tmp_path / "state" / "marker.tmp").exists()


# --- _expand_locked orchestration -------------------------------------------


@pytest.fixture
def expand_env(tmp_path, monkeypatch):
    """A layout whose disk is a real image file + stubbed kernel operations."""
    disk = tmp_path / "disk.img"
    disk.write_bytes(_mbr(_valid_entries(part_start=5000, part_sectors=1000)))
    layout = dp.Layout(
        disk=str(disk),
        partition=str(tmp_path / "mmcblk0p4"),
        disk_sectors=20_000,
        partition_start=5000,
        partition_sectors=1000,
    )
    monkeypatch.setattr(dp, "discover_layout", lambda: layout)
    monkeypatch.setattr(dp, "PENDING_MARKER", str(tmp_path / "state" / "pending"))
    monkeypatch.setattr(dp, "MBR_BACKUP", str(tmp_path / "state" / "mbr.backup"))
    monkeypatch.setattr(dp, "_finish", lambda layout: None)
    return layout, disk, tmp_path


def test_expand_when_already_full_finishes_without_writing(monkeypatch, expand_env):
    layout, _disk, _tmp = expand_env
    full = dp.Layout(
        disk=layout.disk,
        partition=layout.partition,
        disk_sectors=layout.partition_start + layout.partition_sectors,
        partition_start=layout.partition_start,
        partition_sectors=layout.partition_sectors,
    )
    monkeypatch.setattr(dp, "discover_layout", lambda: full)
    ok, message = dp.expand()
    assert ok
    assert "already uses" in message


def test_expand_grows_and_defers_when_kernel_resize_fails(monkeypatch, expand_env):
    layout, disk, _tmp = expand_env

    def _boom(_layout):
        raise OSError("busy")

    monkeypatch.setattr(dp, "_resize_kernel_partition", _boom)
    ok, message = dp.expand()
    assert ok
    assert "reboot required" in message
    # The MBR on disk was rewritten to grow p4 to fill the disk.
    _kind, start, sectors = dp._entry(dp._read_mbr(disk.as_posix()), 4)
    assert start == 5000
    assert sectors == layout.target_sectors


def test_expand_succeeds_when_kernel_resize_ok(monkeypatch, expand_env):
    monkeypatch.setattr(dp, "_resize_kernel_partition", lambda _layout: None)
    ok, message = dp.expand()
    assert ok
    assert "successfully" in message


def test_expand_refuses_when_firmware_entries_would_change(monkeypatch, expand_env):
    real_expanded = dp.expanded_mbr

    def _tamper(layout, mbr):
        result = bytearray(real_expanded(layout, mbr))
        # Corrupt partition 1 in the produced MBR to trip the safety check.
        result[dp.MBR_PARTITION_OFFSET + 8] ^= 0xFF
        return bytes(result)

    monkeypatch.setattr(dp, "expanded_mbr", _tamper)
    ok, message = dp.expand()
    assert not ok
    assert "firmware partitions" in message


def test_expand_reports_failure_on_bad_mbr(expand_env):
    _layout_obj, disk, _tmp = expand_env
    disk.write_bytes(b"\x00" * dp.MBR_SIZE)  # invalid signature
    ok, message = dp.expand()
    assert not ok
    assert "Could not safely expand" in message


def test_main_returns_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(dp, "expand", lambda: (True, "done"))
    assert dp.main() == 0
    monkeypatch.setattr(dp, "expand", lambda: (False, "nope"))
    assert dp.main() == 1
    assert "nope" in capsys.readouterr().out
