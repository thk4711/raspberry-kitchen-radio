import threading
import time
from unittest import mock

import dbus
from bluetooth_service.bluetooth_service import (
    A2DP_SINK_UUID,
    MEDIA_PLAYER_IFACE,
    MEDIA_TRANSPORT_IFACE,
    BluetoothService,
)
from music_source import Metadata
from online_artwork import (
    ArtworkResolution,
    MusicBrainzSearchResult,
    OnlineArtworkResolver,
    TrackIdentity,
)


class FakeArtworkResolver:
    def __init__(self):
        self.requests = []
        self.cancel_count = 0
        self.closed = False

    def request(self, track, callback):
        self.requests.append((track, callback))
        return True

    def cancel(self):
        self.cancel_count += 1

    def close(self):
        self.closed = True


def _service_with_resolver(resolver):
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    service._artwork_resolver = resolver
    service._track_identity = None
    service.metadata = Metadata(name="", title="", cover="", md5="", state=False)
    return service


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_constructor_does_not_wait_for_absent_dbus(monkeypatch):
    monkeypatch.setattr(dbus, "SystemBus", mock.Mock(side_effect=dbus.DBusException("absent")))
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
    device_path = "/org/bluez/hci0/dev_AA"
    player_path = f"{device_path}/player0"
    manager = mock.Mock()
    manager.GetManagedObjects.return_value = {
        player_path: {MEDIA_PLAYER_IFACE: {"Device": device_path}},
        f"{device_path}/fd0": {
            MEDIA_TRANSPORT_IFACE: {
                "Device": device_path,
                "UUID": A2DP_SINK_UUID,
                "State": "active",
            }
        },
    }

    def _get(iface, prop):
        assert iface == MEDIA_PLAYER_IFACE
        if prop == "Track":
            return {"Title": "Song", "Artist": "Band", "Album": "LP"}
        raise AssertionError(f"unexpected property {prop}")

    props = mock.Mock()
    props.Get.side_effect = _get

    def _interface(_obj, iface_name):
        return manager if iface_name.endswith("ObjectManager") else props

    monkeypatch.setattr(dbus, "SystemBus", mock.Mock(return_value=mock.Mock()))
    monkeypatch.setattr(dbus, "Interface", mock.Mock(side_effect=_interface))
    monkeypatch.setattr(BluetoothService, "_wait", lambda self, delay: self._stop.wait(0.01))
    service = BluetoothService()
    try:
        assert _wait_until(service.get_play_state)
        metadata = service.get_metadata()
        assert metadata.name == "Band"
        assert metadata.title == "Song"
        assert metadata.cover == ""
        assert metadata.md5 == ""
        assert metadata.state is True
        assert service._track_identity == TrackIdentity("Band", "Song", "LP")
    finally:
        service.close()


def test_avrcp_playing_does_not_claim_source_when_a2dp_is_idle(monkeypatch):
    device_path = "/org/bluez/hci0/dev_AA"
    player_path = f"{device_path}/player0"
    manager = mock.Mock()
    manager.GetManagedObjects.return_value = {
        player_path: {MEDIA_PLAYER_IFACE: {"Device": device_path, "Status": "playing"}},
        f"{device_path}/fd0": {
            MEDIA_TRANSPORT_IFACE: {
                "Device": device_path,
                "UUID": A2DP_SINK_UUID,
                "State": "idle",
            }
        },
    }
    props = mock.Mock()
    props.Get.return_value = {"Title": "AirPlay track", "Artist": "Band"}
    monkeypatch.setattr(
        dbus,
        "Interface",
        lambda _object, interface: manager if interface.endswith("ObjectManager") else props,
    )
    service = _service_with_resolver(None)
    service.service = "org.bluez"

    service._refresh(mock.Mock())

    assert service.get_play_state() is False
    assert service.get_metadata().title == "AirPlay track"
    props.Get.assert_called_once_with(MEDIA_PLAYER_IFACE, "Track")


def test_a2dp_transport_must_belong_to_player_device(monkeypatch):
    player_device = "/org/bluez/hci0/dev_AA"
    other_device = "/org/bluez/hci0/dev_BB"
    manager = mock.Mock()
    manager.GetManagedObjects.return_value = {
        f"{player_device}/player0": {
            MEDIA_PLAYER_IFACE: {"Device": player_device, "Status": "playing"}
        },
        f"{other_device}/fd0": {
            MEDIA_TRANSPORT_IFACE: {
                "Device": other_device,
                "UUID": A2DP_SINK_UUID,
                "State": "active",
            }
        },
    }
    props = mock.Mock()
    props.Get.return_value = {}
    monkeypatch.setattr(
        dbus,
        "Interface",
        lambda _object, interface: manager if interface.endswith("ObjectManager") else props,
    )
    service = _service_with_resolver(None)
    service.service = "org.bluez"

    service._refresh(mock.Mock())

    assert service.get_play_state() is False


def test_dbus_refresh_does_not_block_on_artwork_provider(monkeypatch, tmp_path):
    search_started = threading.Event()
    release_search = threading.Event()

    class BlockingMusicBrainz:
        def search(self, _track):
            search_started.set()
            release_search.wait(1)
            return MusicBrainzSearchResult(recordings=(), status_code=200)

        def close(self):
            pass

    class UnusedCoverArt:
        def close(self):
            pass

    resolver = OnlineArtworkResolver(
        musicbrainz=BlockingMusicBrainz(),
        cover_art=UnusedCoverArt(),
        cache_directory=str(tmp_path / "cache"),
        publication_path=str(tmp_path / "bluetooth_cover.jpg"),
    )
    service = _service_with_resolver(resolver)
    service.service = "org.bluez"
    device_path = "/org/bluez/hci0/dev_AA"
    manager = mock.Mock()
    manager.GetManagedObjects.return_value = {
        f"{device_path}/player0": {MEDIA_PLAYER_IFACE: {"Device": device_path}},
        f"{device_path}/fd0": {
            MEDIA_TRANSPORT_IFACE: {
                "Device": device_path,
                "UUID": A2DP_SINK_UUID,
                "State": "active",
            }
        },
    }
    props = mock.Mock()
    props.Get.return_value = {"Title": "Song", "Artist": "Band"}
    monkeypatch.setattr(
        dbus,
        "Interface",
        lambda _object, interface: manager if interface.endswith("ObjectManager") else props,
    )
    try:
        started = time.monotonic()
        service._refresh(mock.Mock())

        assert time.monotonic() - started < 0.1
        assert search_started.wait(1)
        assert service.get_metadata().title == "Song"
    finally:
        release_search.set()
        resolver.close()


def test_no_player_reports_not_playing(monkeypatch):
    manager = mock.Mock()
    manager.GetManagedObjects.return_value = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
    }
    monkeypatch.setattr(dbus, "SystemBus", mock.Mock(return_value=mock.Mock()))
    monkeypatch.setattr(dbus, "Interface", mock.Mock(return_value=manager))
    monkeypatch.setattr(BluetoothService, "_wait", lambda self, delay: self._stop.wait(0.01))
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


def test_track_navigation_issues_avrcp_commands(monkeypatch):
    player = mock.Mock()
    monkeypatch.setattr(dbus, "Interface", mock.Mock(return_value=player))
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    service.service = "org.bluez"
    service._bus = mock.Mock()
    service._player_path = "/org/bluez/hci0/dev_AA/player0"

    assert service.previous_track() is True
    assert service.next_track() is True
    player.Previous.assert_called_once_with()
    player.Next.assert_called_once_with()


def test_track_navigation_disconnect_is_soft_failure(monkeypatch):
    player = mock.Mock()
    player.Next.side_effect = dbus.DBusException("disconnected")
    monkeypatch.setattr(dbus, "Interface", mock.Mock(return_value=player))
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    service.service = "org.bluez"
    service._bus = mock.Mock()
    service._player_path = "/org/bluez/hci0/dev_AA/player0"

    assert service.next_track() is False
    assert service._player_path is None
    assert service.previous_track() is False


def test_play_index_unsupported():
    service = BluetoothService.__new__(BluetoothService)
    service._lock = threading.RLock()
    assert service.play_index(1) is False


def test_track_change_clears_artwork_and_requests_resolution():
    resolver = FakeArtworkResolver()
    service = _service_with_resolver(resolver)
    service.metadata = Metadata(
        name="Old Artist", title="Old Song", cover="/tmp/old.jpg", md5="old", state=True
    )
    service._track_identity = TrackIdentity("Old Artist", "Old Song")

    service._update_track(
        TrackIdentity("New Artist", "New Song", "Album"), "New Artist", "New Song", True
    )

    metadata = service.get_metadata()
    assert metadata.name == "New Artist"
    assert metadata.title == "New Song"
    assert metadata.cover == ""
    assert metadata.md5 == ""
    assert metadata.state is True
    assert [request[0] for request in resolver.requests] == [
        TrackIdentity("New Artist", "New Song", "Album")
    ]


def test_successful_resolution_is_retained_during_same_track_poll():
    resolver = FakeArtworkResolver()
    service = _service_with_resolver(resolver)
    track = TrackIdentity("Artist", "Song", "Album")

    service._update_track(track, "Artist", "Song", True)
    callback = resolver.requests[0][1]
    callback(ArtworkResolution(track.cache_key, "/tmp/bluetooth_cover.jpg", "fingerprint"))
    service._update_track(TrackIdentity(" artist ", "song", "album"), " artist ", "song", False)

    metadata = service.get_metadata()
    assert metadata.cover == "/tmp/bluetooth_cover.jpg"
    assert metadata.md5 == "fingerprint"
    assert metadata.state is False
    assert len(resolver.requests) == 1


def test_stale_artwork_resolution_is_ignored():
    resolver = FakeArtworkResolver()
    service = _service_with_resolver(resolver)
    first = TrackIdentity("Artist", "First")
    second = TrackIdentity("Artist", "Second")

    service._update_track(first, first.artist, first.title, True)
    stale_callback = resolver.requests[0][1]
    service._update_track(second, second.artist, second.title, True)
    stale_callback(ArtworkResolution(first.cache_key, "/tmp/bluetooth_cover.jpg", "stale"))

    metadata = service.get_metadata()
    assert metadata.title == "Second"
    assert metadata.cover == ""
    assert metadata.md5 == ""


def test_incomplete_track_and_disconnect_cancel_artwork():
    resolver = FakeArtworkResolver()
    service = _service_with_resolver(resolver)
    track = TrackIdentity("Artist", "Song")

    service._update_track(track, track.artist, track.title, True)
    service._update_track(None, "Artist", "", True)
    assert resolver.cancel_count == 1
    assert service.get_metadata().cover == ""

    service._update_track(track, track.artist, track.title, True)
    service._clear_track()
    metadata = service.get_metadata()
    assert resolver.cancel_count == 2
    assert metadata.name == ""
    assert metadata.title == ""
    assert metadata.state is False


def test_close_stops_artwork_resolver():
    resolver = FakeArtworkResolver()
    service = _service_with_resolver(resolver)
    service._stop = threading.Event()

    service.close()

    assert service._stop.is_set()
    assert resolver.closed
