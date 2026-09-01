import logging
import os
import subprocess
import threading
from time import sleep
from typing import List, Optional

from music_source import Metadata, MusicSource
from utilities import UtilityLibrary

logger = logging.getLogger(__name__)

utility = UtilityLibrary()

class MPDService(MusicSource):
    """Internet-radio playback backend driven by MPD via the ``mpc`` CLI.

    Manages a list of preset stations (loaded from ``stations.conf``), starts a
    daemon thread that keeps MPD in the desired play state, and exposes track
    metadata for the display.
    """

    def __init__(self) -> None:
        """Load preset stations and start the play-state watchdog thread."""
        self.name = "mpd"
        self.module_location = os.path.dirname(os.path.abspath(__file__))
        conf = utility.read_config(f'{self.module_location}/stations.conf')
        self.stations: List[dict] = [{'name': item, 'url': conf[item]['url'], 'logo': conf[item]['logo']} for item in conf]
        self.current_station = 0
        self.desired_play_state = False
        self.metadata = Metadata(name="", title="", cover="", md5="", state=False)
        # Start the thread to ensure desired play state in background
        self.player_thread = threading.Thread(target=self.ensure_desired_play_state, daemon=True)
        self.player_thread.start()

    def _run_mpc_command(self, command: str) -> Optional[str]:
        """Run an ``mpc`` subcommand and return its stripped stdout.

        Args:
            command (str): The ``mpc`` subcommand and arguments (e.g. ``play``).

        Returns:
            str or None: The command's stdout, or ``None`` when it could not be
            run.
        """
        try:
            result = subprocess.run(['mpc'] + command.split(), capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"mpc command failed: {result.stderr.strip()}")
            return result.stdout.strip()
        except Exception as e:
            logger.exception(f"Error running mpc command: {e}")
            return None

    def ensure_desired_play_state(self) -> None:
        """Continuously re-assert the desired play state in the background."""
        sleep(20)
        while True:
            self.check_state(self.desired_play_state)
            sleep(1)

    def get_play_state(self) -> bool:
        """
        Check if MPD is currently playing.

        Returns:
            bool: True if MPD is playing, False otherwise.
        """
        status = self._run_mpc_command('status')
        if status is None:
            logger.warning("Unable to get status from MPD.")
            return False
        return 'playing' in status


    def set_play_state(self, should_play: bool) -> bool:
        """
        Set the play state to play or stop based on a bool input.

        Args:
            should_play (bool): True to start playing, False to stop.

        Returns:
            bool: Always True once the requested state has been applied.
        """
        self.desired_play_state = should_play
        if should_play:
            logger.info("Setting MPD to play state.")
            self._run_mpc_command('play')
        else:
            logger.info("Setting MPD to stop state.")
            self._run_mpc_command('stop')
        return True

    def play_index(self, index: int) -> bool:
        """
        Play an internet radio station URL based on index in config file.

        Args:
            index (int): The 1-based index of the station to play.

        Returns:
            bool: True if the station started playing, False on error.
        """
        self.desired_play_state = True
        try:
            self.desired_play_state = True
            self.current_station = index - 1
            self._run_mpc_command('clear')
            self._run_mpc_command(f'add {self.stations[self.current_station]["url"]}')
            self._run_mpc_command('play')
            return True
        except Exception as e:
            logger.error(f"Error playing station {self.current_station}: {e}")
            return False

    def _is_status_line(self, line: str) -> bool:
        """Return True when a line is MPD/mpc status, not stream metadata."""
        lowered = line.lower()
        return (
            lowered.startswith('[')
            or lowered.startswith('volume:')
            or lowered.startswith('repeat:')
            or lowered.startswith('random:')
            or lowered.startswith('single:')
            or lowered.startswith('consume:')
            or lowered.startswith('error:')
        )

    def get_metadata(self) -> Metadata:
        """
        Get the metadata of the current stream.

        Returns:
            Metadata: The current stream metadata.
        """
        self.metadata.title = ""
        self.metadata.cover = f"{self.module_location}/logos/{self.stations[self.current_station]['logo']}"
        self.metadata.name = self.stations[self.current_station]['name']
        self.metadata.state = self.get_play_state()

        output = self._run_mpc_command('current -f %title%')
        if output:
            # ``mpc current`` prints only current song/stream metadata, unlike
            # ``mpc status`` which can append status/volume lines while stream
            # metadata is still unavailable. Still filter defensively so bogus
            # lines such as "volume: 22% ..." are never shown on the display.
            title = ""
            for line in output.split('\n'):
                stripped = line.strip()
                if not stripped or self._is_status_line(stripped):
                    continue
                title = stripped
                break
            self.metadata.title = f'{title} ' if title else ''
        else:
            # This is expected shortly after changing an internet-radio stream.
            # Keep the title row blank until real stream metadata arrives.
            self.metadata.title = ""
        return self.metadata

    def check_state(self, desired_state: bool) -> None:
        """
        Check if the MPD play state matches the desired state.
        If not, change it.

        Args:
            desired_state (bool): The desired play state.
        """
        try:
            status = self.get_play_state()
            if status != desired_state:
                self.set_play_state(desired_state)
        except Exception as e:
            logger.error(f"Unable to check MPD state: {e}")
            self.set_play_state(False)
            utility.restart_systemd_service('mpd.service')
            sleep(5)
            self.set_play_state(desired_state)
