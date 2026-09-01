import threading
import time
from unittest import mock

import dbus
from bluetooth_service.bluetooth_service import (
    MEDIA_PLAYER_IFACE,
    BluetoothService,
)


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_constructor_does_not_wait_for_absent_dbus(monkeypatch):
    monkeypatch.setattr(
        dbus, "SystemBus", mock.Mock(side_effect=dbus.DBusException("absent"))
    )
    started = time.monotonic()
    service = BluetoothService()
    try:
        assert time.monotonic() - started < 0.25
        assert service.get_play_state() is False
        metadata = service.get_metadata()
        assert metadata.name == ""
        assert metadata.title == ""
        assert metadata.cover == ""
        assert metadata.state is False
    finally:
        service.close()


def test_reads_metadata_from_connected_player(monkeypatch):
    # ObjectManager reports one object exposing MediaPlayer1.
    manager = mock.Mock()
    manager.GetManagedObjects.return_value = {
        "/org/bluez/hci0/dev_AA/player0": {MEDIA_PLAYER_IFACE: {}},
    }

    def _get(iface, prop):
        assert iface == MEDIA_PLAYER_IFACE
        if prop == "Status":
            return "playing"
        if prop == "Track":
            return {"Title": "Song", "Artist": "Band", "Album": "LP"}
        raise AssertionError(f"unexpected property {prop}")

    props = mock.Mock()
    props.Get.side_effect = _get

    def _interface(_obj, iface_name):
        return manager if iface_name.endswith("ObjectManager") else props

    monkeypatch.setattr(dbus, "SystemBus", mock.Mock(return_value=mock.Mock()))
    monkeypatch.setattr(dbus, "Interface", mock.Mock(side_effect=_interface))
    monkeypatch.setattr(
        BluetoothService, "_wait", lambda self, delay: self._stop.wait(0.01)
    )
    service = BluetoothService()
    try:
        assert _wait_until(service.get_play_state)
        metadata = service.get_metadata()
        assert metadata.name == "Band"
        assert metadata.title == "Song"
        assert metadata.cover == ""
        assert metadata.md5 == ""
        assert metadata.state is True
    finally:
        service.close()


def test_no_player_reports_not_playing(monkeypatch):
    manager = mock.Mock()
    manager.GetManagedObjects.return_value = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
    }
    monkeypatch.setattr(dbus, "SystemBus", mock.Mock(return_value=mock.Mock()))
    monkeypatch.setattr(dbus, "Interface", mock.Mock(return_value=manager))
    monkeypatch.setattr(
        BluetoothService, "_wait", lambda self, delay: self._stop.wait(0.01)
    )
    service = BluetoothService()
    try:
        # Give the worker a moment to run a refresh cycle.
        time.sleep(0.1)
        assert service.get_play_state() is False
    finally:
        service.close()


def test_set_play_state_without_connection_returns_false():
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    service._bus = None
    service._player_path = None
    assert service.set_play_state(True) is False
    assert service.set_play_state(False) is False


def test_set_play_state_issues_avrcp_command(monkeypatch):
    player = mock.Mock()
    monkeypatch.setattr(dbus, "Interface", mock.Mock(return_value=player))
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    service.service = "org.bluez"
    service._bus = mock.Mock()
    service._player_path = "/org/bluez/hci0/dev_AA/player0"
    assert service.set_play_state(True) is True
    player.Play.assert_called_once()
    assert service.set_play_state(False) is True
    player.Pause.assert_called_once()


def test_play_index_unsupported():
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    assert service.play_index(1) is False
