"""Static and unit checks for the firmware-update installation image layout."""

import re
import struct
from pathlib import Path

import pytest

from radio_web import data_partition

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "buildroot" / "external" / "board" / "radio"
GENIMAGE = BOARD / "genimage.cfg.in"
LAYOUT = BOARD / "image-layout.conf"
POST_IMAGE = BOARD / "post-image.sh"
POST_BUILD = BOARD / "post-build.sh"
INITTAB = BOARD / "rootfs-overlay" / "etc" / "inittab"
PERSISTENT_PATHS = BOARD / "rootfs-overlay" / "usr" / "sbin" / "radio-persistent-paths"
PERSISTENT_CONFIG = BOARD / "rootfs-overlay" / "usr" / "sbin" / "radio-persistent-config"
PERSISTENT_BOOT = BOARD / "rootfs-overlay" / "usr" / "sbin" / "radio-persistent-boot"
CHRONY_CONFIG = BOARD / "rootfs-overlay" / "etc" / "chrony.conf"
ARTWORK_CONFIG = BOARD / "rootfs-overlay" / "etc" / "radio" / "artwork.ini"
PERSISTENT_DOC = ROOT / "doc" / "persistent-data.md"


def _layout_values():
    values = {}
    for line in LAYOUT.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _mbr(entries):
    data = bytearray(512)
    data[510:512] = b"\x55\xaa"
    for number, kind, start, sectors in entries:
        offset = 446 + (number - 1) * 16
        data[offset + 4] = kind
        struct.pack_into("<II", data, offset + 8, start, sectors)
    return bytes(data)


def _data_layout(sectors=1000):
    return data_partition.Layout(
        disk="/dev/mmcblk0",
        partition="/dev/mmcblk0p4",
        disk_sectors=20_000,
        partition_start=5000,
        partition_sectors=sectors,
    )


def test_genimage_has_fixed_four_partition_ab_layout():
    text = GENIMAGE.read_text(encoding="utf-8")
    partitions = re.findall(r"^\s*partition ([\w-]+) \{", text, re.MULTILINE)
    assert partitions == ["boot", "rootfs-a", "rootfs-b", "data"]
    assert 'partition-table-type = "dos"' in text
    assert "offset = 8M" in text
    assert text.count("size = 768M") == 2
    assert "size = 128M" in text
    assert 'image = "rootfs-a.ext4"' in text
    assert 'image = "rootfs-b.ext4"' in text
    assert 'image = "data.ext4"' in text


def test_partition_boundaries_are_ordered_non_overlapping_and_match_layout():
    values = _layout_values()
    reserved = int(values["RESERVED_SIZE"])
    boot = int(values["BOOT_SIZE"])
    slot = int(values["ROOTFS_SLOT_SIZE"])
    data = int(values["DATA_SIZE"])
    boundaries = [
        (reserved, reserved + boot),
        (reserved + boot, reserved + boot + slot),
        (reserved + boot + slot, reserved + boot + 2 * slot),
        (reserved + boot + 2 * slot, reserved + boot + 2 * slot + data),
    ]
    assert boundaries == sorted(boundaries)
    assert all(end > start for start, end in boundaries)
    assert all(left[1] <= right[0] for left, right in zip(boundaries, boundaries[1:]))
    assert values["ROOTFS_A_LABEL"] != values["ROOTFS_B_LABEL"]
    text = GENIMAGE.read_text(encoding="utf-8")
    assert f"offset = {reserved // (1024 * 1024)}M" in text
    assert text.count(f"size = {slot // (1024 * 1024)}M") == 2


def test_environment_regions_are_redundant_and_outside_partitions():
    values = _layout_values()
    reserved = int(values["RESERVED_SIZE"])
    size = int(values["UBOOT_ENV_SIZE"])
    offsets = [int(values["UBOOT_ENV_OFFSET"]), int(values["UBOOT_ENV_REDUND_OFFSET"])]
    assert offsets[0] != offsets[1]
    assert all(offset >= 512 and offset + size <= reserved for offset in offsets)
    assert offsets[0] + size <= offsets[1] or offsets[1] + size <= offsets[0]


def test_filesystem_labels_and_uuids_are_distinct_and_deterministic():
    values = _layout_values()
    labels = {values[key] for key in ("ROOTFS_A_LABEL", "ROOTFS_B_LABEL", "DATA_LABEL")}
    uuids = {values[key] for key in ("ROOTFS_A_UUID", "ROOTFS_B_UUID", "DATA_UUID")}
    assert len(labels) == 3
    assert len(uuids) == 3
    assert all(re.fullmatch(r"[0-9a-f-]{36}", value) for value in uuids)


def test_image_builder_creates_both_slots_and_seed_data():
    text = POST_IMAGE.read_text(encoding="utf-8")
    assert 'cp -f "${BINARIES_DIR}/rootfs.ext4" "$image"' in text
    assert 'ROOTFS_A="${BINARIES_DIR}/rootfs-a.ext4"' in text
    assert 'ROOTFS_B="${BINARIES_DIR}/rootfs-b.ext4"' in text
    assert 'DATA_IMAGE="${BINARIES_DIR}/data.ext4"' in text
    assert "schema-version" in text
    assert "history.json" in text
    assert "for path in /radio /radio/logos /update/upload" in text
    assert "/network/wpa_supplicant.conf /identity" in text


def test_slot_owned_kernel_and_stable_loader_assets_are_assembled_in_their_layers():
    post_image = POST_IMAGE.read_text(encoding="utf-8")
    boot = (BOARD / "boot.cmd").read_text(encoding="utf-8")
    assert 'cp -f "${BINARIES_DIR}/rootfs.ext4" "$image"' in post_image
    assert "ext4load mmc 0:${rootpart} ${kernel_addr_r} /boot/zImage" in boot
    assert 'for f in "${BINARIES_DIR}"/*.dtb "${BINARIES_DIR}"/rpi-firmware/*' in post_image
    assert 'cp -f "${BOARD_DIR}/cmdline.txt" "${BINARIES_DIR}/cmdline.txt"' not in post_image
    assert 'FILES+=( "u-boot.bin" "boot.scr" )' in post_image
    assert '"${BINARIES_DIR}"/rpi-firmware/*' in post_image


def test_seed_data_ownership_and_modes_match_the_privilege_boundary():
    text = POST_IMAGE.read_text(encoding="utf-8")
    assert "for path in /radio /radio/logos /update/upload" in text
    assert 'set_data_inode "$path" uid 601' in text
    assert "set_data_inode /update/upload mode 040750" in text
    assert "set_data_inode /update/queue mode 040700" in text
    assert "set_data_inode /update/config-backups mode 040700" in text
    assert "set_data_inode /update/history.json mode 0100600" in text
    assert 'chmod 0600 "$DATA_ROOT/network/wpa_supplicant.conf"' in text
    assert "set_data_inode /radio/artwork.ini uid 601" in text
    assert "set_data_inode /radio/artwork.ini gid 601" in text
    assert "set_data_inode /radio/artwork.ini mode 0100644" in text


def test_online_artwork_ships_disabled_and_is_seeded_as_managed_config():
    assert ARTWORK_CONFIG.read_text(encoding="utf-8") == (
        "[online_artwork]\nenabled = false\nprovider = musicbrainz\n"
    )
    device_table = (BOARD / "device_table.txt").read_text(encoding="utf-8")
    assert "/etc/radio/artwork.ini   f       644     601    601" in device_table
    assert 'cp -a "${TARGET_DIR}/etc/radio/." "$DATA_ROOT/radio/"' in POST_IMAGE.read_text(
        encoding="utf-8"
    )


def test_mutable_device_state_is_excluded_from_update_manifest():
    manifest = (BOARD / "sw-description.in").read_text(encoding="utf-8")
    assert manifest.count('filename = "rootfs.ext4.gz";') == 2
    for forbidden in (
        "wpa_supplicant",
        "root-password",
        "dropbear",
        "bluetooth",
        "history.json",
        "radio-adc.json",
        "asound.state",
        "data.ext4",
    ):
        assert forbidden not in manifest.lower()


def test_data_mount_and_compatibility_paths_precede_provisioning():
    post_build = POST_BUILD.read_text(encoding="utf-8")
    assert 'mkdir -p "${TARGET_DIR}/data"' in post_build
    assert "/dev/mmcblk0p4 /data ext4" in post_build
    inittab = INITTAB.read_text(encoding="utf-8")
    assert inittab.index("radio-persistent-boot") < inittab.index("hostname -F")
    paths = PERSISTENT_PATHS.read_text(encoding="utf-8")
    assert 'link_path "$DATA/radio" /etc/radio' in paths
    assert 'link_path "$DATA/network/wpa_supplicant.conf" /etc/wpa_supplicant.conf' in paths
    assert 'rm -rf "$compat"' in paths
    assert 'ln -s "$target" "$compat"' in paths
    assert 'link_path "$DATA/identity/dropbear" /etc/dropbear' in paths
    assert 'link_path "$DATA/bluetooth" /var/lib/bluetooth' in paths


def test_inittab_creates_world_readable_equalizer_runtime_dir():
    # /run/radio must exist mode 0755 before audio consumers open the LADSPA
    # plugin, so the unprivileged 'mpd' user can read the live equalizer.rt.
    inittab = INITTAB.read_text(encoding="utf-8")
    assert "install -d -m 0755 /run/radio" in inittab
    # It is created before the radio services (and their consumers) start.
    assert inittab.index("install -d -m 0755 /run/radio") < inittab.index("rcS")


def test_chrony_overlay_ends_with_newline_for_safe_runtime_rewrites():
    assert CHRONY_CONFIG.read_bytes().endswith(b"\n")


def test_persistent_prepare_and_runtime_render_wrap_provisioning():
    boot = PERSISTENT_BOOT.read_text(encoding="utf-8")
    validation = boot.index("/proc/mounts")
    paths = boot.index("radio-persistent-paths", validation)
    prepare = boot.index("radio-persistent-config prepare", paths)
    provision = boot.index("provision-from-boot", prepare)
    render = boot.index("radio-persistent-config render", provision)
    assert validation < paths < prepare < provision < render
    assert "persistent provisioning skipped" in boot
    wrapper = PERSISTENT_CONFIG.read_text(encoding="utf-8")
    assert "radio_web.persistent_config" in wrapper


def test_persistent_path_allowlist_excludes_firmware_owned_audio_files():
    paths = PERSISTENT_PATHS.read_text(encoding="utf-8")
    for required in (
        "$DATA/radio/logos",
        "$DATA/network",
        "$DATA/identity/dropbear",
        "$DATA/bluetooth",
        "$DATA/update/config-backups",
    ):
        assert required in paths
    for forbidden in ("mpd.conf", "asound.conf", "asound.state", "radio-audio.conf"):
        assert forbidden not in paths


def test_persistent_data_reference_covers_layout_links_and_update_state():
    """Keep the canonical persistence inventory aligned with its implementation."""
    text = PERSISTENT_DOC.read_text(encoding="utf-8")
    for required in (
        "/dev/mmcblk0p4",
        "/data/radio",
        "/data/radio/equalizer.ini",
        "/data/radio/artwork.ini",
        "/data/radio/usb_audio.ini",
        "/data/radio/usb_audio_output.ini",
        "/data/network/wpa_supplicant.conf",
        "/data/identity/root-password.hash",
        "/data/identity/dropbear",
        "/data/bluetooth",
        "/data/operations",
        "/data/update/upload/firmware.swu.part",
        "/data/update/upload/firmware.swu.ready",
        "/data/update/upload/firmware.swu.json",
        "/data/update/upload/current.json",
        "/data/update/queue/firmware.swu",
        "/data/update/history.json",
        "/data/update/config-backups/schema-*",
        "/data/update/data-resize/pending",
        "/data/update/data-resize/mbr.backup",
        "/etc/radio",
        "/etc/wpa_supplicant.conf",
        "/etc/dropbear",
        "/var/lib/bluetooth",
        "data.ext4",
        "radio-persistent-boot",
        "radio-persistent-paths",
        "pisonic-config.txt",
    ):
        assert required in text


def test_persistent_data_reference_names_generated_and_volatile_exclusions():
    text = PERSISTENT_DOC.read_text(encoding="utf-8")
    for excluded in (
        "/run/firmware-update/*",
        "/tmp/firmware-health.log",
        "/tmp/radio-adc.json",
        "/var/log",
        "/etc/mpd.conf",
        "/etc/asound.conf",
        "/etc/hostname",
        "/etc/chrony.conf",
        "/etc/localtime",
    ):
        assert excluded in text
    assert "contains no `data.ext4`" in text
    assert "/data/operations/summary.json" in text


def test_data_expansion_preserves_firmware_partition_entries():
    original = _mbr(
        [
            (1, 0x0C, 100, 900),
            (2, 0x83, 1000, 2000),
            (3, 0x83, 3000, 2000),
            (4, 0x83, 5000, 1000),
        ]
    )
    result = data_partition.expanded_mbr(_data_layout(), original)
    assert result[446 : 446 + 3 * 16] == original[446 : 446 + 3 * 16]
    assert data_partition._entry(result, 4) == (0x83, 5000, 15_000)


def test_data_expansion_rejects_missing_or_overlapping_slots():
    missing = _mbr([(1, 0x0C, 100, 900), (2, 0x83, 1000, 2000), (4, 0x83, 5000, 1000)])
    with pytest.raises(ValueError, match="partitions 1 through 4"):
        data_partition.validate_mbr(_data_layout(), missing)
    overlap = _mbr(
        [
            (1, 0x0C, 100, 900),
            (2, 0x83, 1000, 2500),
            (3, 0x83, 3000, 2000),
            (4, 0x83, 5000, 1000),
        ]
    )
    with pytest.raises(ValueError, match="overlap"):
        data_partition.validate_mbr(_data_layout(), overlap)


def test_data_layout_discovery_requires_ext4_partition_four(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("1 0 179:4 / /data rw - ext4 /dev/mmcblk0p4 rw\n")
    sysfs = tmp_path / "sys"
    (sysfs / "mmcblk0" / "queue").mkdir(parents=True)
    (sysfs / "mmcblk0p4").mkdir()
    (sysfs / "mmcblk0" / "queue" / "logical_block_size").write_text("512\n")
    (sysfs / "mmcblk0" / "size").write_text("20000\n")
    (sysfs / "mmcblk0p4" / "start").write_text("5000\n")
    (sysfs / "mmcblk0p4" / "size").write_text("1000\n")
    layout = data_partition.discover_layout(str(mountinfo), str(sysfs))
    assert layout.partition == "/dev/mmcblk0p4"
    assert layout.target_sectors == 15_000


def test_rootfs_expansion_surface_is_removed():
    protocol = (ROOT / "radio_web" / "helper_protocol.py").read_text(encoding="utf-8")
    routes = (ROOT / "radio_web" / "routes.py").read_text(encoding="utf-8")
    assert "expand_rootfs" not in protocol
    assert "/device/rootfs/" not in routes
    assert not (ROOT / "radio_web" / "rootfs_resize.py").exists()
    assert not (BOARD / "rootfs-overlay" / "etc" / "init.d" / "S12rootfs-resize").exists()
