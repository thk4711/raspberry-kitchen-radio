"""Static checks for the minimal, local-only SWUpdate target integration."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "buildroot" / "external" / "board" / "radio"
DEFCONFIG = ROOT / "buildroot" / "external" / "configs" / "radio_rpi3_defconfig"
SWUPDATE_CONFIG = BOARD / "swupdate.config"
MANIFEST = BOARD / "sw-description.in"
LAYOUT = BOARD / "image-layout.conf"
POST_BUILD = BOARD / "post-build.sh"
OVERLAY = BOARD / "rootfs-overlay"
FW_ENV = OVERLAY / "etc" / "fw_env.config"
HWREVISION = OVERLAY / "etc" / "hwrevision"
INSTALLER = OVERLAY / "usr" / "sbin" / "radio-swupdate-inactive"
PROGRESS_PACKAGE = ROOT / "buildroot" / "external" / "package" / "radio-swupdate-progress"
BOOT_SCRIPT = BOARD / "boot.cmd"
CMDLINE = BOARD / "cmdline.txt"
UBOOT_FRAGMENT = BOARD / "uboot.fragment"
UBOOT_ENV = BOARD / "uboot-env.txt"
HEALTH_SERVICE = OVERLAY / "etc" / "init.d" / "S99firmware-health"
OPERATIONAL_SERVICE = OVERLAY / "etc" / "init.d" / "S15operational-summary"
RADIO_SERVICE = OVERLAY / "etc" / "init.d" / "S90radio"


def _assignments(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    return values


def test_defconfig_selects_minimal_swupdate_dependencies():
    text = DEFCONFIG.read_text(encoding="utf-8")
    for selection in (
        "BR2_PACKAGE_LIBCONFIG=y",
        "BR2_PACKAGE_OPENSSL=y",
        "BR2_PACKAGE_ZLIB=y",
        "BR2_PACKAGE_LIBUBOOTENV=y",
        "BR2_PACKAGE_SWUPDATE=y",
        'BR2_PACKAGE_SWUPDATE_CONFIG="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/swupdate.config"',
    ):
        assert selection in text
    assert "# BR2_PACKAGE_SWUPDATE_WEBSERVER is not set" in text
    assert "# BR2_PACKAGE_SWUPDATE_INSTALL_WEBSITE is not set" in text


def test_swupdate_enables_only_required_update_features():
    text = SWUPDATE_CONFIG.read_text(encoding="utf-8")
    for enabled in (
        "CONFIG_HW_COMPATIBILITY=y",
        "CONFIG_UBOOT=y",
        "CONFIG_BOOTLOADER_DEFAULT_UBOOT=y",
        "CONFIG_SSL_IMPL_OPENSSL=y",
        "CONFIG_HASH_VERIFY=y",
        "CONFIG_GUNZIP=y",
        "CONFIG_LIBCONFIG=y",
        "CONFIG_BOOTLOADERHANDLER=y",
        "CONFIG_RAW=y",
    ):
        assert enabled in text

    for disabled in (
        "CONFIG_SCRIPTS",
        "CONFIG_SIGNED_IMAGES",
        "CONFIG_ENCRYPTED_IMAGES",
        "CONFIG_ARCHIVE",
        "CONFIG_DISKPART",
        "CONFIG_SHELLSCRIPTHANDLER",
        "CONFIG_SWUFORWARDER_HANDLER",
        "CONFIG_UBIVOL",
        "CONFIG_SURICATTA",
        "CONFIG_WEBSERVER",
        "CONFIG_MONGOOSE",
    ):
        assert f"# {disabled} is not set" in text


def test_swupdate_uses_runtime_ipc_and_stable_hardware_revision():
    config = SWUPDATE_CONFIG.read_text(encoding="utf-8")
    assert 'CONFIG_SOCKET_CTRL_PATH="/run/swupdate/sockinstctrl"' in config
    assert 'CONFIG_SOCKET_PROGRESS_PATH="/run/swupdate/swupdateprog"' in config
    assert 'CONFIG_HW_COMPATIBILITY_FILE="/etc/hwrevision"' in config
    assert HWREVISION.read_text(encoding="utf-8").strip() == (
        "raspberry-kitchen-radio-rpi3a-plus 1"
    )


def test_redundant_environment_matches_reserved_image_layout():
    layout = _assignments(LAYOUT)
    entries = []
    for line in FW_ENV.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            device, offset, size = line.split()
            entries.append((device, int(offset, 0), int(size, 0)))

    assert entries == [
        ("/dev/mmcblk0", int(layout["UBOOT_ENV_OFFSET"]), int(layout["UBOOT_ENV_SIZE"])),
        (
            "/dev/mmcblk0",
            int(layout["UBOOT_ENV_REDUND_OFFSET"]),
            int(layout["UBOOT_ENV_SIZE"]),
        ),
    ]
    reserved = int(layout["RESERVED_SIZE"])
    assert all(offset + size <= reserved for _, offset, size in entries)
    assert entries[0][1] + entries[0][2] <= entries[1][1]


def test_manifest_has_two_fixed_slots_sharing_one_streamed_payload():
    text = MANIFEST.read_text(encoding="utf-8")
    assert text.count('filename = "rootfs.ext4.gz";') == 2
    assert text.count('type = "raw";') == 2
    assert text.count('compressed = "zlib";') == 2
    assert text.count("installed-directly = true;") == 2
    assert text.count('sha256 = "@ROOTFS_ARCHIVE_SHA256@";') == 2
    assert re.findall(r'device = "([^"]+)";', text) == [
        "/dev/mmcblk0p2",
        "/dev/mmcblk0p3",
    ]
    assert "slot-a:" in text
    assert "slot-b:" in text
    for forbidden in ("/dev/mmcblk0p1", "/dev/mmcblk0p4", 'device = "/dev/mmcblk0"'):
        assert forbidden not in text


def test_manifest_trial_state_matches_target_slot():
    text = MANIFEST.read_text(encoding="utf-8")
    slot_a, slot_b = text.split("slot-b:", 1)
    assert '{ name = "previous_slot"; value = "B"; }' in slot_a
    assert '{ name = "active_slot"; value = "A"; }' in slot_a
    assert '{ name = "previous_slot"; value = "A"; }' in slot_b
    assert '{ name = "active_slot"; value = "B"; }' in slot_b
    assert text.count('{ name = "upgrade_available"; value = "1"; }') == 2


def test_installer_derives_only_the_opposite_slot_from_kernel_cmdline():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "for argument in $(cat /proc/cmdline)" in text
    assert "A)" in text and "selection=slot-b" in text
    assert "B)" in text and "selection=slot-a" in text
    assert "PACKAGE=/data/update/queue/firmware.swu" in text
    assert '[ "$#" -ne 0 ]' in text
    assert 'exec /usr/bin/swupdate -i "$PACKAGE" -e "stable,$selection"' in text
    assert 'exec /usr/bin/swupdate -i "$1"' not in text
    assert "/dev/mmcblk0p2" in text and "/dev/mmcblk0p3" in text


def test_swupdate_daemon_is_disabled_but_local_installer_is_executable():
    post_build = POST_BUILD.read_text(encoding="utf-8")
    assert "for script in S80swupdate S90nqptp" in post_build
    assert '"${TARGET_DIR}/usr/sbin/radio-swupdate-inactive"' in post_build
    assert not any(BOARD.rglob("*.pem"))
    assert not any(BOARD.rglob("*.crt"))
    assert not any(BOARD.rglob("*.key"))


def test_machine_readable_progress_bridge_is_selected_and_uses_ipc_library():
    defconfig = DEFCONFIG.read_text(encoding="utf-8")
    source = (PROGRESS_PACKAGE / "radio-swupdate-progress.c").read_text(encoding="utf-8")
    makefile = (PROGRESS_PACKAGE / "radio-swupdate-progress.mk").read_text(encoding="utf-8")
    assert "BR2_PACKAGE_RADIO_SWUPDATE_PROGRESS=y" in defconfig
    assert "progress_ipc_connect_with_path" in source
    assert '"/run/swupdate/swupdateprog"' in source
    assert 'printf("PROGRESS\\t%u\\t%u\\t%u\\t%u\\t%s\\t%s\\n"' in source
    assert "-lswupdate" in makefile
    assert "$(TARGET_DIR)/usr/bin/radio-swupdate-progress" in makefile


def test_uboot_ab_policy_selects_matching_root_and_counts_trials():
    defconfig = DEFCONFIG.read_text(encoding="utf-8")
    boot = BOOT_SCRIPT.read_text(encoding="utf-8")
    fragment = UBOOT_FRAGMENT.read_text(encoding="utf-8")
    environment = UBOOT_ENV.read_text(encoding="utf-8")
    assert 'BR2_TARGET_UBOOT_BOARD_DEFCONFIG="rpi_3_32b"' in defconfig
    assert "BR2_LINUX_KERNEL_INSTALL_TARGET=y" in defconfig
    assert "CONFIG_ENV_IS_IN_MMC=y" in fragment
    assert "CONFIG_ENV_REDUNDANT=y" in fragment
    assert "CONFIG_ENV_OFFSET=0x100000" in fragment
    assert "CONFIG_ENV_OFFSET_REDUND=0x200000" in fragment
    assert "CONFIG_ENV_SIZE=0x10000" in fragment
    assert "setexpr bootcount ${bootcount} + 1" in boot
    assert "setenv rollback_from ${active_slot}" in boot
    assert "setenv active_slot ${previous_slot}" in boot
    assert "root=/dev/mmcblk0p${rootpart}" in boot
    assert "radio.slot=${rootslot}" in boot
    assert "setenv bootargs" in boot
    assert "boot_source=uboot" in boot
    assert CMDLINE.read_text(encoding="utf-8").strip() == (
        "rootwait console=tty1 quiet loglevel=3 logo.nologo"
    )
    assert "ext4load mmc 0:${rootpart}" in boot
    assert "scriptaddr=0x05400000" in environment
    assert "kernel_addr_r=0x00080000" in environment
    assert "bootcmd=fatload mmc 0:1 ${scriptaddr} boot.scr; source ${scriptaddr}" in environment


def test_post_image_rejects_resolved_uboot_without_redundant_environment():
    post_image = (BOARD / "post-image.sh").read_text(encoding="utf-8")
    assert "UBOOT_CONFIG=$(find" in post_image
    for required in (
        "CONFIG_ENV_IS_IN_MMC=y",
        "CONFIG_ENV_REDUNDANT=y",
        "CONFIG_ENV_SIZE=0x10000",
        "CONFIG_ENV_OFFSET=0x100000",
        "CONFIG_ENV_OFFSET_REDUND=0x200000",
    ):
        assert f"'{required}'" in post_image


def test_post_image_validates_and_seeds_canonical_redundant_environment_pair():
    post_image = (BOARD / "post-image.sh").read_text(encoding="utf-8")
    assert 'ENV_IMAGE_SIZE=$(wc -c < "$ENV_IMAGE"' in post_image
    assert 'ENV_IMAGE_FLAG=$(od -An -tu1 -j4 -N1 "$ENV_IMAGE"' in post_image
    assert '[ "$ENV_IMAGE_SIZE" != "$UBOOT_ENV_SIZE" ]' in post_image
    assert '[ "$ENV_IMAGE_FLAG" != "1" ]' in post_image
    assert "printf '\\000' | dd of=\"$ENV_REDUND_IMAGE\" bs=1 seek=4" in post_image
    assert 'dd if="$ENV_IMAGE" of="${BINARIES_DIR}/sdcard.img"' in post_image
    assert 'dd if="$ENV_REDUND_IMAGE" of="${BINARIES_DIR}/sdcard.img"' in post_image


def test_uboot_trial_fallback_is_ordered_before_slot_selection():
    boot = BOOT_SCRIPT.read_text(encoding="utf-8")
    increment = boot.index("setexpr bootcount ${bootcount} + 1")
    limit = boot.index("if test ${bootcount} -gt ${bootlimit}")
    remember = boot.index("setenv rollback_from ${active_slot}")
    restore = boot.index("setenv active_slot ${previous_slot}")
    clear_trial = boot.index("setenv upgrade_available 0", restore)
    save = boot.index("saveenv", clear_trial)
    select = boot.index('if test "${active_slot}" = "B"')
    assert increment < limit < remember < restore < clear_trial < save < select


def test_manifest_trial_state_is_complete_for_both_directions():
    text = MANIFEST.read_text(encoding="utf-8")
    assert text.count('{ name = "bootcount"; value = "0"; }') == 2
    assert text.count('{ name = "bootlimit"; value = "3"; }') == 2
    assert text.count('{ name = "upgrade_available"; value = "1"; }') == 2
    assert '{ name = "previous_slot"; value = "B"; }' in text
    assert '{ name = "active_slot"; value = "A"; }' in text
    assert '{ name = "previous_slot"; value = "A"; }' in text
    assert '{ name = "active_slot"; value = "B"; }' in text


def test_late_health_service_is_backgrounded_and_packaged():
    service = HEALTH_SERVICE.read_text(encoding="utf-8")
    post_build = POST_BUILD.read_text(encoding="utf-8")
    assert "start-stop-daemon -S -q -b" in service
    assert "radio-firmware-health reconcile" in service
    assert "fw_printenv -n upgrade_available" in service
    assert "radio-firmware-health -- run" in service
    assert "S99firmware-health" in post_build


def test_operational_summary_is_packaged_and_records_recovery_boundaries():
    operational = OPERATIONAL_SERVICE.read_text(encoding="utf-8")
    radio = RADIO_SERVICE.read_text(encoding="utf-8")
    post_build = POST_BUILD.read_text(encoding="utf-8")
    assert "radio_web.operational_summary" in operational
    assert "record boot" in operational
    assert "record clean-shutdown" in operational
    assert "heartbeat_timeout" in radio
    assert "operational_summary restart" in radio
    assert "S15operational-summary" in post_build


def test_release_metadata_and_fixed_other_slot_mountpoint_are_packaged():
    post_build = POST_BUILD.read_text(encoding="utf-8")
    assert "generate_release_metadata.py" in post_build
    assert '"${TARGET_DIR}/etc/radio-release.json"' in post_build
    assert '"${TARGET_DIR}/mnt/firmware-other-ro"' in post_build
