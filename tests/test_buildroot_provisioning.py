"""Static checks for Buildroot features required by boot provisioning."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFCONFIG = ROOT / "buildroot" / "external" / "configs" / "radio_rpi3_defconfig"
BUSYBOX_FRAGMENT = ROOT / "buildroot" / "external" / "board" / "radio" / "busybox.fragment"
PROVISION_SCRIPT = (
    ROOT
    / "buildroot"
    / "external"
    / "board"
    / "radio"
    / "rootfs-overlay"
    / "usr"
    / "sbin"
    / "provision-from-boot"
)
RADIO_CONFIG = ROOT / "buildroot" / "external" / "board" / "radio" / "radio-config.txt"
POST_IMAGE = ROOT / "buildroot" / "external" / "board" / "radio" / "post-image.sh"
POST_BUILD = ROOT / "buildroot" / "external" / "board" / "radio" / "post-build.sh"
BUILD_SCRIPT = ROOT / "buildroot" / "build.sh"
DROPBEAR_INIT = (
    ROOT
    / "buildroot"
    / "external"
    / "board"
    / "radio"
    / "rootfs-overlay"
    / "etc"
    / "init.d"
    / "S50dropbear"
)


def test_busybox_chpasswd_is_enabled_for_root_password_provisioning():
    fragment = BUSYBOX_FRAGMENT.read_text(encoding="utf-8")
    defconfig = DEFCONFIG.read_text(encoding="utf-8")
    provisioner = PROVISION_SCRIPT.read_text(encoding="utf-8")

    assert "CONFIG_CHPASSWD=y" in fragment
    assert (
        'BR2_PACKAGE_BUSYBOX_CONFIG_FRAGMENT_FILES="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/busybox.fragment"'
        in defconfig
    )
    assert "printf 'root:%s\\n' \"$pw\" | chpasswd" in provisioner


def test_boot_partition_ships_active_radio_config_under_final_name():
    config = RADIO_CONFIG.read_text(encoding="utf-8")
    post_image = POST_IMAGE.read_text(encoding="utf-8")
    assert "wifi_ssid=MyNetwork" in config
    assert "hostname=changeme" in config
    assert "root_password=changeme" in config
    assert "enable_ssh=0" in config
    assert "timezone=UTC" in config
    assert 'RADIO_CONFIG="${BOARD_DIR}/radio-config.txt"' in post_image
    assert "radio-config.txt.example" not in post_image


def test_generic_build_does_not_require_per_device_configuration():
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    post_build = POST_BUILD.read_text(encoding="utf-8")

    assert not (ROOT / "buildroot" / "configure.sh").exists()
    assert "local-device.conf" not in build_script
    assert "--configure" not in build_script
    assert 'WPA_CONF="${TARGET_DIR}/etc/wpa_supplicant.conf"' in post_build
    assert ': > "${WPA_CONF}"' in post_build


def test_provisioner_comments_consumed_settings_after_apply():
    provisioner = PROVISION_SCRIPT.read_text(encoding="utf-8")
    assert "comment_consumed_settings" in provisioner
    assert "CONSUMED_KEYS" in provisioner
    assert "mark_consumed wifi_ssid wifi_psk" in provisioner
    assert "mark_consumed wifi_country" in provisioner
    assert "mark_consumed ip_address ip_prefix ip_gateway ip_dns" in provisioner
    assert "mark_consumed hostname" in provisioner
    assert "mark_consumed root_password" in provisioner
    assert "mark_consumed enable_ssh" in provisioner
    assert "mark_consumed timezone" in provisioner
    assert "mark_consumed ntp_server" in provisioner
    assert 'index(consumed, " " key " ") > 0' in provisioner
    assert "settings will retry next boot" in provisioner
    assert 'print "# " line' in provisioner
    assert 'mount -t vfat -o rw "$BOOT_DEV" "$BOOT_MNT"' in provisioner


def test_wifi_write_resolves_symlink_target_into_persistent_data():
    provisioner = PROVISION_SCRIPT.read_text(encoding="utf-8")
    # WiFi credentials must be written through the /etc compatibility symlink
    # into its /data target, never replace the symlink with a regular file.
    assert "resolve_target()" in provisioner
    assert 'wpa_target=$(resolve_target "$WPA_CONF")' in provisioner
    assert 'mv "$tmp" "$wpa_target"' in provisioner
    assert 'mv "$tmp" "$WPA_CONF"' not in provisioner


def test_failed_network_provisioning_preserves_existing_state_and_retries():
    provisioner = PROVISION_SCRIPT.read_text(encoding="utf-8")

    assert "could not persist $WPA_CONF; settings will retry next boot" in provisioner
    assert "could not persist $WLAN_STATIC_FILE; settings will retry next boot" in provisioner
    assert "keeping previous network configuration" in provisioner
    assert 'rm -f "$WLAN_STATIC_FILE"' not in provisioner


def test_ssh_is_disabled_without_explicit_enable():
    script = DROPBEAR_INIT.read_text(encoding="utf-8")
    assert 'if ! [ -r "$DEVICE_CONFIG" ] || ! awk' in script
    assert 'if (value == "true") enabled = 1' in script


def test_provisioner_persists_identity_and_device_choices():
    provisioner = PROVISION_SCRIPT.read_text(encoding="utf-8")
    assert "radio-persistent-config capture-root-password" in provisioner
    assert "radio-persistent-config set-device name" in provisioner
    assert "radio-persistent-config set-device timezone" in provisioner
    assert "radio-persistent-config set-device ntp_server" in provisioner
    assert "radio-persistent-config set-device ssh_enabled" in provisioner
    assert "enabled and persisted" in provisioner
    assert "cannot persist setting; SSH kept disabled" in provisioner
    assert "/etc/shadow" not in provisioner
