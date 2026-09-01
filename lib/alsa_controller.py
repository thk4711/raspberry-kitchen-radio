import logging
import math
from typing import Optional

import alsaaudio

logger = logging.getLogger(__name__)


class ALSAController:
    """Thin wrapper around a single long-lived ALSA mixer handle.

    The volume knob (polled by :class:`~adc_controller.ADCController`) can call
    :meth:`set_volume` many times per second, so the mixer is opened once and
    reused, and transparently re-opened if the handle goes stale.
    """

    def __init__(self, mixer_name: str = 'Digital') -> None:
        """Open the named ALSA mixer and cache the handle.

        Args:
            mixer_name (str): The ALSA mixer control name (e.g. ``Digital``).
        """
        self.mixer_name = mixer_name
        # Open the mixer once and reuse it. Opening a new ``alsaaudio.Mixer``
        # on every volume tick (the ADC loop can call this many times/second)
        # is wasteful; keep a single long-lived handle instead.
        self.mixer: Optional[alsaaudio.Mixer] = self._open_mixer()

    def _open_mixer(self) -> Optional['alsaaudio.Mixer']:
        """Open (or re-open) the ALSA mixer handle.

        Returns:
            alsaaudio.Mixer or None: The opened mixer, or ``None`` on failure.
        """
        try:
            return alsaaudio.Mixer(self.mixer_name)
        except alsaaudio.ALSAAudioError as e:
            logger.error(f"Unable to open ALSA mixer '{self.mixer_name}': {e}")
            return None

    @staticmethod
    def linear_to_log_volume(volume: int) -> int:
        """Convert linear volume (0-100) to logarithmic scale (0-100).

        Args:
            volume (int): Linear volume level in the range 0-100.

        Returns:
            int: The corresponding logarithmic volume level (0-100).
        """
        if volume == 0:
            return 0
        return int(100 * (math.log10(volume) / math.log10(100)))

    def set_volume(self, volume: int) -> None:
        """Set the volume on the ALSA mixer.

        The input is clamped to 0-100 and mapped through
        :meth:`linear_to_log_volume`. A stale mixer handle is re-opened once
        before giving up.

        Args:
            volume (int): Desired linear volume level (clamped to 0-100).
        """
        if volume < 0:
            volume = 0
        elif volume > 100:
            volume = 100

        log_volume = self.linear_to_log_volume(volume)

        if self.mixer is None:
            self.mixer = self._open_mixer()
            if self.mixer is None:
                return

        try:
            self.mixer.setvolume(log_volume)
        except alsaaudio.ALSAAudioError as e:
            # The handle may have gone stale; try to re-open it once.
            logger.warning(f"Failed to set volume, re-opening mixer: {e}")
            self.mixer = self._open_mixer()
            if self.mixer is not None:
                try:
                    self.mixer.setvolume(log_volume)
                except alsaaudio.ALSAAudioError as e2:
                    logger.error(f"Unable to set volume after re-open: {e2}")
