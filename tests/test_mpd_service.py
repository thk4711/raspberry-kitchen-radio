"""Host-side behavior and recovery tests for the MPD backend."""

import subprocess
from unittest import mock

from mpd_service import mpd_service
from mpd_service.mpd_service import MPDService
from music_source import Metadata


def _service():
    service = MPDService.__new__(MPDService)
    service.name = "mpd"
    service.module_location = "/app/mpd_service"
    service.stations = [
        {"name": "News", "url": "https://example.test/news", "logo": "news.png"},
        {"name": "Music", "url": "https://example.test/music", "logo": "music.png"},
    ]
    service.current_station = 0
    service.desired_play_state = False
    service.metadata = Metadata(name="", title="", cover="", md5="", state=False)
    return service


def test_run_command_returns_output_even_for_nonzero_status(monkeypatch):
    run = mock.Mock(
        return_value=subprocess.CompletedProcess([], 1, stdout=" output\n", stderr="failed\n")
    )
    monkeypatch.setattr(mpd_service.subprocess, "run", run)
    assert _service()._run_mpc_command("current -f %title%") == "output"
    run.assert_called_once_with(["mpc", "current", "-f", "%title%"], capture_output=True, text=True)


def test_run_command_exception_returns_none(monkeypatch):
    monkeypatch.setattr(mpd_service.subprocess, "run", mock.Mock(side_effect=OSError("missing")))
    assert _service()._run_mpc_command("status") is None


def test_get_and_set_play_state():
    service = _service()
    service._run_mpc_command = mock.Mock(side_effect=["volume: 20%\n[playing]", "stopped"])
    assert service.get_play_state() is True
    assert service.get_play_state() is False

    service._run_mpc_command.reset_mock()
    service._run_mpc_command.side_effect = None
    service._run_mpc_command.return_value = ""
    assert service.set_play_state(True) is True
    assert service.desired_play_state is True
    assert service.set_play_state(False) is True
    assert service._run_mpc_command.call_args_list == [mock.call("play"), mock.call("stop")]


def test_play_index_runs_clear_add_play_and_rejects_invalid_index():
    service = _service()
    service._run_mpc_command = mock.Mock(return_value="")
    assert service.play_index(2) is True
    assert service.current_station == 1
    assert service._run_mpc_command.call_args_list == [
        mock.call("clear"),
        mock.call("add https://example.test/music"),
        mock.call("play"),
    ]
    assert service.play_index(99) is False


def test_metadata_filters_status_lines_and_uses_station_logo(monkeypatch):
    service = _service()
    service.get_play_state = mock.Mock(return_value=True)
    service._run_mpc_command = mock.Mock(return_value="volume: 22%\n[playing]\nReal title\n")
    monkeypatch.setattr(mpd_service, "_logo_path", mock.Mock(return_value="/logo/news.png"))
    metadata = service.get_metadata()
    assert metadata.name == "News"
    assert metadata.title == "Real title "
    assert metadata.cover == "/logo/news.png"
    assert metadata.state is True


def test_metadata_clears_title_when_command_has_no_metadata(monkeypatch):
    service = _service()
    service.metadata.title = "stale"
    service.get_play_state = mock.Mock(return_value=False)
    service._run_mpc_command = mock.Mock(return_value=None)
    monkeypatch.setattr(mpd_service, "_logo_path", mock.Mock(return_value="logo"))
    assert service.get_metadata().title == ""


def test_check_state_reconciles_and_recovers_after_failure(monkeypatch):
    service = _service()
    service.get_play_state = mock.Mock(return_value=False)
    service.set_play_state = mock.Mock()
    service.check_state(True)
    service.set_play_state.assert_called_once_with(True)

    service.get_play_state.side_effect = RuntimeError("mpd failed")
    service.set_play_state.reset_mock()
    restart = mock.Mock()
    monkeypatch.setattr(mpd_service.utility, "restart_systemd_service", restart)
    monkeypatch.setattr(mpd_service, "sleep", mock.Mock())
    service.check_state(True)
    assert service.set_play_state.call_args_list == [mock.call(False), mock.call(True)]
    restart.assert_called_once_with("mpd.service")


def test_logo_path_rejects_traversal_and_prefers_managed_file(monkeypatch):
    assert mpd_service._logo_path("/module", "../secret.png") == ""
    monkeypatch.setattr(mpd_service.os.path, "isfile", mock.Mock(return_value=True))
    assert mpd_service._logo_path("/module", "station.png").endswith("logos/station.png")
