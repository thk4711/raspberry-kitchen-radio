"""Integration coverage for Bluetooth online-artwork fallback behavior."""

import hashlib
import importlib
import io
import threading
import time
import types

import pytest
from bluetooth_service.bluetooth_service import BluetoothService
from display import logo_fallback, panel_factory
from music_source import Metadata
from online_artwork import (
    ArtworkResolution,
    CoverArtFailure,
    CoverArtResult,
    MusicBrainzFailure,
    MusicBrainzSearchResult,
    OnlineArtworkResolver,
    TrackIdentity,
)
from PIL import Image


class _FakePanel:
    width = 240
    height = 280

    def __init__(self, *args, **kwargs):
        pass

    def Init(self):
        pass

    def bl_DutyCycle(self, duty):
        pass

    def ShowFullFrame(self, pixels):
        pass


class _InertThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class _ControlledResolver:
    def __init__(self):
        self.requests = []
        self.cancel_count = 0

    def request(self, track, callback):
        self.requests.append((track, callback))
        return True

    def cancel(self):
        self.cancel_count += 1


class _MusicBrainz:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def search(self, track):
        self.calls.append(track)
        return self.result

    def close(self):
        pass


class _CoverArt:
    def __init__(self, failure=None, color=(210, 30, 20)):
        self.failure = failure
        self.color = color
        self.calls = []

    def fetch(self, match, destination):
        self.calls.append((match, destination))
        if self.failure is not None:
            return CoverArtResult(failure=self.failure)
        output = io.BytesIO()
        Image.new("RGB", (120, 120), self.color).save(output, format="JPEG")
        data = output.getvalue()
        with open(destination, "wb") as artwork:
            artwork.write(data)
        return CoverArtResult(fingerprint=hashlib.sha256(data).hexdigest(), status_code=200)

    def close(self):
        pass


@pytest.fixture
def display(monkeypatch):
    monkeypatch.setattr(panel_factory, "get_panel_class", lambda _name: _FakePanel)
    import display.display_control as display_control

    display_control = importlib.reload(display_control)
    monkeypatch.setattr(
        display_control,
        "threading",
        types.SimpleNamespace(Thread=_InertThread, RLock=threading.RLock),
    )
    monkeypatch.setattr(display_control, "monotonic", lambda: 0.0)
    return display_control.DisplayController()


def _service(resolver=None):
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    service._artwork_resolver = resolver
    service._track_identity = None
    service.metadata = Metadata(name="", title="", cover="", md5="", state=False)
    return service


def _track(title="Song"):
    return TrackIdentity("Artist", title, "Album")


def _recording(track):
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "score": 100,
        "title": track.title,
        "artist-credit": [{"name": track.artist}],
        "releases": [
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "title": track.album,
                "status": "Official",
                "release-group": {
                    "id": "33333333-3333-4333-8333-333333333333",
                    "title": track.album,
                    "primary-type": "Album",
                },
            }
        ],
    }


def _online_resolver(tmp_path, search_result, cover_art=None):
    musicbrainz = _MusicBrainz(search_result)
    cover_art = cover_art or _CoverArt()
    resolver = OnlineArtworkResolver(
        musicbrainz=musicbrainz,
        cover_art=cover_art,
        cache_directory=str(tmp_path / "cache"),
        publication_path=str(tmp_path / "bluetooth_cover.jpg"),
        negative_ttl=60,
        transient_retry=60,
    )
    return resolver, musicbrainz, cover_art


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _sync_display(display, service, source="bluetooth"):
    metadata = service.get_metadata()
    display.update_metadata(
        metadata.name,
        metadata.title,
        metadata.cover,
        metadata.md5,
        state=metadata.state,
        art_mode="cover",
        source=source,
    )
    display._transient.clear_crossfade()


def _assert_bluetooth_glyph(display, service):
    metadata = service.get_metadata()
    assert metadata.cover == ""
    assert metadata.md5 == ""
    _sync_display(display, service)
    art = display._build_art_layer()
    tile = display._fallback_logo()
    assert art.size == (display.width, display.height)
    assert tile.getpixel((2, tile.height // 2))[:3] == logo_fallback.BLUETOOTH_TILE_COLOR


def test_artwork_disabled_renders_bluetooth_glyph(display):
    service = _service()
    track = _track()
    service._update_track(track, track.artist, track.title, True)
    _assert_bluetooth_glyph(display, service)


def test_incomplete_metadata_renders_bluetooth_glyph(display):
    resolver = _ControlledResolver()
    service = _service(resolver)
    service._update_track(None, "Artist", "", True)
    assert resolver.requests == []
    _assert_bluetooth_glyph(display, service)


def test_pending_lookup_renders_bluetooth_glyph(display):
    resolver = _ControlledResolver()
    service = _service(resolver)
    track = _track()
    service._update_track(track, track.artist, track.title, True)
    assert len(resolver.requests) == 1
    _assert_bluetooth_glyph(display, service)


def test_no_confident_match_renders_bluetooth_glyph(display, tmp_path):
    resolver, musicbrainz, cover_art = _online_resolver(
        tmp_path, MusicBrainzSearchResult(recordings=(), status_code=200)
    )
    service = _service(resolver)
    track = _track()
    try:
        service._update_track(track, track.artist, track.title, True)
        assert _wait_until(lambda: len(musicbrainz.calls) == 1)
        assert _wait_until(lambda: track.cache_key in resolver._negative)
        assert cover_art.calls == []
        _assert_bluetooth_glyph(display, service)
    finally:
        resolver.close()


@pytest.mark.parametrize("failure", [MusicBrainzFailure.CONNECTION, MusicBrainzFailure.TLS])
def test_offline_or_tls_failure_renders_bluetooth_glyph(display, tmp_path, failure):
    resolver, musicbrainz, _cover_art = _online_resolver(
        tmp_path, MusicBrainzSearchResult(failure=failure)
    )
    service = _service(resolver)
    track = _track()
    try:
        service._update_track(track, track.artist, track.title, True)
        assert _wait_until(lambda: len(musicbrainz.calls) == 1)
        assert _wait_until(lambda: track.cache_key in resolver._negative)
        _assert_bluetooth_glyph(display, service)
    finally:
        resolver.close()


@pytest.mark.parametrize(
    "failure", [CoverArtFailure.INVALID_IMAGE, CoverArtFailure.RESPONSE_TOO_LARGE]
)
def test_corrupt_or_oversized_image_renders_bluetooth_glyph(display, tmp_path, failure):
    track = _track()
    cover_art = _CoverArt(failure=failure)
    resolver, _musicbrainz, cover_art = _online_resolver(
        tmp_path,
        MusicBrainzSearchResult(recordings=(_recording(track),), status_code=200),
        cover_art,
    )
    service = _service(resolver)
    try:
        service._update_track(track, track.artist, track.title, True)
        assert _wait_until(lambda: len(cover_art.calls) == 1)
        assert _wait_until(lambda: track.cache_key in resolver._negative)
        _assert_bluetooth_glyph(display, service)
    finally:
        resolver.close()


def test_valid_downloaded_cover_renders_full_bleed(display, tmp_path):
    track = _track()
    resolver, _musicbrainz, _cover_art = _online_resolver(
        tmp_path,
        MusicBrainzSearchResult(recordings=(_recording(track),), status_code=200),
    )
    service = _service(resolver)
    try:
        service._update_track(track, track.artist, track.title, True)
        assert _wait_until(lambda: bool(service.get_metadata().cover))
        _sync_display(display, service)
        art = display._build_art_layer()
        red, green, blue = art.getpixel((display.width // 2, display.height // 2))
        assert red > green and red > blue
    finally:
        resolver.close()


def test_next_track_clears_old_cover_immediately(display, tmp_path):
    resolver = _ControlledResolver()
    service = _service(resolver)
    first = _track("First")
    second = _track("Second")
    cover = tmp_path / "old-cover.jpg"
    Image.new("RGB", (120, 120), (210, 30, 20)).save(cover)

    service._update_track(first, first.artist, first.title, True)
    resolver.requests[0][1](ArtworkResolution(first.cache_key, str(cover), "old"))
    assert service.get_metadata().cover == str(cover)

    service._update_track(second, second.artist, second.title, True)
    _assert_bluetooth_glyph(display, service)


def test_stale_previous_track_result_is_ignored(display, tmp_path):
    resolver = _ControlledResolver()
    service = _service(resolver)
    first = _track("First")
    second = _track("Second")
    cover = tmp_path / "stale-cover.jpg"
    Image.new("RGB", (120, 120), (210, 30, 20)).save(cover)

    service._update_track(first, first.artist, first.title, True)
    stale_callback = resolver.requests[0][1]
    service._update_track(second, second.artist, second.title, True)
    stale_callback(ArtworkResolution(first.cache_key, str(cover), "stale"))
    _assert_bluetooth_glyph(display, service)


def test_source_change_does_not_replace_usb_fallback_with_stale_bluetooth_art(display, tmp_path):
    resolver = _ControlledResolver()
    service = _service(resolver)
    track = _track()
    cover = tmp_path / "late-cover.jpg"
    Image.new("RGB", (120, 120), (210, 30, 20)).save(cover)

    service._update_track(track, track.artist, track.title, True)
    late_callback = resolver.requests[0][1]
    _sync_display(display, service)
    display._build_art_layer()

    display.update_metadata(
        "USB Audio", "Connected source", "", "", state=True, art_mode="cover", source="usb"
    )
    usb_art_before = display._build_art_layer().tobytes()
    late_callback(ArtworkResolution(track.cache_key, str(cover), "late"))
    usb_art_after = display._build_art_layer()

    usb_tile = display._fallback_logo()
    assert display.metadata["source"] == "usb"
    assert display.metadata["cover"] == ""
    assert usb_art_after.tobytes() == usb_art_before
    assert usb_tile.getpixel((2, usb_tile.height // 2))[:3] == logo_fallback.USB_TILE_COLOR
