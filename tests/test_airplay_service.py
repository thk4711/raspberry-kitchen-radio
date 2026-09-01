import time
from unittest import mock

import dbus
from airplay_service.airplay_service import AirplayService


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_constructor_does_not_wait_for_absent_dbus(monkeypatch, tmp_path):
    monkeypatch.setattr(dbus, "SystemBus", mock.Mock(
        side_effect=dbus.DBusException("absent")
    ))
    started = time.monotonic()
    service = AirplayService(False, str(tmp_path / "missing-fifo"))
    try:
        assert time.monotonic() - started < 0.25
        assert service.get_play_state() is False
    finally:
        service.close()


def test_connects_when_dbus_appears_later(monkeypatch, tmp_path):
    interface = mock.Mock()
    interface.Get.return_value = "Playing"
    bus = mock.Mock()
    bus.get_object.return_value = object()
    monkeypatch.setattr(
        dbus, "SystemBus",
        mock.Mock(side_effect=[dbus.DBusException("absent"), bus])
    )
    monkeypatch.setattr(dbus, "Interface", mock.Mock(return_value=interface))
    monkeypatch.setattr(
        AirplayService, "_wait", lambda self, delay: self._stop.wait(0.01)
    )
    service = AirplayService(False, str(tmp_path / "missing-fifo"))
    try:
        assert _wait_until(service.get_play_state)
    finally:
        service.close()


def test_disconnect_clears_interface_for_reconnect(monkeypatch, tmp_path):
    interface = mock.Mock()
    interface.Get.side_effect = dbus.DBusException("restarted")
    service = AirplayService.__new__(AirplayService)
    service._lock = __import__('threading').RLock()
    service.properties_interface = interface
    assert service.get_play_state() is False
    assert service.properties_interface is None
