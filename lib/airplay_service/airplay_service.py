import logging
import os
import threading
from pathlib import Path

import dbus
from airplay_service.airplay_metadata_processor import AirplayMetadataProcessor
from music_source import Metadata, MusicSource
from utilities import UtilityLibrary

logger = logging.getLogger(__name__)
utility = UtilityLibrary()
PIPE_PATH = "/tmp/shairport-sync-metadata"
INHIBIT_FILE = "/run/airplay-inhibited"

# shairport-sync D-Bus identifiers. The RemoteControl interface carries both the
# read-only PlayerState property and the playback command methods. On the stable
# branch these commands work only for classic AirPlay senders (they are backed by
# DACP); the development branch adds experimental AirPlay 2 support. When a
# command is unavailable we always fall back to the local inhibit marker so a
# source switch is still guaranteed. See doc/airplay.md.
SHAIRPORT_BUS_NAME = "org.gnome.ShairportSync"
SHAIRPORT_OBJECT_PATH = "/org/gnome/ShairportSync"
REMOTE_CONTROL_IFACE = "org.gnome.ShairportSync.RemoteControl"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


class AirplayService(MusicSource):
    """Asynchronously connect to Shairport-Sync and consume its metadata."""

    def __init__(
        self,
        start_binary: bool = True,
        pipe_path: str = PIPE_PATH,
        inhibit_file: str = INHIBIT_FILE,
    ) -> None:
        self.name = "airplay"
        self.pipe_path = pipe_path
        self.module_location = os.path.dirname(os.path.abspath(__file__))
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.properties_interface = None
        # RemoteControl command interface, resolved by the D-Bus worker alongside
        # the properties interface so set_play_state() can reuse the live object.
        self.remote_control_interface = None
        self.inhibit_file = Path(
            os.environ.get("RADIO_AIRPLAY_INHIBIT_FILE", inhibit_file)
        )
        self.metadata = Metadata(name="", title="", cover="", md5="", state=False)
        # Inhibition belongs to the lifetime of the radio controller, mirroring the
        # USB source: never carry a marker over a controller restart, so a still-
        # active AirPlay session can be re-detected by the fresh controller.
        self._set_inhibited(False)
        if start_binary:
            self.start_background_processes()
        self.dbus_thread = threading.Thread(
            target=self._dbus_worker, daemon=True, name="airplay-dbus"
        )
        self.metadata_thread = threading.Thread(
            target=self.metadata_reader, daemon=True, name="airplay-metadata"
        )
        self.dbus_thread.start()
        self.metadata_thread.start()

    def start_background_processes(self) -> None:
        nqptp = os.environ.get("RADIO_NQPTP_BINARY", "nqptp")
        airplay = os.environ.get("RADIO_AIRPLAY_BINARY", "shairport-sync")
        utility.start_external_program_in_background(nqptp)
        utility.start_external_program_in_background(
            f"{airplay} -c {self.module_location}/airplay.conf"
        )

    def _dbus_worker(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                bus = dbus.SystemBus()
                remote = bus.get_object(SHAIRPORT_BUS_NAME, SHAIRPORT_OBJECT_PATH)
                interface = dbus.Interface(remote, PROPERTIES_IFACE)
                remote_control = dbus.Interface(remote, REMOTE_CONTROL_IFACE)
                with self._lock:
                    self.properties_interface = interface
                    self.remote_control_interface = remote_control
                delay = 1.0
                while not self._wait(1):
                    try:
                        interface.Get(REMOTE_CONTROL_IFACE, "PlayerState")
                    except dbus.DBusException:
                        break
                with self._lock:
                    if self.properties_interface is interface:
                        self.properties_interface = None
                    if self.remote_control_interface is remote_control:
                        self.remote_control_interface = None
            except dbus.DBusException as exc:
                logger.warning("AirPlay D-Bus unavailable; retrying: %s", exc)
            self._wait(delay)
            delay = min(delay * 2, 30.0)

    def _wait(self, delay: float) -> bool:
        """Interruptible wait, split out so retry timing is testable."""
        return self._stop.wait(delay)

    def metadata_reader(self) -> None:
        processor = AirplayMetadataProcessor()
        delay = 0.5
        while not self._stop.is_set():
            try:
                with open(self.pipe_path, "r") as pipe:
                    delay = 0.5
                    for line in pipe:
                        if self._stop.is_set():
                            return
                        try:
                            new = processor.process_line(line, pipe)
                        except Exception as exc:
                            logger.warning("Ignoring malformed AirPlay metadata: %s", exc)
                            continue
                        if new:
                            with self._lock:
                                self.metadata = Metadata(
                                    name=new.get("artist", ""),
                                    title=new.get("track", ""),
                                    cover=new.get("filename", ""),
                                    md5=new.get("md5", ""),
                                    state=self.metadata.state,
                                )
            except OSError as exc:
                logger.warning("AirPlay metadata FIFO unavailable; retrying: %s", exc)
            self._wait(delay)
            delay = min(delay * 2, 10.0)

    def _set_inhibited(self, inhibited: bool) -> bool:
        """Create/remove the local inhibit marker; a no-op on error.

        While the marker exists ``get_play_state`` reports ``False`` so the
        controller stops treating AirPlay as the active source, even when the
        sender ignores the RemoteControl command (e.g. an AirPlay 2 client that
        does not expose remote control). The marker is cleared automatically once
        the sender's own session ends (``PlayerState`` leaves ``Playing``), so a
        later AirPlay stream can take over normally.
        """
        try:
            if inhibited:
                self.inhibit_file.parent.mkdir(parents=True, exist_ok=True)
                self.inhibit_file.touch()
            else:
                try:
                    self.inhibit_file.unlink()
                except FileNotFoundError:
                    pass
            return True
        except OSError as exc:
            logger.warning("Unable to change AirPlay inhibition: %s", exc)
            return False

    def _player_state(self) -> str:
        """Return shairport-sync's ``PlayerState`` string, or ``""`` if unknown."""
        with self._lock:
            interface = self.properties_interface
        if interface is None:
            return ""
        try:
            return str(interface.Get(REMOTE_CONTROL_IFACE, "PlayerState"))
        except dbus.DBusException:
            with self._lock:
                if self.properties_interface is interface:
                    self.properties_interface = None
            return ""

    def _remote_control_available(self) -> bool:
        """True when shairport reports a live remote-control channel.

        ``Available`` is driven by the sender advertising a DACP remote-control
        server. Classic AirPlay senders do; AirPlay 2 senders do so only with the
        development branch and are not guaranteed. A missing/false value means the
        command would be a no-op, so callers use the inhibit fallback instead.
        """
        with self._lock:
            interface = self.properties_interface
        if interface is None:
            return False
        try:
            return bool(interface.Get(REMOTE_CONTROL_IFACE, "Available"))
        except dbus.DBusException:
            return False

    def _send_remote_command(self, command: str) -> bool:
        """Invoke a RemoteControl method by name; return True if it was sent.

        Resolved defensively so a shairport-sync build that lacks the method (or
        a stale D-Bus object) fails softly to the inhibit fallback rather than
        raising into the metadata loop.
        """
        with self._lock:
            remote_control = self.remote_control_interface
        if remote_control is None:
            return False
        method = getattr(remote_control, command, None)
        if method is None:
            return False
        try:
            method()
            return True
        except dbus.DBusException as exc:
            logger.warning("AirPlay %s command failed: %s", command, exc)
            with self._lock:
                if self.remote_control_interface is remote_control:
                    self.remote_control_interface = None
            return False

    def get_play_state(self) -> bool:
        # A live inhibit marker means another source has taken over. Clear it
        # once the AirPlay session itself has ended so a new stream can win.
        if self.inhibit_file.exists():
            if self._player_state() != "Playing":
                self._set_inhibited(False)
            return False
        return self._player_state() == "Playing"

    def get_metadata(self) -> Metadata:
        state = self.get_play_state()
        with self._lock:
            result = self.metadata.snapshot()
        result.state = state
        return result

    def set_play_state(self, state: bool) -> bool:
        """Start or stop AirPlay playback.

        Stopping is best-effort remote control (Pause) plus a guaranteed local
        inhibit fallback, so the source switch happens even when the sender
        cannot be paused remotely. Starting clears the inhibit marker and issues
        Play when a remote-control channel is available. Returns True once the
        requested state has been applied locally (the marker is authoritative).
        """
        if state:
            self._set_inhibited(False)
            if self._remote_control_available():
                self._send_remote_command("Play")
            return True
        # Stop: try to pause the sender, but always inhibit locally so routing
        # stops regardless of whether the remote command was honoured.
        if self._remote_control_available():
            self._send_remote_command("Pause")
        return self._set_inhibited(True)

    def play_index(self, index: int) -> bool:
        return False

    def close(self) -> None:
        self._stop.set()
