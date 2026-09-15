"""Host-side configuration, metadata, and failure tests for Spotify."""

import os
import threading
from unittest import mock

from music_source import Metadata
from spotify_service import spotify_service
from spotify_service.spotify_service import SpotifyService


def _service(tmp_path):
    service = SpotifyService.__new__(SpotifyService)
    service.name = "spotify"
    service.spotify_host = "radio"
    service.spotify_port = 3678
    service.spotify_url = "http://radio:3678"
    service.metadata = Metadata(name="", title="", cover="", md5="", state=False)
    service._metadata_lock = threading.RLock()
    service.module_location = str(tmp_path)
    service.config_path = str(tmp_path / "spotify.conf")
    service.binary = "go-librespot"
    service.config_arg = "--config_path"
    return service


def test_config_path_resolution_order(monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setenv("RADIO_SPOTIFY_CONF", "/chosen/config.yml")
    assert service._resolve_config_path() == "/chosen/config.yml"
    monkeypatch.delenv("RADIO_SPOTIFY_CONF")
    monkeypatch.setattr(spotify_service.os, "access", mock.Mock(return_value=True))
    assert service._resolve_config_path().endswith("spotify.conf")
    spotify_service.os.access.return_value = False
    assert service._resolve_config_path() == "/tmp/spotify.conf"


def test_create_config_and_config_dir_argument(monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setattr(spotify_service.socket, "gethostname", mock.Mock(return_value="kitchen"))
    dump = mock.Mock()
    monkeypatch.setattr(spotify_service.yaml, "dump", dump)
    service.create_config_yml()
    config = dump.call_args.args[0]
    assert config["device_name"] == "kitchen"
    assert config["server"]["port"] == 3678
    assert config["audio_device"] == "default"
    assert config["log_level"] == "error"
    service.config_arg = "--config_dir"
    assert service._config_arg_value() == str(tmp_path)


def test_read_metadata_updates_track_and_play_state(monkeypatch, tmp_path):
    service = _service(tmp_path)
    monkeypatch.setattr(
        spotify_service.utility,
        "request_json",
        mock.Mock(return_value={"track": {"artist_names": ["A", "B"], "name": "Song"}}),
    )
    service.read_metadata_once()
    assert service.metadata.name == "A B"
    assert service.metadata.title == "Song"
    assert service.get_play_state() is True


def test_paused_malformed_and_request_failure_are_safe(monkeypatch, tmp_path):
    service = _service(tmp_path)
    request = mock.Mock(
        side_effect=[
            {"paused": True, "track": {"artist_names": [], "name": "Song"}},
            {"track": {"artist_names": "not-a-list", "name": "Bad"}},
            OSError("offline"),
        ]
    )
    monkeypatch.setattr(spotify_service.utility, "request_json", request)
    service.read_metadata_once()
    assert service.get_play_state() is False
    service.metadata.state = True
    service.read_metadata_once()
    assert service.get_play_state() is False
    service.read_metadata_once()  # swallowed, reader remains alive


def test_cover_is_written_atomically_and_not_downloaded_twice(monkeypatch, tmp_path):
    service = _service(tmp_path)
    status = {
        "track": {
            "artist_names": ["Artist"],
            "name": "Song",
            "album_cover_url": "https://cover.test/image",
        }
    }
    monkeypatch.setattr(spotify_service.utility, "request_json", mock.Mock(return_value=status))
    image = mock.Mock(return_value=b"jpeg")
    monkeypatch.setattr(spotify_service.utility, "request_image", image)
    temporary = tmp_path / "temporary-cover"
    descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT)
    monkeypatch.setattr(
        spotify_service.tempfile, "mkstemp", mock.Mock(return_value=(descriptor, str(temporary)))
    )
    replace = mock.Mock()
    monkeypatch.setattr(spotify_service.os, "replace", replace)

    service.read_metadata_once()
    assert service.metadata.cover == "/tmp/spotify_cover.jpg"
    assert service.metadata.md5 == "https://cover.test/image"
    replace.assert_called_once_with(str(temporary), "/tmp/spotify_cover.jpg")
    service.read_metadata_once()
    image.assert_called_once_with("https://cover.test/image")


def test_metadata_returns_snapshot_and_set_play_state_handles_failures(monkeypatch, tmp_path):
    service = _service(tmp_path)
    service.metadata.name = "original"
    snapshot = service.get_metadata()
    snapshot.name = "changed"
    assert service.metadata.name == "original"

    request = mock.Mock(side_effect=[object(), None, OSError("offline")])
    monkeypatch.setattr(spotify_service.utility, "make_request", request)
    assert service.set_play_state(True) is True
    assert service.set_play_state(False) is False
    assert service.set_play_state(True) is False
    assert service.play_index(1) is False
