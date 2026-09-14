"""Tests for the USB Audio gadget ``MusicSource`` integration."""

import subprocess
import sys
import threading
import types
from unittest import mock

from usb_audio_service.usb_audio_service import USBAudioService


def _result(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _amixer_output(rate: int) -> str:
    return (
        "numid=1,iface=PCM,name='Capture Rate'\n"
        "  ; type=INTEGER,access=r--v----,values=1,min=48000,max=48000,step=0\n"
        f"  : values={rate}\n"
    )


def _import_radio(monkeypatch):
    """Import the controller without loading the Raspberry Pi display driver."""
    monkeypatch.setitem(sys.modules, "yaml", types.ModuleType("yaml"))
    display_module = types.ModuleType("display.display_control")
    display_module.DisplayController = mock.Mock()
    monkeypatch.setitem(sys.modules, "display.display_control", display_module)
    import radio

    return radio


def test_active_capture_stream_reports_playing(monkeypatch, tmp_path):
    run = mock.Mock(return_value=_result(_amixer_output(48000)))
    monkeypatch.setattr(subprocess, "run", run)
    service = USBAudioService(inhibit_file=str(tmp_path / "inhibited"))

    assert service.get_play_state() is True
    assert run.call_args.args[0][0] == "/usr/bin/amixer"


def test_zero_missing_or_malformed_capture_rate_is_inactive(monkeypatch, tmp_path):
    service = USBAudioService(inhibit_file=str(tmp_path / "inhibited"))
    for result in (
        _result(_amixer_output(0)),
        _result("unexpected output\n"),
        _result(returncode=1),
    ):
        monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=result))
        assert service.get_play_state() is False


def test_amixer_failure_is_inactive(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", mock.Mock(side_effect=OSError("missing")))
    service = USBAudioService(inhibit_file=str(tmp_path / "inhibited"))
    assert service.get_play_state() is False


def test_stop_inhibits_open_stream_until_host_closes(monkeypatch, tmp_path):
    inhibit = tmp_path / "inhibited"
    state = {"rate": 48000}

    def run(*args, **kwargs):
        return _result(_amixer_output(state["rate"]))

    monkeypatch.setattr(subprocess, "run", run)
    service = USBAudioService(inhibit_file=str(inhibit))

    assert service.get_play_state() is True
    assert service.set_play_state(False) is True
    assert inhibit.exists()
    assert service.get_play_state() is False

    state["rate"] = 0
    assert service.get_play_state() is False
    assert not inhibit.exists()

    state["rate"] = 48000
    assert service.get_play_state() is True


def test_start_removes_inhibition(monkeypatch, tmp_path):
    inhibit = tmp_path / "inhibited"
    inhibit.touch()
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=_result(_amixer_output(48000))))
    service = USBAudioService(inhibit_file=str(inhibit))

    assert service.set_play_state(True) is True
    assert not inhibit.exists()
    assert service.get_play_state() is True


def test_constructor_removes_stale_inhibition(monkeypatch, tmp_path):
    inhibit = tmp_path / "inhibited"
    inhibit.touch()
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=_result(_amixer_output(48000))))
    service = USBAudioService(inhibit_file=str(inhibit))

    assert not inhibit.exists()
    assert service.get_play_state() is True


def test_metadata_and_unsupported_preset(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", mock.Mock(return_value=_result(_amixer_output(48000))))
    service = USBAudioService(inhibit_file=str(tmp_path / "inhibited"))

    metadata = service.get_metadata()
    assert metadata.name == "USB Audio"
    assert metadata.title == "Connected source"
    assert metadata.cover == ""
    assert metadata.state is True
    assert service.play_index(1) is False


def test_controller_selects_new_usb_stream_and_stops_previous_source(monkeypatch):
    """Exercise the controller boundary without constructing Pi hardware."""
    radio = _import_radio(monkeypatch)

    previous = mock.Mock()
    previous.get_play_state.return_value = True
    usb = mock.Mock()
    usb.get_play_state.return_value = True

    controller = radio.RadioController.__new__(radio.RadioController)
    controller._state_lock = threading.RLock()
    controller.active_service = previous
    controller.update_metadata = mock.Mock()
    controller.services = [
        {"name": "usb", "service": usb, "state": False},
        {"name": "mpd", "service": previous, "state": True},
    ]

    controller.check_play_states()

    assert controller.active_service is usb
    previous.set_play_state.assert_called_once_with(False)


def test_controller_prepares_sources_before_starting_arbitration(monkeypatch):
    """An already-open USB stream must outrank the default MPD startup."""
    radio = _import_radio(monkeypatch)

    events = []
    controller = radio.RadioController.__new__(radio.RadioController)
    controller.source_flags = {"usb_audio": True, "internet_radio": True}
    controller.adc_controller = mock.Mock()
    controller.adc_controller.initialize_volume.return_value = 25
    controller.adc_controller.read_adc_switch.return_value = True
    controller.handle_switch_state_change = mock.Mock(
        side_effect=lambda number, state: events.append(("switch", state))
    )
    controller.mpd = mock.Mock()
    controller.mpd.play_index.side_effect = lambda index: events.append(("mpd", index))
    controller.metadata_loop = mock.Mock()

    class FakeThread:
        def __init__(self, *, target, daemon):
            assert target == controller.metadata_loop
            assert daemon is True

        def start(self):
            events.append(("metadata", True))

    monkeypatch.setattr(radio.threading, "Thread", FakeThread)
    monkeypatch.setattr(radio, "sleep", mock.Mock(side_effect=KeyboardInterrupt))

    controller.start()

    assert events == [
        ("switch", True),
        ("mpd", 1),
        ("metadata", True),
    ]
