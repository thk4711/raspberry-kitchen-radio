"""Tests for the optional amplifier GPIO in ``radio.RadioController``.

The amp-enable pin is now per sound card and may be absent (a board with no
power amp). These tests exercise ``_resolve_amp_pin`` and the ``GPIO`` guards
without running the full, hardware-heavy ``RadioController.__init__``.
"""

import sys
import threading
import types
from unittest import mock


def _import_radio(monkeypatch):
    """Import the controller without loading the Raspberry Pi display driver."""
    monkeypatch.setitem(sys.modules, "yaml", types.ModuleType("yaml"))
    display_module = types.ModuleType("display.display_control")
    display_module.DisplayController = mock.Mock()
    monkeypatch.setitem(sys.modules, "display.display_control", display_module)
    import radio

    return radio


def _controller(monkeypatch, config):
    """Build a RadioController shell (no __init__) with a given config dict."""
    radio = _import_radio(monkeypatch)
    controller = radio.RadioController.__new__(radio.RadioController)
    controller._state_lock = threading.RLock()
    controller.config = config
    return radio, controller


class TestResolveAmpPin:
    def test_numeric_pin_is_used(self, monkeypatch):
        _radio, c = _controller(monkeypatch, {"gpio": {"amp": 26}})
        assert c._resolve_amp_pin() == 26

    def test_zero_is_a_valid_pin(self, monkeypatch):
        _radio, c = _controller(monkeypatch, {"gpio": {"amp": 0}})
        assert c._resolve_amp_pin() == 0

    def test_none_sentinel_disables_amp(self, monkeypatch):
        _radio, c = _controller(monkeypatch, {"gpio": {"amp": "none"}})
        assert c._resolve_amp_pin() is None

    def test_false_disables_amp(self, monkeypatch):
        # read_config coerces "false" to the bool False.
        _radio, c = _controller(monkeypatch, {"gpio": {"amp": False}})
        assert c._resolve_amp_pin() is None

    def test_missing_key_or_section_disables_amp(self, monkeypatch):
        _radio, c = _controller(monkeypatch, {"gpio": {}})
        assert c._resolve_amp_pin() is None
        _radio, c = _controller(monkeypatch, {})
        assert c._resolve_amp_pin() is None

    def test_garbage_disables_amp(self, monkeypatch):
        _radio, c = _controller(monkeypatch, {"gpio": {"amp": "twenty-six"}})
        assert c._resolve_amp_pin() is None


class TestAmpGpioGuards:
    def test_switch_toggle_drives_pin_when_set(self, monkeypatch):
        radio, c = _controller(monkeypatch, {})
        c.AMP_PIN = 26
        c.power_switch = False
        c.display = mock.Mock()
        c.active_service = mock.Mock()
        c.update_metadata = mock.Mock()
        radio.GPIO.output.reset_mock()
        c.handle_switch_state_change(1, True)
        radio.GPIO.output.assert_called_once_with(26, True)

    def test_switch_toggle_skips_gpio_when_no_amp(self, monkeypatch):
        radio, c = _controller(monkeypatch, {})
        c.AMP_PIN = None
        c.power_switch = False
        c.display = mock.Mock()
        c.active_service = mock.Mock()
        c.update_metadata = mock.Mock()
        radio.GPIO.output.reset_mock()
        c.handle_switch_state_change(1, True)
        radio.GPIO.output.assert_not_called()
