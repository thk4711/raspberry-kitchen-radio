"""Host tests for the PCF8574 button reader and debounce policy."""

from unittest import mock

import smbus2
from button_handler import ButtonHandler


def _handler(monkeypatch):
    bus = mock.Mock()
    monkeypatch.setattr(smbus2, "SMBus", mock.Mock(return_value=bus), raising=False)
    return ButtonHandler(1, 0x20), bus


def test_constructor_opens_requested_bus(monkeypatch):
    handler, _bus = _handler(monkeypatch)
    smbus2.SMBus.assert_called_once_with(1)
    assert handler.address == 0x20


def test_read_returns_byte_and_i2c_failure_returns_none(monkeypatch):
    handler, bus = _handler(monkeypatch)
    bus.read_byte.return_value = 0x3E
    assert handler.read_pcf8574() == 0x3E
    bus.read_byte.side_effect = OSError("missing")
    assert handler.read_pcf8574() is None


def test_poll_maps_active_low_bits_to_one_based_buttons(monkeypatch):
    handler, bus = _handler(monkeypatch)
    callback = mock.Mock()
    bus.read_byte.return_value = 0b00101110  # buttons 1 and 5 pressed
    handler.poll_once(callback, now=1.0)
    assert callback.call_args_list == [mock.call(1), mock.call(5)]


def test_poll_ignores_unchanged_failed_and_debounced_reads(monkeypatch):
    handler, bus = _handler(monkeypatch)
    callback = mock.Mock()

    bus.read_byte.return_value = handler.last_data
    handler.poll_once(callback, now=1.0)
    bus.read_byte.side_effect = OSError("transient")
    handler.poll_once(callback, now=1.0)
    bus.read_byte.side_effect = None
    bus.read_byte.return_value = 0b00111110
    handler.poll_once(callback, now=0.1)

    callback.assert_not_called()


def test_release_updates_debounce_without_dispatching(monkeypatch):
    handler, bus = _handler(monkeypatch)
    handler.last_data = 0b00111110
    callback = mock.Mock()
    bus.read_byte.return_value = 0b00111111
    handler.poll_once(callback, now=2.0)
    callback.assert_not_called()
    assert handler.debounce_timer == 2.0


def test_poll_uses_system_clock_when_not_injected(monkeypatch):
    handler, bus = _handler(monkeypatch)
    bus.read_byte.return_value = 0b00111101
    callback = mock.Mock()
    monkeypatch.setattr("button_handler.time.time", mock.Mock(return_value=3.0))
    handler.poll_once(callback)
    callback.assert_called_once_with(2)


def test_cleanup_closes_bus(monkeypatch):
    handler, bus = _handler(monkeypatch)
    handler.cleanup()
    bus.close.assert_called_once_with()
