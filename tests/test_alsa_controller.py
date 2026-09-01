"""Tests for ALSAController in lib/alsa_controller.py.

The ``alsaaudio`` module is stubbed in conftest.py. Covers the pure
linear->log volume mapping and the clamping / re-open behaviour of set_volume
with a mocked mixer.
"""
from unittest import mock

import alsaaudio  # stubbed via conftest
from alsa_controller import ALSAController


class TestLinearToLogVolume:
    def test_zero_maps_to_zero(self):
        assert ALSAController.linear_to_log_volume(0) == 0

    def test_hundred_maps_to_hundred(self):
        assert ALSAController.linear_to_log_volume(100) == 100

    def test_is_monotonic_non_decreasing(self):
        prev = -1
        for v in range(0, 101, 5):
            cur = ALSAController.linear_to_log_volume(v)
            assert cur >= prev
            prev = cur

    def test_log_curve_boosts_low_values(self):
        # log scale: 10 linear -> 50 on the 0..100 log scale.
        assert ALSAController.linear_to_log_volume(10) == 50


def _controller_with_mock_mixer():
    ctrl = ALSAController.__new__(ALSAController)
    ctrl.mixer_name = "Digital"
    ctrl.mixer = mock.Mock()
    return ctrl


class TestSetVolume:
    def test_clamps_below_zero(self):
        ctrl = _controller_with_mock_mixer()
        ctrl.set_volume(-20)
        ctrl.mixer.setvolume.assert_called_once_with(0)

    def test_clamps_above_hundred(self):
        ctrl = _controller_with_mock_mixer()
        ctrl.set_volume(250)
        ctrl.mixer.setvolume.assert_called_once_with(100)

    def test_applies_log_mapping(self):
        ctrl = _controller_with_mock_mixer()
        ctrl.set_volume(10)
        ctrl.mixer.setvolume.assert_called_once_with(50)

    def test_reopens_mixer_when_none(self):
        ctrl = ALSAController.__new__(ALSAController)
        ctrl.mixer_name = "Digital"
        ctrl.mixer = None
        new_mixer = mock.Mock()
        with mock.patch.object(ALSAController, "_open_mixer", return_value=new_mixer) as opener:
            ctrl.set_volume(100)
        opener.assert_called_once()
        new_mixer.setvolume.assert_called_once_with(100)

    def test_reopens_once_on_alsa_error(self):
        ctrl = ALSAController.__new__(ALSAController)
        ctrl.mixer_name = "Digital"
        stale = mock.Mock()
        stale.setvolume.side_effect = alsaaudio.ALSAAudioError("stale")
        ctrl.mixer = stale
        fresh = mock.Mock()
        with mock.patch.object(ALSAController, "_open_mixer", return_value=fresh):
            ctrl.set_volume(100)
        # Fell back to a freshly opened mixer and retried.
        fresh.setvolume.assert_called_once_with(100)
