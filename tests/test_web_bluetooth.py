"""Tests for the read-only Bluetooth adapter status reader and MAC validator."""

import pytest

from radio_web import bluetooth_store, validators

_SHOW = """Controller AA:BB:CC:11:22:33 (public)
	Name: radio
	Alias: Kitchen Radio
	Powered: yes
	Discoverable: no
	Pairable: yes
"""


class TestBluetoothValidators:
    def test_mac_address_ok(self):
        assert validators.validate_mac_address("aa:bb:cc:dd:ee:ff") == ("AA:BB:CC:DD:EE:FF")

    def test_mac_address_rejects_junk(self):
        for bad in ("", "not-a-mac", "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:FF:00"):
            with pytest.raises(ValueError):
                validators.validate_mac_address(bad)


class TestBluetoothStore:
    def test_parse_show(self):
        info = bluetooth_store._parse_show(_SHOW)
        assert info["alias"] == "Kitchen Radio"
        assert info["powered"] is True
        assert info["discoverable"] is False

    def test_adapter_info_missing_adapter(self, monkeypatch):
        monkeypatch.setattr(bluetooth_store, "_run", lambda cmd: None)
        info = bluetooth_store.adapter_info()
        assert info["available"] is False
        assert info["alias"] is None

    def test_adapter_info_reports_fields(self, monkeypatch):
        monkeypatch.setattr(bluetooth_store, "_run", lambda cmd: _SHOW)
        info = bluetooth_store.adapter_info()
        assert info["available"] is True
        assert info["alias"] == "Kitchen Radio"
        assert info["powered"] is True
        assert info["discoverable"] is False
