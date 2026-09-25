"""Static checks for target-side Buildroot networking scripts."""

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WLAN_INIT = (
    ROOT
    / "buildroot"
    / "external"
    / "board"
    / "radio"
    / "rootfs-overlay"
    / "etc"
    / "init.d"
    / "S41wlan"
)
DHCP_SCRIPT = WLAN_INIT.parents[1] / "udhcpc-wlan.script"
PERSISTENT_PATHS = (
    ROOT
    / "buildroot"
    / "external"
    / "board"
    / "radio"
    / "rootfs-overlay"
    / "usr"
    / "sbin"
    / "radio-persistent-paths"
)
PROVISION_SCRIPT = PERSISTENT_PATHS.with_name("provision-from-boot")
BUSYBOX_FRAGMENT = ROOT / "buildroot" / "external" / "board" / "radio" / "busybox.fragment"


def test_dhcp_sends_live_hostname_in_all_request_paths():
    script = WLAN_INIT.read_text(encoding="utf-8")
    assert 'dhcp_hostname="$(hostname)"' in script
    assert script.count('-x "hostname:$dhcp_hostname"') == 2


def test_wifi_startup_logs_progress_and_uses_bounded_polling():
    script = WLAN_INIT.read_text(encoding="utf-8")
    assert 'LOG="${S41WLAN_LOG:-/tmp/S41wlan.log}"' in script
    assert 'IFACE_WAIT_SECONDS="${S41WLAN_IFACE_WAIT_SECONDS:-12}"' in script
    assert 'IFACE_POLL_SECONDS="${S41WLAN_IFACE_POLL_SECONDS:-0.25}"' in script
    assert 'LINK_WAIT_SECONDS="${S41WLAN_LINK_WAIT_SECONDS:-8}"' in script
    assert "waiting up to ${IFACE_WAIT_SECONDS}s for ${IFACE}" in script
    assert "appeared after $((now - started))s" in script
    assert "starting wpa_supplicant with driver nl80211" in script
    assert "starting persistent DHCP client" in script


def test_cached_lease_is_applied_before_resident_dhcp_starts():
    script = WLAN_INIT.read_text(encoding="utf-8")
    assert "CACHE=/data/network-cache/wlan-last-lease.env" in script
    assert script.index("apply_cached_lease ||") < script.index("start_dhcp\n")
    dhcp_args = next(
        line.strip() for line in script.splitlines() if line.strip().startswith('args="')
    )
    assert dhcp_args == 'args="-i $IFACE -b -p $UDHCPC_PID -s $UDHCPC_SCRIPT -a"'
    assert "-q" not in dhcp_args
    assert '-r "$req_ip"' in script


def test_dhcp_hook_refreshes_cache_and_clears_explicitly_rejected_lease():
    script = DHCP_SCRIPT.read_text(encoding="utf-8")
    assert "/data/network-cache/wlan-last-lease.env" in script
    assert "bound|renew)" in script
    assert 'cmp -s "$cache_tmp" "$CACHE"' in script
    assert "SSID_HASH=" in script
    assert "deconfig)" in script
    assert "DHCP deconfig ignored; keeping any optimistic cached address" in script
    assert "nak)" in script
    assert "clear_rejected_lease" in script
    assert 'rm -f "$CACHE"' in script
    assert "flock 9 || exit 1" in script


def test_dhcp_hook_writes_sanitized_cache_and_removes_it_on_nak(tmp_path):
    commands = tmp_path / "commands"
    commands.mkdir()
    ip_log = tmp_path / "ip.log"
    ip_command = commands / "ip"
    ip_command.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$IP_LOG"\n'
        'if [ "$1" = "-o" ] && [ -n "$CURRENT_ADDR" ]; then\n'
        '    printf "2: wlan0    inet %s scope global wlan0\\n" "$CURRENT_ADDR"\n'
        "fi\n",
        encoding="ascii",
    )
    ip_command.chmod(0o755)
    iw_command = commands / "iw"
    iw_command.write_text('#!/bin/sh\nprintf "\\tSSID: Test network\\n"\n', encoding="ascii")
    iw_command.chmod(0o755)
    sha256sum_command = commands / "sha256sum"
    sha256sum_command.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        '[ "${FAIL_HASH:-0}" = "1" ] && exit 1\n'
        'printf "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  -\\n"\n',
        encoding="ascii",
    )
    sha256sum_command.chmod(0o755)
    flock_command = commands / "flock"
    flock_command.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    flock_command.chmod(0o755)

    cache = tmp_path / "network-cache" / "wlan-last-lease.env"
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 9.9.9.9 # eth0\n", encoding="ascii")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{commands}:{env['PATH']}",
            "IP_LOG": str(ip_log),
            "RADIO_WLAN_LEASE_CACHE": str(cache),
            "RADIO_RESOLV_CONF": str(resolv),
            "RADIO_WLAN_LEASE_LOCK": str(tmp_path / "wlan-lease.lock"),
            "S41WLAN_LOG": str(tmp_path / "wlan.log"),
            "interface": "wlan0",
            "ip": "192.0.2.20",
            "subnet": "255.255.255.254",
            "broadcast": "192.0.2.21",
            "router": "192.0.2.1 invalid",
            "dns": "192.0.2.53 invalid",
            "search": "example.test bad'name",
        }
    )

    subprocess.run(["sh", str(DHCP_SCRIPT), "bound"], env=env, check=True)
    assert cache.read_text(encoding="ascii") == (
        "IP='192.0.2.20'\n"
        "SUBNET='255.255.255.254'\n"
        "PREFIX='31'\n"
        "BROADCAST='192.0.2.21'\n"
        "ROUTER='192.0.2.1'\n"
        "DNS='192.0.2.53'\n"
        "DOMAIN='example.test'\n"
        "SSID_HASH='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n"
    )
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert "nameserver 192.0.2.53 # wlan0" in resolv.read_text(encoding="ascii")

    env["CURRENT_ADDR"] = "192.0.2.20/31"
    subprocess.run(["sh", str(DHCP_SCRIPT), "renew"], env=env, check=True)
    assert ip_log.read_text(encoding="ascii").count("addr flush dev wlan0 scope global") == 1

    subprocess.run(["sh", str(DHCP_SCRIPT), "nak"], env=env, check=True)
    assert not cache.exists()
    assert resolv.read_text(encoding="ascii") == "nameserver 9.9.9.9 # eth0\n"
    assert ip_log.read_text(encoding="ascii").count("addr flush dev wlan0 scope global") == 2

    env["FAIL_HASH"] = "1"
    env["CURRENT_ADDR"] = ""
    subprocess.run(["sh", str(DHCP_SCRIPT), "bound"], env=env, check=True)
    assert not cache.exists()
    assert ip_log.read_text(encoding="ascii").count("addr flush dev wlan0 scope global") == 3


def test_network_cache_is_private_and_invalidated_by_boot_provisioning():
    persistent = PERSISTENT_PATHS.read_text(encoding="utf-8")
    provision = PROVISION_SCRIPT.read_text(encoding="utf-8")
    assert 'mkdir -p "$DATA/network-cache"' in persistent
    assert 'chmod 0700 "$DATA/network-cache"' in persistent
    assert 'chmod 0600 "$DATA/network-cache/wlan-last-lease.env"' in persistent
    assert "LEASE_CACHE=/data/network-cache/wlan-last-lease.env" in provision
    assert 'rm -f "$LEASE_CACHE"' in provision


def test_busybox_supplies_cache_hashing_and_hook_locking_commands():
    fragment = BUSYBOX_FRAGMENT.read_text(encoding="utf-8")
    assert "CONFIG_SHA256SUM=y" in fragment
    assert "CONFIG_FLOCK=y" in fragment
