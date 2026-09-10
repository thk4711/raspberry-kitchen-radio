#!/usr/bin/python3
"""Entry point and top-level orchestration for Raspberry Kitchen Radio.

This module wires the individual building blocks together into the
:class:`RadioController`: the playback backends (MPD internet radio, AirPlay,
Spotify Connect, Bluetooth, and USB Audio), the SPI display, and the
ADS1115-based analog inputs (volume
knob, preset buttons and the power switch). It also owns the amplifier GPIO and
the background metadata/play-state loop.
"""

import logging
import os
import sys
import threading
from time import sleep
from typing import Any, Dict, List, Optional

# Handle --version as early as possible, before importing hardware-only modules
# (RPi.GPIO etc.) so it works on any host. Resolve the version from the bundled
# lib/ package, which is the single source of truth (lib/_version.py).
if __name__ == "__main__" and any(a in ("--version", "-V") for a in sys.argv[1:]):
    _lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    from _version import __version__ as _v

    print(f"raspberry-kitchen-radio {_v}")
    raise SystemExit(0)

import RPi.GPIO as GPIO


def _resolve_log_level(default: int = logging.ERROR) -> int:
    """Resolve the logging level from the ``RADIO_LOG_LEVEL`` env var.

    Accepts standard level names (DEBUG, INFO, WARNING, ERROR, CRITICAL) or a
    numeric level. Falls back to ``default`` when unset or invalid.
    """
    raw = os.environ.get("RADIO_LOG_LEVEL")
    if not raw:
        return default
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return getattr(logging, raw.upper(), default)


# Configure logging. Level is driven by the RADIO_LOG_LEVEL env var (default
# ERROR) and may additionally be overridden by a ``[logging] level`` entry in
# radio.conf when the env var is not set (applied once the config is loaded).
logging.basicConfig(level=_resolve_log_level())
logger = logging.getLogger(__name__)

# Startup-progress milestones are logged at INFO on a dedicated logger that is
# not gated by the (default ERROR) app level, so boot progress stays visible on
# the console exactly like the previous print() calls, without lowering the
# level for the rest of the application.
_startup_logger = logging.getLogger("radio.startup")
_startup_logger.setLevel(logging.INFO)

# GPIO setup
GPIO.setmode(GPIO.BCM)

# Add the bundled 'lib' directory to sys.path. Resolve it relative to this
# file instead of the current working directory so both of these work:
#   cd /opt/raspberry-kitchen-radio && python3 radio.py
#   python3 /opt/raspberry-kitchen-radio/radio.py
file_location = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(file_location, "lib"))

# Import music controller
from adc_controller import ADCController
from adc_snapshot import resolve_adc_status_file, write_adc_snapshot
from airplay_service.airplay_service import AirplayService
from bluetooth_service.bluetooth_service import BluetoothService

# Import other custom modules
from display_1_inch_69.display_control import DisplayController
from mpd_service.mpd_service import MPDService
from music_source import MusicSource
from spotify_service.spotify_service import SpotifyService
from status_snapshot import (
    artwork_descriptor,
    build_snapshot,
    resolve_status_file,
    write_snapshot,
)
from usb_audio_service.usb_audio_service import USBAudioService
from utilities import MANAGED_CONFIG_DIR, UtilityLibrary

from radio_web import audio_hardware_store

utility = UtilityLibrary()


class RadioController:
    """
    Main controller class for managing radio services, display, and GPIO interactions.

    Attributes:
        config (dict): Configuration settings loaded from the config file.
        AMP_PIN (Optional[int]): BCM GPIO pin for the amplifier control, or
            ``None`` for a sound card with no power amp to switch.
        METADATA_UPDATE_INTERVAL (int): Interval in seconds for metadata updates.
        MIXER_NAME (str): Audio mixer name used by the ADC controller.
        services (list): List of available services and their states.
        active_service (object): The currently active service object.
        power_switch (bool): Current state of the power switch.
    """

    def __init__(self) -> None:
        """
        Initialize the RadioController with configuration and set up components.
        """
        _startup_logger.info("Starting up")
        file_location = os.path.dirname(os.path.abspath(__file__))
        self.config: Dict[str, Any] = utility.read_config_layered(
            f"{file_location}/radio.conf",
            os.path.join(MANAGED_CONFIG_DIR, "device.ini"),
        )
        # Layer the web-UI managed audio override on top
        # of radio.conf as well. It carries the ALSA mixer name and a maximum
        # output-volume cap; a missing file/key means "use the shipped default"
        # so a fresh image is unchanged. Applied on restart, like every managed
        # override.
        audio_override = os.path.join(MANAGED_CONFIG_DIR, "audio.ini")
        if os.path.isfile(audio_override):
            for section, values in utility.read_config(audio_override).items():
                self.config.setdefault(section, {}).update(values)
        adc_override = os.path.join(MANAGED_CONFIG_DIR, "adc.ini")
        if os.path.isfile(adc_override):
            for section, values in utility.read_config(adc_override).items():
                self.config.setdefault(section, {}).update(values)

        self._state_lock = threading.RLock()
        # Music-source feature flags. Managed by the web
        # UI at /etc/radio/sources.ini; a missing file/key means "enabled" so a
        # fresh image behaves exactly as before. Disabled sources are neither
        # constructed/launched nor added to self.services, so they disappear
        # from both the "Enabled" and "Running" views.
        self.source_flags: Dict[str, bool] = self._load_source_flags(file_location)
        # Arm liveness before any optional backend is initialized.
        self._heartbeat_file = self._resolve_heartbeat_file()
        self._touch_heartbeat()
        # Resolve the read-only status-snapshot path the web UI reads.
        # Best-effort only: any write failure
        # degrades to a silent no-op and must never disturb the metadata loop.
        self._status_file = self._resolve_status_file()
        self._adc_status_file = resolve_adc_status_file(
            self.config, os.environ.get("RADIO_ADC_STATUS_FILE")
        )

        # Apply an optional log level from radio.conf, but only when the
        # RADIO_LOG_LEVEL env var did not already set one (env var wins).
        if not os.environ.get("RADIO_LOG_LEVEL"):
            conf_level = self.config.get("logging", {}).get("level")
            if conf_level:
                logging.getLogger().setLevel(
                    getattr(logging, str(conf_level).upper(), logging.ERROR)
                )

        # Constants from config file
        profile = audio_hardware_store.PROFILES[audio_hardware_store.load_profile()]
        self.AMP_PIN: Optional[int] = profile.amp_gpio
        self.METADATA_UPDATE_INTERVAL: int = self.config["metadata"]["update_interval"]
        self.MIXER_NAME: str = profile.knob_mixer

        # Setup amplifier GPIO — only when the selected sound card has a power
        # amp to switch. A card with no amp (e.g. the built-in headphone jack)
        # resolves AMP_PIN to None and drives no GPIO.
        if self.AMP_PIN is not None:
            GPIO.setup(self.AMP_PIN, GPIO.OUT)
            GPIO.output(self.AMP_PIN, False)

        # Initialize components. Each source object is always constructed so
        # the attribute exists; disabled sources simply do not launch their
        # backend, are left off self.services (so they never appear as Running),
        # and their init scripts no-op.
        _startup_logger.info("Loading MPD")
        self.mpd = MPDService()
        _startup_logger.info("Loading AIRPLAY")
        self.airplay = AirplayService(
            start_binary=self.source_flags.get("airplay", True),
            pipe_path=self.config["airplay"]["pipe_path"],
        )
        _startup_logger.info("Loading SPOTIFY")
        self.spotify = SpotifyService(
            host=self.config["spotify"]["host"],
            port=self.config["spotify"]["port"],
            start_binary=self.source_flags.get("spotify", True),
        )
        _startup_logger.info("Loading USB AUDIO")
        usb_conf = self.config.get("usb_audio", {})
        self.usb_audio = USBAudioService(
            card=usb_conf.get("card", "UAC1Gadget"),
            inhibit_file=usb_conf.get("inhibit_file", "/run/usb-audio-inhibited"),
        )
        _startup_logger.info("Loading BLUETOOTH")
        self.bluetooth = BluetoothService(
            service=self.config.get("bluetooth", {}).get("dbus_service", "org.bluez")
        )
        self.display = DisplayController()
        adc_conf = self.config["adc"]
        # Optional maximum-volume cap from the managed audio override.
        # Absent -> 100 (uncapped), matching today.
        try:
            max_volume = int(str(self.config.get("volume", {}).get("max", 100)))
        except (TypeError, ValueError):
            max_volume = 100
        max_volume = max(0, min(100, max_volume))
        self.adc_controller = ADCController(
            mixer_name=self.MIXER_NAME,
            mixer_max_percent=profile.mixer_max_percent,
            switch_callback=self.handle_switch_state_change,
            button_callback=self.handle_button_press,
            volume_callback=self.handle_volume_change,
            i2c_address=int(str(adc_conf["i2c_address"]), 0),
            i2c_bus=int(str(adc_conf.get("i2c_bus", 1)), 0),
            volume_min_input=float(adc_conf["volume_min_input"]),
            volume_max_input=float(adc_conf["volume_max_input"]),
            button_min=float(adc_conf["button_min"]),
            button_max=float(adc_conf["button_max"]),
            button_tolerance=float(adc_conf["button_tolerance"]),
            switch_threshold=float(adc_conf.get("switch_threshold", 300)),
            max_volume=max_volume,
            snapshot_callback=self._write_adc_snapshot,
            start_thread=False,
        )

        # Define service states. Only enabled sources join the arbitration list
        # and the published status snapshot; a disabled source is therefore
        # neither Enabled nor Running. ``internet_radio``
        # is the user-facing name for MPD.
        candidate_services: List[Dict[str, Any]] = [
            {"name": "airplay", "service": self.airplay, "flag": "airplay"},
            {"name": "usb", "service": self.usb_audio, "flag": "usb_audio"},
            {"name": "mpd", "service": self.mpd, "flag": "internet_radio"},
            {"name": "spotify", "service": self.spotify, "flag": "spotify"},
            {"name": "bluetooth", "service": self.bluetooth, "flag": "bluetooth"},
        ]
        self.services: List[Dict[str, Any]] = [
            {"name": item["name"], "service": item["service"], "state": False}
            for item in candidate_services
            if self.source_flags.get(item["flag"], True)
        ]

        # Default active source: MPD when enabled, else the first enabled
        # source, else MPD (so the attribute is always a valid MusicSource even
        # if every source was disabled — an unusual, self-inflicted state).
        if self.source_flags.get("internet_radio", True):
            self.active_service: MusicSource = self.mpd
        elif self.services:
            self.active_service = self.services[0]["service"]
        else:
            self.active_service = self.mpd
        self.power_switch: bool = self.adc_controller.read_adc_switch()

        # Liveness heartbeat. The BusyBox freeze-watcher (S90radio) kills and
        # restarts this process if the heartbeat file goes stale, which catches
        # a hung-but-alive app (e.g. a wedged D-Bus/ALSA/SPI/HTTP call inside the
        # metadata loop) that the plain crash-restart supervisor never sees.
        #
        # Resolution order: RADIO_HEARTBEAT_FILE env var (set by S90radio) wins,
        # then radio.conf [watchdog] heartbeat_file, else a sensible tmpfs
        # default. Disable entirely with [watchdog] enabled = false or by
        # setting RADIO_HEARTBEAT_FILE to an empty string. Any write failure
        # degrades the feature to a silent no-op — it must never break the app
        # or the (Pi-less) test runs.
        _startup_logger.info("Init done")

    def _resolve_heartbeat_file(self) -> str:
        """Resolve the liveness heartbeat path, or ``''`` when disabled.

        The environment variable ``RADIO_HEARTBEAT_FILE`` takes precedence over
        the ``[watchdog]`` section of ``radio.conf`` so the init script can
        keep the app and the freeze-watcher pointed at the same file. Returns
        an empty string when the feature is disabled or when no writable path
        can be determined, in which case :meth:`_touch_heartbeat` is a no-op.
        """
        wd_conf = self.config.get("watchdog", {})
        enabled = wd_conf.get("enabled", True)
        if enabled is False:
            return ""

        env_path = os.environ.get("RADIO_HEARTBEAT_FILE")
        if env_path is not None:
            path = env_path.strip()
        else:
            path = str(wd_conf.get("heartbeat_file", "/tmp/radio.alive")).strip()
        if not path:
            return ""

        # Verify we can actually create/write the file now; if not, disable so
        # the loop never pays for a doomed write every iteration.
        try:
            with open(path, "a"):
                pass
        except OSError as e:
            logger.warning(f"Heartbeat disabled; cannot write {path}: {e}")
            return ""
        logger.info(f"Liveness heartbeat file: {path}")
        return path

    def _touch_heartbeat(self) -> None:
        """Update the heartbeat file's mtime; a silent no-op when disabled."""
        if not self._heartbeat_file:
            return
        try:
            os.utime(self._heartbeat_file, None)
        except FileNotFoundError:
            # File was removed (e.g. by the freeze-watcher after a stale
            # detection); recreate it so liveness is re-established.
            try:
                with open(self._heartbeat_file, "w"):
                    pass
            except OSError:
                pass
        except OSError:
            pass

    def _load_source_flags(self, file_location: str) -> Dict[str, bool]:
        """Load the managed music-source feature flags.

        Reads ``<MANAGED_CONFIG_DIR>/sources.ini`` layered over a built-in
        all-enabled default, using the same ``read_config_layered`` parser as
        the rest of the app. The flags live under a single ``[sources]`` section
        (``key = true/false``). A missing file, missing section or missing key
        all mean "enabled", so a fresh image behaves exactly as before and the
        player never fails to build a source because of this optional file.
        """
        defaults = {
            "internet_radio": True,
            "airplay": True,
            "spotify": True,
            "bluetooth": True,
            "usb_audio": True,
        }
        try:
            conf = utility.read_config_layered(
                f"{file_location}/sources.conf",
                os.path.join(MANAGED_CONFIG_DIR, "sources.ini"),
            )
        except SystemExit:
            # No shipped sources.conf default: fall back to all-enabled rather
            # than exiting, since the feature is optional (the web UI writes
            # only the override sources.ini).
            conf = {}
        section = conf.get("sources", {}) if isinstance(conf, dict) else {}
        flags = dict(defaults)
        for key in defaults:
            value = section.get(key)
            if isinstance(value, bool):
                flags[key] = value
        return flags

    def _resolve_amp_pin(self) -> Optional[int]:
        """Resolve the amplifier-enable BCM pin, or ``None`` when there is none.

        The value comes from ``[gpio] amp`` (``radio.conf``, layered over by the
        managed ``audio.ini`` the Sound card page writes). A positive integer is
        the pin ``radio.py`` drives; the sentinel ``none`` / ``false`` (or a
        missing key) means the selected sound card has no power amp, so no GPIO
        is set up or toggled. Anything unrecognised is treated as "no amp" and
        logged, so a malformed override can never crash startup.
        """
        raw = self.config.get("gpio", {}).get("amp")
        if isinstance(raw, bool):
            # bool is a subclass of int; ``amp = false`` means "no amp", and
            # ``amp = true`` is meaningless as a pin — both disable amp control.
            return None
        if isinstance(raw, int):
            return raw if raw >= 0 else None
        if raw is None or (isinstance(raw, str) and raw.strip().lower() in ("none", "false", "")):
            return None
        logger.warning("gpio.amp value %r is not a valid pin; disabling amp control", raw)
        return None

    def _resolve_status_file(self) -> str:
        """Resolve the read-only status-snapshot path, or ``''`` when disabled.

        The web administration interface (``radio_web``) runs as a *separate*
        process and cannot see the live :class:`MusicSource` objects, so the
        player publishes a small JSON snapshot for it to read. Delegated to
        :func:`status_snapshot.resolve_status_file`; the ``RADIO_STATUS_FILE``
        env var wins, then ``[status] file`` in ``radio.conf``, else a tmpfs
        default. Returns ``''`` when disabled or the path is not writable.
        """
        return resolve_status_file(self.config, os.environ.get("RADIO_STATUS_FILE"))

    def _collect_status_snapshot(self) -> Dict[str, Any]:
        """Build the display-safe status dict published to the web UI.

        Only display-safe primitives are included (source/track names, play
        state, per-source liveness). Never include stream URLs, credentials or
        any secret. The per-source states reuse the ``self.services`` list that
        :meth:`check_play_states` already maintains, so no extra polling is done.
        """
        with self._state_lock:
            active = self.active_service
        active_name = getattr(active, "name", "")
        now_playing: Dict[str, Any] = {}
        try:
            metadata = active.get_metadata()
            now_playing = {
                "name": metadata.name,
                "title": metadata.title,
                "state": bool(metadata.state),
                "artwork": artwork_descriptor(
                    active_name,
                    os.path.basename(metadata.cover) if active_name == "mpd" else metadata.cover,
                    metadata.md5,
                ),
            }
        except Exception as exc:
            logger.debug("Unable to read active metadata for status: %s", exc)
        return build_snapshot(self.power_switch, active_name, now_playing, self.services)

    def _write_status_snapshot(self) -> None:
        """Atomically write the status snapshot; a silent no-op when disabled.

        Delegates to :func:`status_snapshot.write_snapshot`, which uses the same
        ``tempfile`` + ``os.replace`` recipe as the managed config writes
        so a reader never observes a partial file. Any
        error degrades to a no-op and must never break the loop.
        """
        write_snapshot(self._status_file, self._collect_status_snapshot())

    def _write_adc_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Publish one best-effort ADC diagnostics sample for radio-web."""
        write_adc_snapshot(self._adc_status_file, snapshot)

    def handle_switch_state_change(self, switch_number: int, new_state: bool) -> None:
        """
        Handle changes in the state of a switch.

        Args:
            switch_number (int): Number of the switch
            new_state (bool): New state of the power switch.
        """
        logger.debug(f"Switch number {switch_number} state changed to: {new_state}")
        if switch_number == 1:
            self.power_switch = new_state
            self.display.toggle_backlight(new_state)
            self.active_service.set_play_state(new_state)
            self.update_metadata()
            if self.AMP_PIN is not None:
                GPIO.output(self.AMP_PIN, new_state)
        else:
            logger.warning(f"unknown switch {switch_number} change detected")

    def handle_button_press(self, button_number: int) -> None:
        """
        Handle button press events, switching the active service to MPD and playing the selected index.

        Args:
            button_number (int): Button index that was pressed.
        """
        logger.debug(f"Button {button_number} pressed")
        if 1 <= button_number <= 6:
            self.active_service = self.mpd
            self.mpd.play_index(button_number)
            # Surface a brief preset toast naming the station on the display.
            try:
                station = self.mpd.stations[button_number - 1]["name"]
                self.display.show_toast(station)
            except Exception as e:
                logger.error(f"Unable to show preset toast: {e}")
            self.update_metadata()
        else:
            logger.warning(f"unknown button {button_number} press detected")

    def handle_volume_change(self, volume: int) -> None:
        """Forward a volume-knob change to the display's volume OSD.

        The ADC controller already applies the level to the ALSA mixer; this
        only surfaces it on screen as a briefly-shown, auto-hiding overlay.
        Guarded so a display hiccup never disturbs the ADC loop.

        Args:
            volume (int): The new volume level, 0..100.
        """
        try:
            self.display.show_volume(volume)
        except Exception as e:
            logger.error(f"Unable to show volume OSD: {e}")

    def update_metadata(self) -> None:
        """
        Update the display with metadata from the currently active service.

        Also tells the display which art treatment to use: internet-radio (MPD)
        stations render as a dominant-colour backdrop with the centred station
        logo, while Spotify/AirPlay render their real album art full-bleed.
        The play/pause state is forwarded for the status chrome.
        """
        try:
            metadata = self.active_service.get_metadata()
            art_mode = "radio" if self.active_service is self.mpd else "cover"
            source = getattr(self.active_service, "name", "")
            self.display.update_metadata(
                metadata.name,
                metadata.title,
                metadata.cover,
                metadata.md5,
                state=metadata.state,
                art_mode=art_mode,
                source=source,
            )
        except Exception as e:
            logger.error(f"Unable to get metadata: {e}")

    def check_play_states(self) -> None:
        """
        Check the play states of all services, update the active service, and ensure only one service is playing at a time.
        """
        changed_service_name = None

        for service in self.services:
            try:
                new_state = service["service"].get_play_state()
                if not isinstance(new_state, bool):
                    raise TypeError(
                        f"{service['name']}.get_play_state returned "
                        f"{type(new_state).__name__}, expected bool"
                    )
            except Exception as exc:
                logger.error("Unable to check %s state: %s", service["name"], exc)
                new_state = False
            if new_state and not service["state"]:
                # List order is the documented priority for simultaneous starts.
                if changed_service_name is None:
                    changed_service_name = service["name"]
                    with self._state_lock:
                        self.active_service = service["service"]
                self.update_metadata()
            service["state"] = new_state

        if changed_service_name:
            for service in self.services:
                if service["name"] != changed_service_name:
                    try:
                        if service["service"].get_play_state():
                            service["service"].set_play_state(False)
                    except Exception as exc:
                        logger.error("Unable to stop %s: %s", service["name"], exc)

    def metadata_loop(self) -> None:
        """
        Continuous loop to update metadata and check play states at regular intervals.

        Refreshes the liveness heartbeat on every iteration so the external
        freeze-watcher can tell this (hardware/service-touching) thread is still
        cycling. If a call below wedges for longer than the watcher's timeout,
        the heartbeat goes stale and the app is killed and restarted.
        """
        while True:
            try:
                self._touch_heartbeat()
                if self.power_switch:
                    self.update_metadata()
                self.check_play_states()
                self._write_status_snapshot()
            except Exception:
                logger.exception("Unhandled metadata-loop error")
            sleep(self.METADATA_UPDATE_INTERVAL)

    def start(self) -> None:
        """
        Start the main loop of the radio controller, managing the power switch state and metadata updates.
        """
        try:
            initial_volume = self.adc_controller.initialize_volume()
            if initial_volume is None:
                logger.error("No valid initial volume reading; audio remains disabled")
                while True:
                    sleep(1)
            self.adc_controller.start_monitoring()
            self.power_switch = self.adc_controller.read_adc_switch()
            self.handle_switch_state_change(1, self.power_switch)
            # Establish the default MPD state before source arbitration starts.
            # If an already-connected USB host is active, the first metadata
            # poll then sees MPD and USB together and the documented service
            # order lets USB win.  Starting MPD after the polling thread used to
            # make MPD look like a later source transition, which inhibited USB.
            if self.source_flags.get("internet_radio", True):
                self.mpd.play_index(1)
            threading.Thread(target=self.metadata_loop, daemon=True).start()
            while True:
                sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            GPIO.cleanup()


if __name__ == "__main__":
    radio_controller = RadioController()
    radio_controller.start()
