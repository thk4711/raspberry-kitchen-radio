import logging
import os
import subprocess
import threading
from time import sleep
from typing import List, Optional

from music_source import Metadata, MusicSource
from utilities import MANAGED_CONFIG_DIR, UtilityLibrary

logger = logging.getLogger(__name__)

utility = UtilityLibrary()


def _logo_path(module_location: str, filename: str) -> str:
    """Resolve a managed uploaded logo before the immutable shipped asset."""
    name = os.path.basename(filename)
    if not name or name != filename:
        return ""
    managed = os.path.join(MANAGED_CONFIG_DIR, "logos", name)
    if os.path.isfile(managed):
        return managed
    return os.path.join(module_location, "logos", name)


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
        conf = utility.read_config_preferred(
            os.path.join(MANAGED_CONFIG_DIR, "stations.ini"),
            f"{self.module_location}/stations.conf",
        )
        self.stations: List[dict] = [
            {"name": item, "url": conf[item]["url"], "logo": conf[item]["logo"]} for item in conf
        ]
        self._station_lock = threading.RLock()
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
            result = subprocess.run(["mpc"] + command.split(), capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"mpc command failed: {result.stderr.strip()}")
                return None
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
        status = self._run_mpc_command("status")
        if status is None:
            logger.warning("Unable to get status from MPD.")
            return False
        return "playing" in status

    def set_play_state(self, should_play: bool) -> bool:
        """
        Set the play state to play or stop based on a bool input.

        Args:
            should_play (bool): True to start playing, False to stop.

        Returns:
            bool: True when ``mpc`` accepted the command, False otherwise.
        """
        with self._station_lock:
            self.desired_play_state = should_play
            if should_play:
                logger.info("Setting MPD to play state.")
                result = self._run_mpc_command("play")
            else:
                logger.info("Setting MPD to stop state.")
                result = self._run_mpc_command("stop")
            return result is not None

    def play_index(self, index: int) -> bool:
        """
        Play an internet radio station URL based on index in config file.

        Args:
            index (int): The 1-based index of the station to play.

        Returns:
            bool: True if the station started playing, False on error.
        """
        with self._station_lock:
            try:
                station_index = index - 1
                if station_index < 0:
                    raise IndexError
                station = self.stations[station_index]
            except (IndexError, TypeError):
                logger.error("Invalid station index: %s", index)
                return False

            for command in ("clear", f'add {station["url"]}', "play"):
                if self._run_mpc_command(command) is None:
                    return False
            self.current_station = station_index
            self.desired_play_state = True
            return True

    def next_track(self) -> bool:
        """Play the next configured station, wrapping to the first."""
        with self._station_lock:
            if not self.stations:
                return False
            return self.play_index((self.current_station + 1) % len(self.stations) + 1)

    def previous_track(self) -> bool:
        """Play the previous configured station, wrapping to the last."""
        with self._station_lock:
            if not self.stations:
                return False
            return self.play_index((self.current_station - 1) % len(self.stations) + 1)

    def _is_status_line(self, line: str) -> bool:
        """Return True when a line is MPD/mpc status, not stream metadata."""
        lowered = line.lower()
        return (
            lowered.startswith("[")
            or lowered.startswith("volume:")
            or lowered.startswith("repeat:")
            or lowered.startswith("random:")
            or lowered.startswith("single:")
            or lowered.startswith("consume:")
            or lowered.startswith("error:")
        )

    def get_metadata(self) -> Metadata:
        """
        Get the metadata of the current stream.

        Returns:
            Metadata: The current stream metadata.
        """
        with self._station_lock:
            self.metadata.title = ""
            self.metadata.cover = _logo_path(
                self.module_location, self.stations[self.current_station]["logo"]
            )
            self.metadata.name = self.stations[self.current_station]["name"]
            self.metadata.state = self.get_play_state()

            output = self._run_mpc_command("current -f %title%")
            if output:
                # ``mpc current`` prints only current song/stream metadata, unlike
                # ``mpc status`` which can append status/volume lines while stream
                # metadata is still unavailable. Still filter defensively so bogus
                # lines such as "volume: 22% ..." are never shown on the display.
                title = ""
                for line in output.split("\n"):
                    stripped = line.strip()
                    if not stripped or self._is_status_line(stripped):
                        continue
                    title = stripped
                    break
                self.metadata.title = f"{title} " if title else ""
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
            utility.restart_systemd_service("mpd.service")
            sleep(5)
            self.set_play_state(desired_state)
