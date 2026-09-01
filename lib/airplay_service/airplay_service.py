import logging
import os
import threading

import dbus
from airplay_service.airplay_metadata_processor import AirplayMetadataProcessor
from music_source import Metadata, MusicSource
from utilities import UtilityLibrary

logger = logging.getLogger(__name__)
utility = UtilityLibrary()
PIPE_PATH = '/tmp/shairport-sync-metadata'


class AirplayService(MusicSource):
    """Asynchronously connect to Shairport-Sync and consume its metadata."""

    def __init__(self, start_binary: bool = True,
                 pipe_path: str = PIPE_PATH) -> None:
        self.name = 'airplay'
        self.pipe_path = pipe_path
        self.module_location = os.path.dirname(os.path.abspath(__file__))
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.properties_interface = None
        self.metadata = Metadata(
            name='', title='', cover='', md5='', state=False
        )
        if start_binary:
            self.start_background_processes()
        self.dbus_thread = threading.Thread(
            target=self._dbus_worker, daemon=True, name='airplay-dbus'
        )
        self.metadata_thread = threading.Thread(
            target=self.metadata_reader, daemon=True, name='airplay-metadata'
        )
        self.dbus_thread.start()
        self.metadata_thread.start()

    def start_background_processes(self) -> None:
        nqptp = os.environ.get('RADIO_NQPTP_BINARY', 'nqptp')
        airplay = os.environ.get('RADIO_AIRPLAY_BINARY', 'shairport-sync')
        utility.start_external_program_in_background(nqptp)
        utility.start_external_program_in_background(
            f'{airplay} -c {self.module_location}/airplay.conf'
        )

    def _dbus_worker(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                bus = dbus.SystemBus()
                remote = bus.get_object(
                    'org.gnome.ShairportSync', '/org/gnome/ShairportSync'
                )
                interface = dbus.Interface(
                    remote, 'org.freedesktop.DBus.Properties'
                )
                with self._lock:
                    self.properties_interface = interface
                delay = 1.0
                while not self._wait(1):
                    try:
                        interface.Get(
                            'org.gnome.ShairportSync.RemoteControl',
                            'PlayerState'
                        )
                    except dbus.DBusException:
                        break
                with self._lock:
                    if self.properties_interface is interface:
                        self.properties_interface = None
            except dbus.DBusException as exc:
                logger.warning(
                    'AirPlay D-Bus unavailable; retrying: %s', exc
                )
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
                with open(self.pipe_path, 'r') as pipe:
                    delay = 0.5
                    for line in pipe:
                        if self._stop.is_set():
                            return
                        try:
                            new = processor.process_line(line, pipe)
                        except Exception as exc:
                            logger.warning(
                                'Ignoring malformed AirPlay metadata: %s', exc
                            )
                            continue
                        if new:
                            with self._lock:
                                self.metadata = Metadata(
                                    name=new.get('artist', ''),
                                    title=new.get('track', ''),
                                    cover=new.get('filename', ''),
                                    md5=new.get('md5', ''),
                                    state=self.metadata.state
                                )
            except OSError as exc:
                logger.warning(
                    'AirPlay metadata FIFO unavailable; retrying: %s', exc
                )
            self._wait(delay)
            delay = min(delay * 2, 10.0)

    def get_play_state(self) -> bool:
        with self._lock:
            interface = self.properties_interface
        if interface is None:
            return False
        try:
            state = interface.Get(
                'org.gnome.ShairportSync.RemoteControl', 'PlayerState'
            )
            return state == 'Playing'
        except dbus.DBusException:
            with self._lock:
                if self.properties_interface is interface:
                    self.properties_interface = None
            return False

    def get_metadata(self) -> Metadata:
        state = self.get_play_state()
        with self._lock:
            result = self.metadata.snapshot()
        result.state = state
        return result

    def set_play_state(self, state: bool) -> bool:
        return False

    def play_index(self, index: int) -> bool:
        return False

    def close(self) -> None:
        self._stop.set()
