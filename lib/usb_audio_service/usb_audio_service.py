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
        return not self.inhibit_file.exists()

    def set_play_state(self, desired_state: bool) -> bool:
        """Enable routing, or inhibit it until the current host stream closes."""
        return self._set_inhibited(not desired_state)

    def play_index(self, index: int) -> bool:
        """USB Audio has no preset selection operation."""
        return False

    def get_metadata(self) -> Metadata:
        state = self.get_play_state()
        return Metadata(
            name="USB Audio",
            title="Connected source" if state else "",
            cover="",
            md5="",
            state=state,
        )
