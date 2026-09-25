"""Bluetooth A2DP-sink playback source driven by BlueZ over D-Bus.

Unlike the AirPlay and Spotify backends, this service does **not** launch any
media daemon itself. On the appliance image the Bluetooth stack is owned by two
init scripts: Buildroot's ``/etc/init.d/S40bluetoothd`` runs ``bluetoothd`` (the
BlueZ stack, with ``--experimental`` set via ``/etc/default/bluetoothd`` so AVRCP
metadata is exposed), and ``/etc/init.d/S42bluetooth`` adds the no-PIN
auto-pairing agent, ``bluealsa`` (the A2DP receiver) and ``bluealsa-aplay``
(which routes the received PCM to the ALSA default device / I2S DAC). This class
is a pure *consumer* of the BlueZ D-Bus API:

* It watches ``org.bluez`` for a connected device exposing the AVRCP
  ``org.bluez.MediaPlayer1`` interface and reads its ``Track``
  (title/artist/album) properties for the display. Actual playback state comes
  from the device's A2DP ``org.bluez.MediaTransport1`` interface; AVRCP status
  can describe media playing through another output such as AirPlay.
* ``set_play_state`` issues AVRCP ``Play`` / ``Pause`` so switching sources on
  the radio actually pauses the phone.

A2DP/AVRCP carries no cover art, so an optional online-artwork resolver can add
it asynchronously from the track metadata. Until resolution succeeds,
``cover``/``md5`` stay empty and the display renders its Bluetooth fallback.
The D-Bus reconnect/backoff loop mirrors :class:`AirplayService` so a
``bluetoothd`` restart is transparent.
"""

import logging
import os
import threading
from typing import Any, Optional

import dbus
from music_source import Metadata, MusicSource
from online_artwork import ArtworkResolution, OnlineArtworkResolver, TrackIdentity

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
BLUEZ_ROOT = "/"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
MEDIA_PLAYER_IFACE = "org.bluez.MediaPlayer1"
MEDIA_TRANSPORT_IFACE = "org.bluez.MediaTransport1"
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
PLAYING_TRANSPORT_STATES = frozenset(("pending", "active"))


class BluetoothService(MusicSource):
    """Expose a connected Bluetooth A2DP source as a radio :class:`MusicSource`.

    Args:
        service: BlueZ D-Bus well-known name (override for tests).
        artwork_resolver: Optional asynchronous resolver. ``None`` disables
            online artwork lookup.
    """

    def __init__(
        self,
        service: str = BLUEZ_SERVICE,
        artwork_resolver: Optional[OnlineArtworkResolver] = None,
    ) -> None:
        self.name = "bluetooth"
        self.service = os.environ.get("RADIO_BLUETOOTH_DBUS_SERVICE", service)
        self._artwork_resolver = artwork_resolver
        self._stop = threading.Event()
        self._lock = threading.RLock()
        # Cached D-Bus handles for the currently connected media player. They
        # are refreshed by the worker thread and cleared on disconnect so a
        # reconnect transparently rebinds.
        self._bus: Optional[Any] = None
        self._player_path: Optional[str] = None
        self._track_identity: Optional[TrackIdentity] = None
        self.metadata = Metadata(name="", title="", cover="", md5="", state=False)
        self.dbus_thread = threading.Thread(
            target=self._dbus_worker, daemon=True, name="bluetooth-dbus"
        )
        self.dbus_thread.start()

    # -- background D-Bus discovery -------------------------------------------

    def _dbus_worker(self) -> None:
        """Discover the connected media player and poll its metadata.

        Reconnects with exponential backoff (identical to the AirPlay worker)
        so a ``bluetoothd`` restart, or a phone connecting later, is handled
        without user intervention.
        """
        delay = 1.0
        while not self._stop.is_set():
            try:
                bus = dbus.SystemBus()
                with self._lock:
                    self._bus = bus
                delay = 1.0
                while not self._wait(1):
                    try:
                        self._refresh(bus)
                    except dbus.DBusException:
                        break
                with self._lock:
                    if self._bus is bus:
                        self._bus = None
                        self._player_path = None
                self._clear_track()
            except dbus.DBusException as exc:
                logger.warning("Bluetooth D-Bus unavailable; retrying: %s", exc)
            self._wait(delay)
            delay = min(delay * 2, 30.0)

    def _wait(self, delay: float) -> bool:
        """Interruptible wait, split out so retry timing is testable."""
        return self._stop.wait(delay)

    def _transport_is_playing(self, objects: Any, player_path: str) -> bool:
        """Return whether the player's device has a streaming A2DP transport."""
        player = objects.get(player_path, {}).get(MEDIA_PLAYER_IFACE, {})
        device = str(player.get("Device", ""))
        if not device:
            return False

        for interfaces in objects.values():
            transport = interfaces.get(MEDIA_TRANSPORT_IFACE)
            if transport is None:
                continue
            if (
                str(transport.get("Device", "")) == device
                and str(transport.get("UUID", "")).lower() == A2DP_SINK_UUID
                and str(transport.get("State", "")) in PLAYING_TRANSPORT_STATES
            ):
                return True
        return False

    def _find_player_path(self, objects: Any) -> Optional[str]:
        """Return an AVRCP player path, preferring one whose A2DP stream is active."""
        paths = []
        for path, interfaces in objects.items():
            if MEDIA_PLAYER_IFACE in interfaces:
                paths.append(str(path))
        return next(
            (path for path in paths if self._transport_is_playing(objects, path)),
            paths[0] if paths else None,
        )

    def _refresh(self, bus: Any) -> None:
        """Poll the connected player and update the cached metadata snapshot."""
        manager = dbus.Interface(bus.get_object(self.service, BLUEZ_ROOT), OBJECT_MANAGER_IFACE)
        objects = manager.GetManagedObjects()
        path = self._find_player_path(objects)
        with self._lock:
            self._player_path = path
        if path is None:
            self._clear_track()
            return

        props = dbus.Interface(bus.get_object(self.service, path), PROPERTIES_IFACE)
        try:
            track = props.Get(MEDIA_PLAYER_IFACE, "Track")
        except dbus.DBusException:
            track = {}

        title = str(track.get("Title", "")) if track else ""
        artist = str(track.get("Artist", "")) if track else ""
        album = str(track.get("Album", "")) if track else ""
        track_identity = TrackIdentity.from_metadata(artist, title, album)
        self._update_track(
            track_identity,
            artist,
            title,
            self._transport_is_playing(objects, path),
        )

    def _update_track(
        self,
        track_identity: Optional[TrackIdentity],
        artist: str,
        title: str,
        playing: bool,
    ) -> None:
        """Update text immediately and start lookup only for a changed track."""
        with self._lock:
            previous_key = (
                self._track_identity.cache_key if self._track_identity is not None else None
            )
            current_key = track_identity.cache_key if track_identity is not None else None
            track_changed = previous_key != current_key
            cover = "" if track_changed else self.metadata.cover
            fingerprint = "" if track_changed else self.metadata.md5
            self._track_identity = track_identity
            self.metadata = Metadata(
                name=artist,
                title=title,
                cover=cover,
                md5=fingerprint,
                state=playing,
            )
        if not track_changed or self._artwork_resolver is None:
            return
        if track_identity is None:
            self._artwork_resolver.cancel()
        else:
            self._artwork_resolver.request(track_identity, self._apply_artwork)

    def _apply_artwork(self, resolution: ArtworkResolution) -> None:
        """Apply a resolver result only while its normalized track is current."""
        with self._lock:
            if (
                self._track_identity is None
                or self._track_identity.cache_key != resolution.track_key
            ):
                return
            self.metadata = Metadata(
                name=self.metadata.name,
                title=self.metadata.title,
                cover=resolution.cover_path,
                md5=resolution.fingerprint,
                state=self.metadata.state,
            )

    def _clear_track(self) -> None:
        """Clear disconnected-track state and invalidate any pending artwork."""
        with self._lock:
            had_track = self._track_identity is not None
            self._track_identity = None
            self.metadata = Metadata(name="", title="", cover="", md5="", state=False)
        if had_track and self._artwork_resolver is not None:
            self._artwork_resolver.cancel()

    # -- MusicSource contract --------------------------------------------------

    def get_play_state(self) -> bool:
        with self._lock:
            return self.metadata.state

    def get_metadata(self) -> Metadata:
        with self._lock:
            return self.metadata.snapshot()

    def set_play_state(self, desired_state: bool) -> bool:
        """Issue an AVRCP Play/Pause to the connected phone.

        Returns ``True`` when a command was dispatched, ``False`` when nothing
        is connected or the call failed (so the controller can log it without
        crashing the metadata loop).
        """
        return self._send_player_command("Play" if desired_state else "Pause")

    def _send_player_command(self, command: str) -> bool:
        """Invoke one AVRCP command on the connected BlueZ media player."""
        with self._lock:
            bus = self._bus
            path = self._player_path
        if bus is None or path is None:
            return False
        try:
            player = dbus.Interface(bus.get_object(self.service, path), MEDIA_PLAYER_IFACE)
            method = getattr(player, command)
            method()
            return True
        except (AttributeError, dbus.DBusException) as exc:
            logger.warning("Bluetooth %s command failed: %s", command, exc)
            with self._lock:
                if self._bus is bus:
                    self._player_path = None
            return False

    def play_index(self, index: int) -> bool:
        """Bluetooth has no button-selectable presets."""
        return False

    def next_track(self) -> bool:
        """Issue AVRCP Next to the connected phone."""
        return self._send_player_command("Next")

    def previous_track(self) -> bool:
        """Issue AVRCP Previous to the connected phone."""
        return self._send_player_command("Previous")

    def close(self) -> None:
        """Stop the background D-Bus and artwork workers."""
        self._stop.set()
        if self._artwork_resolver is not None:
            self._artwork_resolver.close()
