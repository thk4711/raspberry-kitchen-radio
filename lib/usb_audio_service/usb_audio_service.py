"""USB Audio Class 1 gadget source integration.

The BusyBox init service owns the ConfigFS gadget and ALSA bridge. This class
observes the gadget's ALSA ``Capture Rate`` control and participates in the
radio's single-active-source arbitration.
"""

import logging
import os
import subprocess
from pathlib import Path

from music_source import Metadata, MusicSource

logger = logging.getLogger(__name__)


class USBAudioService(MusicSource):
    """Expose host playback through the UAC1 gadget as a ``MusicSource``.

    USB Audio has no remote pause command. When another source wins, an inhibit
    marker tells the bridge to close ``alsaloop`` and keeps this source inactive
    until the host closes its stream. A later host stream can then take over.
    """

    def __init__(
        self,
        card: str = "UAC1Gadget",
        inhibit_file: str = "/run/usb-audio-inhibited",
        command_timeout: float = 2.0,
        hid_helper: str = "/usr/bin/radio-usb-audio-hid",
    ) -> None:
        self.name = "usb"
        configured_card = os.environ.get("RADIO_USB_AUDIO_CARD")
        if configured_card:
            self.card = configured_card
        else:
            mode_file = Path("/etc/radio/usb_audio.ini")
            mode = ""
            try:
                for line in mode_file.read_text(encoding="utf-8").splitlines():
                    key, separator, value = line.partition("=")
                    if key.strip() == "mode" and separator:
                        mode = value.strip().lower()
                        break
            except OSError:
                pass
            self.card = "UAC2Gadget" if mode == "uac2" else card
        self.inhibit_file = Path(os.environ.get("RADIO_USB_AUDIO_INHIBIT_FILE", inhibit_file))
        self.command_timeout = command_timeout
        self.hid_helper = os.environ.get("RADIO_USB_AUDIO_HID_HELPER", hid_helper)
        self._host_paused = False
        # Inhibition belongs to the lifetime of the radio controller. Do not
        # carry a marker over a controller restart: if the host still has an
        # active stream, the fresh controller must be able to detect it.
        self._set_inhibited(False)

    def _capture_rate(self) -> int:
        """Return the host-selected capture rate, or zero if unavailable."""
        try:
            result = subprocess.run(
                [
                    "/usr/bin/amixer",
                    "-c",
                    self.card,
                    "cget",
                    "iface=PCM,name='Capture Rate'",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Unable to read USB Audio capture rate: %s", exc)
            return 0
        if result.returncode != 0:
            return 0
        for line in result.stdout.splitlines():
            # ``amixer cget`` also prints control metadata containing
            # ``values=1`` (the number of values).  Only the final state line,
            # ``: values=<rate>``, is the volatile Capture Rate value.
            state = line.strip()
            marker = ": values="
            if not state.startswith(marker):
                continue
            value = state[len(marker) :].strip().split(",", 1)[0]
            if value.isdigit():
                return int(value)
        return 0

    def _set_inhibited(self, inhibited: bool) -> bool:
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
            logger.warning("Unable to change USB Audio inhibition: %s", exc)
            return False

    def get_play_state(self) -> bool:
        rate = self._capture_rate()
        if rate <= 0:
            # Once the host closes the old stream, permit the next stream to
            # become active without requiring radio-side intervention.
            if self.inhibit_file.exists():
                self._set_inhibited(False)
            return False
        inhibited = self.inhibit_file.exists()
        if not inhibited:
            # An independently started stream supersedes any pause request for
            # an earlier stream, so a later Play command must not toggle it.
            self._host_paused = False
        return not inhibited

    def _send_media_key(self, key: str) -> bool:
        """Best-effort media key to the host over the composite HID gadget.

        Returns True when the helper reported success. Any failure (helper
        missing, host not enumerating the HID interface, timeout) is treated as a
        no-op. Many hosts map these keys to the foreground media app, but
        behaviour is not universal.
        """
        if not self.hid_helper:
            return False
        try:
            result = subprocess.run(
                [self.hid_helper, key],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Unable to send USB Audio media key: %s", exc)
            return False
        return result.returncode == 0

    def set_play_state(self, desired_state: bool) -> bool:
        """Enable routing, or inhibit it until the current host stream closes.

        On stop, additionally send a best-effort Play/Pause media key so a host
        that honours HID consumer keys actually pauses its player. On start,
        balance a pause key sent by this service, even if the host keeps its
        capture stream open while paused. Otherwise, send the key only when the
        host has no active capture stream. The marker remains the authoritative
        routing state.
        """
        if desired_state:
            if self._host_paused or self._capture_rate() <= 0:
                self._send_media_key("playpause")
            self._host_paused = False
        else:
            if not self._host_paused:
                self._host_paused = self._send_media_key("playpause")
        return self._set_inhibited(not desired_state)

    def play_index(self, index: int) -> bool:
        """USB Audio has no preset selection operation."""
        return False

    def next_track(self) -> bool:
        """Send the HID consumer-control Next key to the host."""
        return self._send_media_key("next")

    def previous_track(self) -> bool:
        """Send the HID consumer-control Previous key to the host."""
        return self._send_media_key("previous")

    def get_metadata(self) -> Metadata:
        state = self.get_play_state()
        return Metadata(
            name="USB Audio",
            title="Connected source" if state else "",
            cover="",
            md5="",
            state=state,
        )
