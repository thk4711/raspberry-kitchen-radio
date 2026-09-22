import os
import time
from unittest import mock

import dbus
from airplay_service.airplay_service import AirplayService

AIRPLAY_CONF = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "lib",
    "airplay_service",
    "airplay.conf",
)


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_config_advertises_exact_system_hostname():
    with open(AIRPLAY_CONF, encoding="utf-8") as handle:
        config = handle.read()
    assert 'name = "%h";' in config
    assert 'name = "%H";' not in config


def test_constructor_does_not_wait_for_absent_dbus(monkeypatch, tmp_path):
    monkeypatch.setattr(dbus, "SystemBus", mock.Mock(side_effect=dbus.DBusException("absent")))
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
        dbus, "SystemBus", mock.Mock(side_effect=[dbus.DBusException("absent"), bus])
    )
    monkeypatch.setattr(dbus, "Interface", mock.Mock(return_value=interface))
    monkeypatch.setattr(AirplayService, "_wait", lambda self, delay: self._stop.wait(0.01))
    service = AirplayService(False, str(tmp_path / "missing-fifo"))
    try:
        assert _wait_until(service.get_play_state)
    finally:
        service.close()


def test_set_play_state_stop_pauses_when_available_and_inhibits(monkeypatch, tmp_path):
    marker = tmp_path / "airplay-inhibited"
    props = mock.Mock()
    props.Get.return_value = True  # RemoteControl.Available
    remote = mock.Mock()
    service = AirplayService.__new__(AirplayService)
    service._lock = __import__("threading").RLock()
    service.properties_interface = props
    service.remote_control_interface = remote
    service.inhibit_file = marker

    assert service.set_play_state(False) is True
    remote.Pause.assert_called_once_with()
    assert marker.exists()  # local fallback always applied


def test_set_play_state_stop_inhibits_even_without_remote_control(monkeypatch, tmp_path):
    marker = tmp_path / "airplay-inhibited"
    props = mock.Mock()
    props.Get.return_value = False  # RemoteControl.Available is False (AirPlay 2)
    service = AirplayService.__new__(AirplayService)
    service._lock = __import__("threading").RLock()
    service.properties_interface = props
    service.remote_control_interface = None
    service.inhibit_file = marker

    assert service.set_play_state(False) is True
    assert marker.exists()


def test_set_play_state_start_clears_marker_and_plays(monkeypatch, tmp_path):
    marker = tmp_path / "airplay-inhibited"
    marker.touch()
    props = mock.Mock()
    props.Get.return_value = True
    remote = mock.Mock()
    service = AirplayService.__new__(AirplayService)
    service._lock = __import__("threading").RLock()
    service.properties_interface = props
    service.remote_control_interface = remote
    service.inhibit_file = marker

    assert service.set_play_state(True) is True
    assert not marker.exists()
    remote.Play.assert_called_once_with()


def test_get_play_state_respects_and_clears_inhibit_marker(monkeypatch, tmp_path):
    marker = tmp_path / "airplay-inhibited"
    marker.touch()
    props = mock.Mock()
    service = AirplayService.__new__(AirplayService)
    service._lock = __import__("threading").RLock()
    service.properties_interface = props
    service.remote_control_interface = None
    service.inhibit_file = marker

    # While the session is still playing, the marker keeps AirPlay inhibited.
    props.Get.return_value = "Playing"
    assert service.get_play_state() is False
    assert marker.exists()

    # Once the sender stops, the marker is cleared so a later stream can win.
    props.Get.return_value = "Paused"
    assert service.get_play_state() is False
    assert not marker.exists()

    # With no marker, play state follows shairport's PlayerState.
    props.Get.return_value = "Playing"
    assert service.get_play_state() is True


def test_send_remote_command_missing_method_is_soft_failure(tmp_path):
    marker = tmp_path / "airplay-inhibited"
    props = mock.Mock()
    props.Get.return_value = True
    # A remote-control object that lacks a Pause method (older build) must not
    # raise; set_play_state still applies the local inhibit fallback.
    remote = mock.Mock(spec=[])
    service = AirplayService.__new__(AirplayService)
    service._lock = __import__("threading").RLock()
    service.properties_interface = props
    service.remote_control_interface = remote
    service.inhibit_file = marker

    assert service.set_play_state(False) is True
    assert marker.exists()


def test_disconnect_clears_interface_for_reconnect(monkeypatch, tmp_path):
    interface = mock.Mock()
    interface.Get.side_effect = dbus.DBusException("restarted")
    service = AirplayService.__new__(AirplayService)
    service._lock = __import__("threading").RLock()
    service.properties_interface = interface
    service.remote_control_interface = None
    service.inhibit_file = tmp_path / "airplay-inhibited"
    assert service.get_play_state() is False
    assert service.properties_interface is None
