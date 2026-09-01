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
  ``org.bluez.MediaPlayer1`` interface and reads its ``Status`` (play/pause)
  and ``Track`` (title/artist/album) properties for the display.
* ``set_play_state`` issues AVRCP ``Play`` / ``Pause`` so switching sources on
  the radio actually pauses the phone.

A2DP/AVRCP carries no cover art, so :class:`Metadata` ``cover``/``md5`` stay
empty; the display already renders text-only metadata (Spotify starts the same
way). The D-Bus reconnect/backoff loop mirrors :class:`AirplayService` so a
``bluetoothd`` restart is transparent.
"""

import logging
import os
import threading
from typing import Any, Optional

import dbus
from music_source import Metadata, MusicSource

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
BLUEZ_ROOT = "/"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
MEDIA_PLAYER_IFACE = "org.bluez.MediaPlayer1"


class BluetoothService(MusicSource):
    """Expose a connected Bluetooth A2DP source as a radio :class:`MusicSource`.

    Args:
        service: BlueZ D-Bus well-known name (override for tests).
    """

    def __init__(self, service: str = BLUEZ_SERVICE) -> None:
        self.name = "bluetooth"
        self.service = os.environ.get("RADIO_BLUETOOTH_DBUS_SERVICE", service)
        self._stop = threading.Event()
        self._lock = threading.RLock()
        # Cached D-Bus handles for the currently connected media player. They
        # are refreshed by the worker thread and cleared on disconnect so a
        # reconnect transparently rebinds.
        self._bus: Optional[Any] = None
        self._player_path: Optional[str] = None
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
            except dbus.DBusException as exc:
                logger.warning("Bluetooth D-Bus unavailable; retrying: %s", exc)
            self._wait(delay)
            delay = min(delay * 2, 30.0)

    def _wait(self, delay: float) -> bool:
        """Interruptible wait, split out so retry timing is testable."""
        return self._stop.wait(delay)

    def _find_player_path(self, bus: Any) -> Optional[str]:
        """Return the object path of a connected ``MediaPlayer1``, if any."""
        manager = dbus.Interface(
            bus.get_object(self.service, BLUEZ_ROOT), OBJECT_MANAGER_IFACE
        )
        objects = manager.GetManagedObjects()
        for path, interfaces in objects.items():
            if MEDIA_PLAYER_IFACE in interfaces:
                return str(path)
        return None

    def _refresh(self, bus: Any) -> None:
        """Poll the connected player and update the cached metadata snapshot."""
        path = self._find_player_path(bus)
        with self._lock:
            self._player_path = path
        if path is None:
            with self._lock:
                self.metadata = Metadata(
                    name="", title="", cover="", md5="", state=False
                )
            return

        props = dbus.Interface(
            bus.get_object(self.service, path), PROPERTIES_IFACE
        )
        status = str(props.Get(MEDIA_PLAYER_IFACE, "Status"))
        try:
            track = props.Get(MEDIA_PLAYER_IFACE, "Track")
        except dbus.DBusException:
            track = {}

        title = str(track.get("Title", "")) if track else ""
        artist = str(track.get("Artist", "")) if track else ""
        with self._lock:
            self.metadata = Metadata(
                name=artist,
                title=title,
                cover="",
                md5="",
                state=(status == "playing"),
            )

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
        with self._lock:
            bus = self._bus
            path = self._player_path
        if bus is None or path is None:
            return False
        try:
            player = dbus.Interface(
                bus.get_object(self.service, path), MEDIA_PLAYER_IFACE
            )
            if desired_state:
                player.Play()
            else:
                player.Pause()
            return True
        except dbus.DBusException as exc:
            logger.warning("Bluetooth play-state change failed: %s", exc)
            with self._lock:
                if self._bus is bus:
                    self._player_path = None
            return False

    def play_index(self, index: int) -> bool:
        """Bluetooth has no button-selectable presets."""
        return False

    def close(self) -> None:
        """Stop the background D-Bus worker (used by tests / shutdown)."""
        self._stop.set()
