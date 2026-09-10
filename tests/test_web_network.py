"""Tests for the Area C WiFi/static-IP validators, store and apply/rollback."""

import os

import pytest

from radio_web import (
    helper,
    helper_protocol,
    network_apply,
    network_store,
    validators,
)


class TestNetworkValidators:
    def test_ssid(self):
        assert validators.validate_ssid("  MyNet ") == "MyNet"
        with pytest.raises(ValueError):
            validators.validate_ssid("")
        with pytest.raises(ValueError):
            validators.validate_ssid("x" * 33)

    def test_passphrase_range(self):
        assert validators.validate_wifi_passphrase("12345678") == "12345678"
        with pytest.raises(ValueError):
            validators.validate_wifi_passphrase("short")
        with pytest.raises(ValueError):
            validators.validate_wifi_passphrase("x" * 64)

    def test_country_code(self):
        assert validators.validate_country_code("de") == "DE"
        assert validators.validate_country_code("") == ""
        with pytest.raises(ValueError):
            validators.validate_country_code("DEU")

    def test_ipv4(self):
        assert validators.validate_ipv4("192.168.1.10", "IP") == "192.168.1.10"
        with pytest.raises(ValueError):
            validators.validate_ipv4("192.168.1.999", "IP")
        with pytest.raises(ValueError):
            validators.validate_ipv4("not-an-ip", "IP")

    def test_prefix(self):
        assert validators.validate_ipv4_prefix("24") == 24
        with pytest.raises(ValueError):
            validators.validate_ipv4_prefix("33")

    def test_dns_list(self):
        assert validators.validate_dns_list("1.1.1.1 8.8.8.8") == "1.1.1.1 8.8.8.8"
        assert validators.validate_dns_list("") == ""
        with pytest.raises(ValueError):
            validators.validate_dns_list("1.1.1.1 8.8.8.8 9.9.9.9 4.4.4.4")


class TestNetworkStoreForm:
    def test_dhcp_form(self):
        cleaned = network_store.validate_wifi_form(
            {
                "ssid": "Net",
                "psk": "password1",
                "ip_mode": "dhcp",
            }
        )
        assert cleaned["ssid"] == "Net"
        assert cleaned["ip_address"] == ""

    def test_static_form(self):
        cleaned = network_store.validate_wifi_form(
            {
                "ssid": "Net",
                "psk": "password1",
                "ip_mode": "static",
                "ip_address": "192.168.1.42",
                "ip_prefix": "24",
                "ip_gateway": "192.168.1.1",
                "ip_dns": "1.1.1.1",
            }
        )
        assert cleaned["ip_address"] == "192.168.1.42"
        assert cleaned["ip_gateway"] == "192.168.1.1"
        assert cleaned["ip_dns"] == "1.1.1.1"

    def test_bad_mode_rejected(self):
        with pytest.raises(ValueError):
            network_store.validate_wifi_form(
                {
                    "ssid": "Net",
                    "psk": "password1",
                    "ip_mode": "bridge",
                }
            )


@pytest.fixture()
def wifi_paths(monkeypatch, tmp_path):
    wpa = tmp_path / "wpa_supplicant.conf"
    static = tmp_path / "wlan-static.env"
    marker = tmp_path / "wlan-rollback-pending"
    country = tmp_path / "wifi-country"
    monkeypatch.setattr(network_apply, "WPA_CONF", str(wpa))
    monkeypatch.setattr(network_apply, "WPA_PREV", str(wpa) + ".prev")
    monkeypatch.setattr(network_store, "STATIC_IP_FILE", str(static))
    monkeypatch.setattr(network_store, "ROLLBACK_MARKER", str(marker))
    monkeypatch.setattr(network_store, "WIFI_COUNTRY_FILE", str(country))
    # Never actually restart WiFi or spawn a watcher in unit tests.
    monkeypatch.setattr(network_apply, "_restart_wifi", lambda: True)
    monkeypatch.setattr(network_apply, "_arm_watcher", lambda seconds: None)
    return {"wpa": wpa, "static": static, "marker": marker, "country": country}


class TestNetworkApply:
    def _base(self):
        return {
            "ssid": "Net",
            "psk": "password1",
            "country": "DE",
            "ip_address": "",
            "ip_prefix": "",
            "ip_gateway": "",
            "ip_dns": "",
        }

    def test_atomic_write_through_symlink_preserves_link(self, wifi_paths):
        target = wifi_paths["wpa"].with_name("persistent-wpa.conf")
        target.write_text("old\n")
        wifi_paths["wpa"].symlink_to(target)
        network_apply._atomic_write(str(wifi_paths["wpa"]), "new\n")
        assert wifi_paths["wpa"].is_symlink()
        assert wifi_paths["wpa"].resolve() == target
        assert target.read_text() == "new\n"

    def test_apply_writes_wpa_and_marker(self, wifi_paths):
        ok, _ = network_apply.apply_wifi(self._base(), rollback_seconds=30)
        assert ok is True
        text = wifi_paths["wpa"].read_text()
        assert 'ssid="Net"' in text
        assert "key_mgmt=WPA-PSK" in text
        assert wifi_paths["marker"].exists()
        assert wifi_paths["country"].read_text().strip() == "DE"

    def test_apply_static_writes_env(self, wifi_paths):
        config = self._base()
        config.update(
            {
                "ip_address": "192.168.1.42",
                "ip_prefix": "24",
                "ip_gateway": "192.168.1.1",
                "ip_dns": "1.1.1.1",
            }
        )
        ok, _ = network_apply.apply_wifi(config, rollback_seconds=30)
        assert ok is True
        env = wifi_paths["static"].read_text()
        assert 'IP="192.168.1.42"' in env
        assert 'ROUTER="192.168.1.1"' in env
        assert 'DNS="1.1.1.1"' in env

    def test_apply_dhcp_removes_static(self, wifi_paths):
        wifi_paths["static"].write_text('IP="10.0.0.5"\n')
        network_apply.apply_wifi(self._base(), rollback_seconds=30)
        assert not wifi_paths["static"].exists()

    def test_bad_config_writes_nothing(self, wifi_paths):
        ok, _ = network_apply.apply_wifi({"ssid": "", "psk": "x"})
        assert ok is False
        assert not wifi_paths["wpa"].exists()

    def test_confirm_clears_marker_and_prev(self, wifi_paths):
        wifi_paths["wpa"].write_text("old\n")
        network_apply.apply_wifi(self._base(), rollback_seconds=30)
        assert wifi_paths["marker"].exists()
        ok, _ = network_apply.confirm()
        assert ok is True
        assert not wifi_paths["marker"].exists()
        assert not os.path.exists(network_apply.WPA_PREV)

    def test_rollback_restores_previous(self, wifi_paths):
        wifi_paths["wpa"].write_text("OLD-CONFIG\n")
        network_apply.apply_wifi(self._base(), rollback_seconds=30)
        assert "OLD-CONFIG" not in wifi_paths["wpa"].read_text()
        ok, _ = network_apply.rollback()
        assert ok is True
        assert wifi_paths["wpa"].read_text() == "OLD-CONFIG\n"
        assert not wifi_paths["marker"].exists()

    def test_rollback_pending_reads_marker(self, wifi_paths):
        network_apply.apply_wifi(self._base(), rollback_seconds=30)
        pending = network_store.rollback_pending()
        assert pending is not None
        assert pending["deadline"] is not None


class TestNetworkHelperOps:
    def test_wifi_actions_in_whitelist(self):
        for action in ("set_wifi", "wifi_confirm", "wifi_rollback"):
            assert action in helper_protocol.ACTION_IDS

    def test_set_wifi_forwards_to_apply(self, monkeypatch):
        seen = {}

        def fake_apply(config, rollback_seconds=0):
            seen["config"] = config
            return True, "ok"

        monkeypatch.setattr(network_apply, "apply_wifi", fake_apply)
        ok, _ = helper.dispatch(
            "set_wifi",
            {
                "ssid": "Net",
                "psk": "password1",
                "country": "DE",
                "ip_address": "",
                "ip_prefix": "",
                "ip_gateway": "",
                "ip_dns": "",
            },
        )
        assert ok is True
        assert seen["config"]["ssid"] == "Net"

    def test_set_wifi_revalidates_bad_ssid(self, wifi_paths):
        # No monkeypatch of apply_wifi: the real one must reject the empty SSID.
        ok, message = helper.dispatch("set_wifi", {"ssid": "", "psk": "x"})
        assert ok is False
        assert "SSID" in message
