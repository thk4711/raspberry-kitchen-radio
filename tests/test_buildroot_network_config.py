"""Static checks for target-side hostname propagation in Buildroot scripts."""

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
    assert "starting DHCP client in background" in script
