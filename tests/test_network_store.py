"""Unit tests for the web-side WiFi/static-IP store (radio_web.network_store).

Covers the read-only status helpers that parse on-disk handoff files and the
``iw`` link probe. All filesystem paths are redirected into ``tmp_path`` and the
subprocess is stubbed, so nothing touches the real system.
"""

import subprocess

import pytest

from radio_web import network_store


@pytest.fixture
def paths(monkeypatch, tmp_path):
    static = tmp_path / "wlan-static.env"
    rollback = tmp_path / "wlan-rollback-pending"
    monkeypatch.setattr(network_store, "STATIC_IP_FILE", str(static))
    monkeypatch.setattr(network_store, "ROLLBACK_MARKER", str(rollback))
    return static, rollback


def test_validate_wifi_form_dhcp_leaves_static_blank():
    cleaned = network_store.validate_wifi_form(
        {"ssid": "MyNet", "psk": "12345678", "country": "de", "ip_mode": "dhcp"}
    )
    assert cleaned["ssid"] == "MyNet"
    assert cleaned["country"] == "DE"
    assert cleaned["ip_address"] == ""
    assert cleaned["ip_prefix"] == ""


def test_validate_wifi_form_static_populates_quartet():
    cleaned = network_store.validate_wifi_form(
        {
            "ssid": "MyNet",
            "psk": "12345678",
            "ip_mode": "static",
            "ip_address": "192.168.1.10",
            "ip_prefix": "24",
            "ip_gateway": "192.168.1.1",
            "ip_dns": "1.1.1.1",
        }
    )
    assert cleaned["ip_address"] == "192.168.1.10"
    assert cleaned["ip_prefix"] == "24"
    assert cleaned["ip_gateway"] == "192.168.1.1"
    assert cleaned["ip_dns"]


def test_validate_wifi_form_rejects_bad_mode():
    with pytest.raises(ValueError, match="DHCP or a static"):
        network_store.validate_wifi_form({"ssid": "MyNet", "psk": "12345678", "ip_mode": "bridge"})


def test_rollback_pending_absent_marker(paths):
    assert network_store.rollback_pending() is None


def test_rollback_pending_reads_deadline(paths):
    _static, rollback = paths
    rollback.write_text("deadline=1700000000.5\n")
    result = network_store.rollback_pending()
    assert result == {"deadline": 1700000000.5}


def test_rollback_pending_malformed_deadline_is_none(paths):
    _static, rollback = paths
    rollback.write_text("deadline=not-a-number\n")
    assert network_store.rollback_pending() == {"deadline": None}


def test_read_static_env_parses_and_ignores_comments(paths):
    static, _rollback = paths
    static.write_text('# comment\nIP="192.168.1.10"\nGATEWAY=192.168.1.1\nbroken-line\n')
    parsed = network_store._read_static_env()
    assert parsed == {"IP": "192.168.1.10", "GATEWAY": "192.168.1.1"}


def test_read_static_env_missing_file_is_empty(paths):
    assert network_store._read_static_env() == {}


def test_current_ssid_parses_iw_output(monkeypatch):
    def fake_run(argv, **kwargs):
        assert argv == network_store._IW_LINK_CMD
        return subprocess.CompletedProcess(argv, 0, stdout="Connected\n\tSSID: HomeNet\n")

    monkeypatch.setattr(network_store.subprocess, "run", fake_run)
    assert network_store._current_ssid() == "HomeNet"


def test_current_ssid_none_when_not_associated(monkeypatch):
    monkeypatch.setattr(
        network_store.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="Not connected.\n"),
    )
    assert network_store._current_ssid() is None


def test_current_ssid_none_on_subprocess_error(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("iw missing")

    monkeypatch.setattr(network_store.subprocess, "run", boom)
    assert network_store._current_ssid() is None


def test_current_status_static_mode(monkeypatch, paths):
    static, _rollback = paths
    static.write_text("IP=192.168.1.10\n")
    monkeypatch.setattr(network_store, "_current_ssid", lambda: "HomeNet")
    status = network_store.current_status()
    assert status["mode"] == "static"
    assert status["ssid"] == "HomeNet"
    assert status["static"] == {"IP": "192.168.1.10"}
    assert status["pending"] is None


def test_current_status_dhcp_mode(monkeypatch, paths):
    monkeypatch.setattr(network_store, "_current_ssid", lambda: None)
    status = network_store.current_status()
    assert status["mode"] == "dhcp"
    assert status["ssid"] is None
