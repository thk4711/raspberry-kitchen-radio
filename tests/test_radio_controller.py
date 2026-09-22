"""Focused host tests for top-level source and display orchestration."""

import sys
import threading
import types
from unittest import mock

from music_source import Metadata


def _import_radio(monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", types.ModuleType("yaml"))
    display_module = types.ModuleType("display.display_control")
    display_module.DisplayController = mock.Mock()
    monkeypatch.setitem(sys.modules, "display.display_control", display_module)
    import radio

    return radio


def _controller(monkeypatch):
    radio = _import_radio(monkeypatch)
    controller = radio.RadioController.__new__(radio.RadioController)
    controller._state_lock = threading.RLock()
    controller.display = mock.Mock()
    controller.mpd = mock.Mock()
    controller.active_service = controller.mpd
    controller.power_switch = True
    controller.AMP_PIN = None
    return radio, controller


def test_log_level_accepts_names_numbers_and_invalid_values(monkeypatch):
    radio = _import_radio(monkeypatch)
    monkeypatch.delenv("RADIO_LOG_LEVEL", raising=False)
    assert radio._resolve_log_level() == radio.logging.ERROR
    monkeypatch.setenv("RADIO_LOG_LEVEL", " debug ")
    assert radio._resolve_log_level() == radio.logging.DEBUG
    monkeypatch.setenv("RADIO_LOG_LEVEL", "17")
    assert radio._resolve_log_level() == 17
    monkeypatch.setenv("RADIO_LOG_LEVEL", "unknown")
    assert radio._resolve_log_level() == radio.logging.ERROR


def test_source_flags_default_and_layer_managed_values(monkeypatch):
    radio, controller = _controller(monkeypatch)
    monkeypatch.setattr(
        radio.utility,
        "read_config_layered",
        mock.Mock(return_value={"sources": {"spotify": False, "airplay": True}}),
    )
    flags = controller._load_source_flags("/app")
    assert flags == {
        "internet_radio": True,
        "airplay": True,
        "spotify": False,
        "bluetooth": True,
        "usb_audio": True,
    }


def test_switch_button_and_volume_forward_to_collaborators(monkeypatch):
    radio, controller = _controller(monkeypatch)
    controller.update_metadata = mock.Mock()
    controller.mpd.stations = [{"name": f"Station {index}"} for index in range(1, 7)]

    controller.handle_switch_state_change(1, False)
    controller.display.toggle_backlight.assert_called_once_with(False)
    controller.mpd.set_play_state.assert_called_once_with(False)

    controller.handle_button_press(3)
    controller.mpd.play_index.assert_called_once_with(3)
    controller.display.show_toast.assert_called_once_with("Station 3")
    controller.handle_button_press(7)
    controller.mpd.play_index.assert_called_once()

    controller.handle_volume_change(42)
    controller.display.show_volume.assert_called_once_with(42)
    controller.display.show_volume.side_effect = RuntimeError("display failed")
    controller.handle_volume_change(43)  # display failures never escape the ADC callback


def test_update_metadata_selects_radio_and_cover_art_modes(monkeypatch):
    _radio, controller = _controller(monkeypatch)
    controller.mpd.name = "mpd"
    controller.mpd.get_metadata.return_value = Metadata(
        name="News", title="Now", cover="logo.png", md5="hash", state=True
    )
    controller.update_metadata()
    controller.display.update_metadata.assert_called_once_with(
        "News", "Now", "logo.png", "hash", state=True, art_mode="radio", source="mpd"
    )

    spotify = mock.Mock(name="spotify")
    spotify.name = "spotify"
    spotify.get_metadata.return_value = Metadata(
        name="Artist", title="Track", cover="cover.jpg", md5="v2", state=False
    )
    controller.active_service = spotify
    controller.display.update_metadata.reset_mock()
    controller.update_metadata()
    assert controller.display.update_metadata.call_args.kwargs["art_mode"] == "cover"
    assert controller.display.update_metadata.call_args.kwargs["source"] == "spotify"


def test_update_metadata_swallow_backend_failure(monkeypatch):
    _radio, controller = _controller(monkeypatch)
    controller.active_service.get_metadata.side_effect = RuntimeError("offline")
    controller.update_metadata()
    controller.display.update_metadata.assert_not_called()


def test_play_state_transition_selects_first_new_source_and_stops_others(monkeypatch):
    _radio, controller = _controller(monkeypatch)
    first = mock.Mock()
    first.get_play_state.return_value = True
    second = mock.Mock()
    second.get_play_state.return_value = True
    previous = mock.Mock()
    previous.get_play_state.return_value = True
    controller.services = [
        {"name": "usb", "service": first, "state": False},
        {"name": "spotify", "service": second, "state": False},
        {"name": "mpd", "service": previous, "state": True},
    ]
    controller.update_metadata = mock.Mock()
    controller.check_play_states()
    assert controller.active_service is first
    assert controller.update_metadata.call_count == 2
    second.set_play_state.assert_called_once_with(False)
    previous.set_play_state.assert_called_once_with(False)


def test_button_press_proactively_stops_other_sources(monkeypatch):
    _radio, controller = _controller(monkeypatch)
    controller.update_metadata = mock.Mock()
    controller.mpd.name = "mpd"
    controller.mpd.stations = [{"name": f"Station {index}"} for index in range(1, 7)]
    airplay = mock.Mock()
    airplay.get_play_state.return_value = True
    usb = mock.Mock()
    usb.get_play_state.return_value = True
    controller.services = [
        {"name": "airplay", "service": airplay, "state": True},
        {"name": "usb", "service": usb, "state": True},
        {"name": "mpd", "service": controller.mpd, "state": False},
    ]

    controller.handle_button_press(2)

    # MPD becomes active and the other sources are stopped immediately, without
    # waiting for the next arbitration tick.
    assert controller.active_service is controller.mpd
    controller.mpd.play_index.assert_called_once_with(2)
    airplay.set_play_state.assert_called_once_with(False)
    usb.set_play_state.assert_called_once_with(False)
    # MPD itself is never stopped by the helper.
    controller.mpd.set_play_state.assert_not_called()


def test_stop_other_services_skips_keep_and_survives_backend_errors(monkeypatch):
    _radio, controller = _controller(monkeypatch)
    keep = mock.Mock()
    playing = mock.Mock()
    playing.get_play_state.return_value = True
    idle = mock.Mock()
    idle.get_play_state.return_value = False
    failing = mock.Mock()
    failing.get_play_state.side_effect = RuntimeError("offline")
    controller.services = [
        {"name": "mpd", "service": keep, "state": True},
        {"name": "airplay", "service": playing, "state": True},
        {"name": "usb", "service": idle, "state": False},
        {"name": "spotify", "service": failing, "state": True},
    ]

    controller._stop_other_services("mpd")

    keep.set_play_state.assert_not_called()  # the kept source is untouched
    playing.set_play_state.assert_called_once_with(False)
    idle.set_play_state.assert_not_called()  # already stopped, left alone
    failing.set_play_state.assert_not_called()  # error swallowed, no crash
    _radio, controller = _controller(monkeypatch)
    invalid = mock.Mock()
    invalid.get_play_state.return_value = "yes"
    failing = mock.Mock()
    failing.get_play_state.side_effect = RuntimeError("offline")
    controller.services = [
        {"name": "invalid", "service": invalid, "state": True},
        {"name": "failing", "service": failing, "state": True},
    ]
    controller.check_play_states()
    assert [item["state"] for item in controller.services] == [False, False]


def test_heartbeat_resolution_touch_and_recreation(monkeypatch, tmp_path):
    radio, controller = _controller(monkeypatch)
    path = tmp_path / "alive"
    controller.config = {"watchdog": {"heartbeat_file": str(path)}}
    monkeypatch.delenv("RADIO_HEARTBEAT_FILE", raising=False)
    assert controller._resolve_heartbeat_file() == str(path)
    controller._heartbeat_file = str(path)
    path.unlink()
    controller._touch_heartbeat()
    assert path.exists()
    controller.config = {"watchdog": {"enabled": False}}
    assert controller._resolve_heartbeat_file() == ""
    monkeypatch.setenv("RADIO_HEARTBEAT_FILE", "")
    controller.config = {}
    assert controller._resolve_heartbeat_file() == ""


def test_collect_status_snapshot_handles_metadata_failure(monkeypatch):
    radio, controller = _controller(monkeypatch)
    controller.services = [{"name": "mpd", "service": controller.mpd, "state": True}]
    controller.mpd.name = "mpd"
    controller.mpd.get_metadata.side_effect = RuntimeError("offline")
    build = mock.Mock(return_value={"ok": True})
    monkeypatch.setattr(radio, "build_snapshot", build)
    assert controller._collect_status_snapshot() == {"ok": True}
    assert build.call_args.args[2] == {}
