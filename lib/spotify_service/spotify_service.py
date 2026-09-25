import logging
import os
import socket
import tempfile
import threading
from time import sleep
from typing import Any, Dict

import yaml
from music_source import Metadata, MusicSource
from utilities import UtilityLibrary

logger = logging.getLogger(__name__)

utility = UtilityLibrary()


class SpotifyService(MusicSource):
    """
    A controller class to manage Spotify playback.

    Attributes:
        name (str): The name of the service, 'spotify'.
        spotify_host (str): The hostname where the Spotify client is running.
        spotify_port (int): The port number on which the Spotify client is listening.
        start_binary (bool): Flag to indicate whether to start the Spotify binary.
        spotify_url (str): The URL for accessing the Spotify client.
        metadata (dict): A dictionary to store the current track metadata.
        bin_dir_name (str): The directory path where the Spotify binary is located.
        conf_dir_name (str): The directory path where the configuration files are stored.
        metadata_thread (threading.Thread): A thread that continuously reads metadata from Spotify.
    """

    def __init__(
        self, host: str = "localhost", port: int = 3678, start_binary: bool = True
    ) -> None:
        """Generate the go-librespot config, start it, and read its metadata.

        Args:
            host (str): Hostname where the go-librespot API is reachable.
            port (int): Port of the go-librespot API/server.
            start_binary (bool): Launch the go-librespot binary when True. The
                radio passes ``False`` when the Spotify source is disabled via
                the managed feature flags.
        """
        self.name = "spotify"
        self.spotify_host = host
        self.spotify_port = port
        self.start_binary = start_binary
        self.spotify_url = f"http://{self.spotify_host}:{self.spotify_port}"
        self.metadata = Metadata(name="", title="", cover="", md5="", state=False)
        self._metadata_lock = threading.RLock()
        self.module_location = os.path.dirname(os.path.abspath(__file__))

        # The go-librespot config is generated at runtime. On the appliance
        # image the application directory may live on a read-only rootfs, so
        # prefer a writable location. Order: RADIO_SPOTIFY_CONF env override, a
        # writable module dir, then /tmp. The binary name/path and config
        # argument are also configurable so the appliance can point at the
        # Buildroot-built "go-librespot" on PATH. go-librespot uses
        # --config_dir with a directory containing config.yaml/config.yml.
        self.config_path = self._resolve_config_path()
        self.binary = os.environ.get("RADIO_SPOTIFY_BINARY", "go-librespot")
        self.config_arg = os.environ.get("RADIO_SPOTIFY_CONFIG_ARG", "--config_path")

        # Create configuration file
        self.create_config_yml()

        # Start Spotify binary if required
        if self.start_binary:
            utility.start_external_program_in_background(
                f"{self.binary} {self.config_arg} {self._config_arg_value()}"
            )
            sleep(2)  # Give the binary some time to start

        # Start metadata reader in a background thread
        self.metadata_thread = threading.Thread(target=self.metadata_reader, daemon=True)
        self.metadata_thread.start()

    def _resolve_config_path(self) -> str:
        """Pick a writable path for the generated go-librespot config.

        Tries, in order: the ``RADIO_SPOTIFY_CONF`` env var, the module
        directory (dev / read-write installs), then ``/tmp`` (read-only
        rootfs appliance image).
        """
        override = os.environ.get("RADIO_SPOTIFY_CONF")
        if override:
            return override
        module_conf = f"{self.module_location}/spotify.conf"
        if os.access(self.module_location, os.W_OK):
            return module_conf
        return "/tmp/spotify.conf"

    def create_config_yml(self) -> None:
        """
        Creates the configuration YAML file required by the Spotify client.
        """
        cfg_data: Dict[str, Any] = {
            "device_name": socket.gethostname(),
            "credentials": {"type": "zeroconf"},
            "server": {"enabled": True, "port": self.spotify_port},
            "audio_backend": "alsa",
            "audio_device": "default",
            # Appliance policy: keep go-librespot quiet. "error" is the quietest
            # level upstream supports (trace/debug/info/warn/error) and dropping
            # timestamps trims each line, so no routine log growth is produced.
            "log_level": "error",
            "log_disable_timestamp": True,
        }
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
        with open(self.config_path, "w") as file:
            yaml.dump(cfg_data, file)

    def _config_arg_value(self) -> str:
        """Return the value to pass after the configured config argument."""
        if self.config_arg == "--config_dir":
            return os.path.dirname(self.config_path) or "."
        return self.config_path

    def metadata_reader(self) -> None:
        """
        Continuously reads and updates metadata from the Spotify client.
        """
        while True:
            self.read_metadata_once()
            sleep(1)

    def read_metadata_once(self) -> None:
        """Fetch and apply one status response from go-librespot.

        The production reader calls this once per second. The bounded operation
        is intentionally public so host tests can exercise malformed responses,
        recovery, and cover replacement without running an infinite thread.
        """
        try:
            json_data = utility.request_json(f"{self.spotify_url}/status")
            track = json_data.get("track") if json_data else None
            if isinstance(track, dict):
                artist_names = track.get("artist_names")
                track_name = track.get("name")
            else:
                artist_names = None
                track_name = None
            if isinstance(artist_names, list) and isinstance(track_name, str):
                with self._metadata_lock:
                    self.metadata.name = " ".join(str(item) for item in artist_names)
                    self.metadata.title = track_name
                    self.metadata.state = not (json_data.get("paused") or json_data.get("stopped"))

                # Update album cover only if it has changed.
                album_cover_url = track.get("album_cover_url") if isinstance(track, dict) else None
                if isinstance(album_cover_url, str) and self.metadata.md5 != album_cover_url:
                    image_data = utility.request_image(album_cover_url)
                    cover_path = "/tmp/spotify_cover.jpg"
                    if image_data:
                        fd, temporary = tempfile.mkstemp(dir="/tmp", prefix="spotify-cover-")
                        try:
                            with os.fdopen(fd, "wb") as file:
                                file.write(image_data)
                            os.chmod(temporary, 0o644)
                            os.replace(temporary, cover_path)
                        finally:
                            if os.path.exists(temporary):
                                os.unlink(temporary)
                        with self._metadata_lock:
                            self.metadata.cover = cover_path
                            self.metadata.md5 = album_cover_url
            else:
                with self._metadata_lock:
                    self.metadata.state = False
        except Exception as e:
            logger.error(f"Unable to get Spotify metadata: {e}")

    def get_play_state(self) -> bool:
        """
        Retrieves the current playback state.

        Returns:
            bool: True if playing, False otherwise.
        """
        with self._metadata_lock:
            return self.metadata.state

    def get_metadata(self) -> Metadata:
        """
        Retrieves the current track metadata.

        Returns:
            Metadata: The metadata containing name, title, cover, and state.
        """
        with self._metadata_lock:
            return self.metadata.snapshot()

    def set_play_state(self, state: bool) -> bool:
        """
        Sets the playback state of the Spotify client.

        Args:
            state (bool): True to play, False to pause.

        Returns:
            bool: True if the request was sent successfully, False otherwise.
        """
        return self._send_player_command("resume" if state else "pause")

    def _send_player_command(self, command: str) -> bool:
        """POST one command to the pinned go-librespot player API."""
        try:
            result = utility.make_request(f"{self.spotify_url}/player/{command}", method="POST")
            return result is not None
        except Exception as e:
            logger.error(f"Unable to send Spotify {command} command: {e}")
            return False

    def play_index(self, index: int) -> bool:
        """Provided only for MusicSource compatibility; unused for Spotify.

        Args:
            index (int): Preset index (ignored).

        Returns:
            bool: Always False (Spotify has no button-selectable presets).
        """
        return False

    def next_track(self) -> bool:
        """Request the next Spotify track."""
        return self._send_player_command("next")

    def previous_track(self) -> bool:
        """Request the previous Spotify track."""
        return self._send_player_command("prev")
