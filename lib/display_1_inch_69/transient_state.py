"""Transient overlay timing state for the SPI display.

This is the small, dependency-free state machine that governs the display's
*time-based* overlays, extracted out of the 1000-line
:class:`~display_1_inch_69.display_control.DisplayController` so it can be
reasoned about and unit-tested in isolation:

* the auto-hiding **volume OSD** (last percentage + a ``monotonic()`` deadline);
* the brief centred **preset toast** (station name + deadline);
* the art-layer **crossfade** (previous art image + deadline);
* the **idle-activity** timestamp that drives the clock screensaver.

It holds no PIL/SPI/threading dependency: the caller
(:class:`DisplayController`) still owns the ``_state_lock`` and calls these
methods while holding it, exactly as before. The monotonic clock is injectable
so tests advance time deterministically instead of sleeping.

The rendering code is intentionally left in ``display_control.py``; this module
only owns the *timing/state* half of the transient overlays.
"""

from __future__ import annotations

from time import monotonic
from typing import Any, Callable, Optional


class TransientState:
    """Deadlines + payloads for the display's time-based overlays.

    All ``*_visible`` / ``*_active`` predicates compare the injected clock
    against a stored ``monotonic()`` deadline, matching the previous inline
    behaviour byte-for-byte.

    Args:
        clock: Callable returning a monotonically increasing float (seconds).
            Defaults to :func:`time.monotonic`; tests pass a fake clock.
    """

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        # Volume OSD: visible while clock() < _osd_until.
        self.osd_until: float = 0.0
        self.volume_pct: int = 0
        # Preset toast: visible while text is set and clock() < _toast_until.
        self.toast_until: float = 0.0
        self.toast_text: str = ""
        # Art crossfade: active while a source image is set and clock() < until.
        self.crossfade_until: float = 0.0
        self.crossfade_from: Optional[Any] = None
        # Idle screensaver bookkeeping: timestamp of the last playback activity.
        self.last_activity: float = self._clock()

    # -- mutators (called by DisplayController under its _state_lock) ---------

    def show_volume(self, pct: int, duration: float) -> int:
        """Record the volume OSD level and arm its ``duration``-second window.

        Returns the clamped percentage so the caller can store/display it.
        """
        pct = max(0, min(100, int(pct)))
        self.volume_pct = pct
        self.osd_until = self._clock() + duration
        return pct

    def show_toast(self, text: str, duration: float) -> str:
        """Arm the preset toast for ``duration`` seconds (also marks activity).

        A blank/empty ``text`` clears any pending toast. Returns the stripped
        text so the caller can decide whether anything is shown.
        """
        text = (text or "").strip()
        self.toast_text = text
        self.toast_until = self._clock() + duration if text else 0.0
        self.last_activity = self._clock()
        return text

    def mark_activity(self) -> None:
        """Reset the idle timer (any playback / preset press counts)."""
        self.last_activity = self._clock()

    def start_crossfade(self, from_image: Any, duration: float) -> None:
        """Begin an art-layer crossfade *from* ``from_image`` over ``duration``."""
        self.crossfade_from = from_image
        self.crossfade_until = self._clock() + duration

    def clear_crossfade(self) -> None:
        """Drop the finished crossfade source image."""
        self.crossfade_from = None

    # -- predicates -----------------------------------------------------------

    def osd_visible(self) -> bool:
        """True while the volume OSD is within its display window."""
        return self._clock() < self.osd_until

    def toast_visible(self) -> bool:
        """True while the preset toast text is set and within its window."""
        return bool(self.toast_text) and self._clock() < self.toast_until

    def crossfade_active(self) -> bool:
        """True while an art-layer crossfade is still in progress."""
        return self.crossfade_from is not None and self._clock() < self.crossfade_until

    def crossfade_progress(self, duration: float) -> Optional[float]:
        """Return the 0..1 crossfade blend factor, or ``None`` when inactive.

        ``duration`` is the configured crossfade length in seconds; a
        non-positive duration disables the effect (returns ``None``). When the
        window has elapsed this also returns ``None`` so the caller falls back
        to the plain current art.
        """
        if self.crossfade_from is None or duration <= 0:
            return None
        now = self._clock()
        if now >= self.crossfade_until:
            return None
        t = 1.0 - (self.crossfade_until - now) / duration
        return max(0.0, min(1.0, t))

    def idle_elapsed(self, timeout: float) -> bool:
        """True when ``timeout`` seconds have passed since the last activity."""
        return self._clock() - self.last_activity >= timeout
