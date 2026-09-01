"""Unit tests for the display's transient-overlay timing state machine.

Uses a fake, hand-advanced clock so the OSD/toast/crossfade/idle windows are
exercised deterministically without sleeping.
"""

from display_1_inch_69.transient_state import TransientState


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self, start: float = 100.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_volume_osd_window_and_clamping():
    clk = FakeClock()
    ts = TransientState(clock=clk)
    assert not ts.osd_visible()

    assert ts.show_volume(150, duration=1.5) == 100  # clamped high
    assert ts.volume_pct == 100
    assert ts.osd_visible()

    clk.advance(1.49)
    assert ts.osd_visible()
    clk.advance(0.02)
    assert not ts.osd_visible()

    assert ts.show_volume(-5, duration=1.0) == 0  # clamped low


def test_toast_window_and_blank_clears():
    clk = FakeClock()
    ts = TransientState(clock=clk)
    assert not ts.toast_visible()

    assert ts.show_toast("  MDR JUMP  ", duration=1.6) == "MDR JUMP"
    assert ts.toast_visible()
    clk.advance(1.7)
    assert not ts.toast_visible()

    # Blank text clears any pending toast immediately.
    ts.show_toast("something", duration=5.0)
    assert ts.toast_visible()
    assert ts.show_toast("", duration=5.0) == ""
    assert not ts.toast_visible()


def test_toast_marks_activity():
    clk = FakeClock()
    ts = TransientState(clock=clk)
    clk.advance(50)
    assert ts.idle_elapsed(timeout=30)
    ts.show_toast("preset", duration=1.0)
    assert not ts.idle_elapsed(timeout=30)


def test_idle_elapsed_and_mark_activity():
    clk = FakeClock()
    ts = TransientState(clock=clk)
    assert not ts.idle_elapsed(timeout=30)
    clk.advance(29.9)
    assert not ts.idle_elapsed(timeout=30)
    clk.advance(0.2)
    assert ts.idle_elapsed(timeout=30)
    ts.mark_activity()
    assert not ts.idle_elapsed(timeout=30)


def test_crossfade_active_and_progress():
    clk = FakeClock()
    ts = TransientState(clock=clk)
    assert not ts.crossfade_active()
    assert ts.crossfade_progress(0.15) is None

    sentinel = object()
    ts.start_crossfade(sentinel, duration=0.15)
    assert ts.crossfade_from is sentinel
    assert ts.crossfade_active()

    # Start of the fade -> ~0.0, midway -> ~0.5.
    assert abs(ts.crossfade_progress(0.15) - 0.0) < 1e-6
    clk.advance(0.075)
    assert abs(ts.crossfade_progress(0.15) - 0.5) < 1e-6

    # Past the window: no longer active, progress None.
    clk.advance(0.1)
    assert not ts.crossfade_active()
    assert ts.crossfade_progress(0.15) is None

    ts.clear_crossfade()
    assert ts.crossfade_from is None


def test_crossfade_progress_disabled_when_duration_zero():
    clk = FakeClock()
    ts = TransientState(clock=clk)
    ts.start_crossfade(object(), duration=0.15)
    # A non-positive configured duration disables the effect.
    assert ts.crossfade_progress(0.0) is None
