"""Tests for ADCController in lib/adc_controller.py.

The ``ads1115`` driver and ``alsaaudio`` are stubbed in conftest.py.
``ADCController.__init__`` opens hardware and starts a polling thread, so these
tests either call the static/pure methods directly or build an instance with
``__new__`` and inject a mock ADC — no hardware and no background thread.
"""

from unittest import mock

from adc_controller import ADCController


class TestMapValue:
    def test_min_input_maps_to_zero(self):
        assert ADCController.map_value(0.93, min_input=0.93, max_input=3282) == 0

    def test_max_input_maps_to_hundred(self):
        assert ADCController.map_value(3282, min_input=0.93, max_input=3282) == 100

    def test_midpoint_maps_to_about_fifty(self):
        # The numeric midpoint normalises to ~0.4999 (min_input is 0.93, not
        # 0), and map_value truncates via int(), so the result is 49.
        mid = (0.93 + 3282) / 2
        assert ADCController.map_value(mid, min_input=0.93, max_input=3282) == 49

    def test_result_is_int(self):
        assert isinstance(ADCController.map_value(1500), int)


class TestFindButton:
    # With min=100, max=3100 there are 6 evenly spaced buckets. The central
    # value of bucket i (1..6) is min + step*(i-0.5), step = (max-min)/6 = 500.
    # Centres: 350, 850, 1350, 1850, 2350, 2850.
    def test_each_bucket_centre(self):
        centres = {1: 350, 2: 850, 3: 1350, 4: 1850, 5: 2350, 6: 2850}
        for expected, value in centres.items():
            assert ADCController.find_button(value, 100, 3100, 150) == expected

    def test_within_tolerance(self):
        # 350 +/- 150 still resolves to button 1.
        assert ADCController.find_button(480, 100, 3100, 150) == 1

    def test_gap_between_buttons_returns_none(self):
        # Between bucket 1 (<=500) and bucket 2 (>=700): 600 is outside both.
        assert ADCController.find_button(600, 100, 3100, 150) is None

    def test_out_of_range_returns_none(self):
        assert ADCController.find_button(0, 100, 3100, 150) is None


def _controller_with_mock_ads(read_value):
    ctrl = ADCController.__new__(ADCController)
    ctrl.ads = mock.Mock()
    ctrl.ads.read_channel_mv.return_value = read_value
    ctrl.volume_min_input = 0.93
    ctrl.volume_max_input = 3282
    ctrl.button_min = 100
    ctrl.button_max = 3100
    ctrl.button_tolerance = 150
    return ctrl


class TestReadAdcVolume:
    def test_maps_reading_to_volume(self):
        ctrl = _controller_with_mock_ads(3282)
        assert ctrl.read_adc_volume() == 100

    def test_zero_reading_is_valid_minimum_volume(self):
        ctrl = _controller_with_mock_ads(0)
        assert ctrl.read_adc_volume() == 0

    def test_initial_volume_explicitly_applies_zero(self):
        ctrl = _controller_with_mock_ads(0)
        ctrl.max_volume = 80
        ctrl.alsa_controller = mock.Mock()
        assert ctrl.initialize_volume() == 0
        ctrl.alsa_controller.set_volume.assert_called_once_with(0)


class TestMonitoringLifecycle:
    def test_start_monitoring_is_idempotent(self):
        ctrl = ADCController.__new__(ADCController)
        ctrl.adc_thread = mock.Mock()
        ctrl.adc_thread.is_alive.return_value = True
        ctrl.start_monitoring()
        ctrl.adc_thread.start.assert_not_called()


class TestReadAdcSwitch:
    def test_below_threshold_is_active(self):
        ctrl = _controller_with_mock_ads(100)
        assert ctrl.read_adc_switch() is True

    def test_above_threshold_is_inactive(self):
        ctrl = _controller_with_mock_ads(3000)
        assert ctrl.read_adc_switch() is False

    def test_uses_configured_threshold(self):
        ctrl = _controller_with_mock_ads(450)
        ctrl.switch_threshold = 500
        assert ctrl.read_adc_switch() is True


class TestReadAdcButtons:
    def test_uses_configured_calibration(self):
        ctrl = _controller_with_mock_ads(350)  # centre of bucket 1
        assert ctrl.read_adc_buttons() == 1


class TestDiagnosticsSnapshot:
    def test_snapshot_contains_raw_and_interpreted_channels(self):
        ctrl = _controller_with_mock_ads(0)
        ctrl.snapshot_callback = mock.Mock()
        ctrl._last_snapshot_at = 0
        ctrl._raw_values = {0: 1641, 1: 850, 2: 100, 3: None}
        ctrl.max_volume = 80
        ctrl.switch_threshold = 300
        ctrl.ads.read_channel_mv.return_value = 222
        ctrl._publish_snapshot(49, True, 2)
        snapshot = ctrl.snapshot_callback.call_args.args[0]
        assert snapshot["channels"]["0"]["raw_mv"] == 1641
        assert snapshot["channels"]["1"]["button"] == 2
        assert snapshot["channels"]["2"]["on"] is True
        assert snapshot["channels"]["3"]["raw_mv"] == 222
        assert snapshot["calibration"]["switch_threshold"] == 300


class TestVolumeCallback:
    def _harness(self, callback):
        """A controller whose loop reads one volume then stops via _StopLoop."""
        ctrl = ADCController.__new__(ADCController)
        ctrl.alsa_controller = mock.Mock()
        ctrl.volume_callback = callback
        ctrl.switch_callback = mock.Mock()
        ctrl.button_callback = mock.Mock()
        return ctrl

    def test_volume_callback_fires_on_change(self):
        import adc_controller as adc_mod

        seen = []
        ctrl = self._harness(lambda pct: seen.append(pct))

        # First volume read returns 55 (a change from the loop's initial 0);
        # every subsequent read is unchanged so nothing else fires.
        ctrl.read_adc_volume = mock.Mock(return_value=55)
        ctrl.read_adc_switch = mock.Mock(return_value=False)
        ctrl.read_adc_buttons = mock.Mock(return_value=None)

        # The loop is infinite; cap it by making the Nth sleep break out.
        calls = {"n": 0}

        def _fake_sleep(*_a, **_k):
            calls["n"] += 1
            if calls["n"] > 3:
                raise KeyboardInterrupt

        with mock.patch.object(adc_mod, "sleep", _fake_sleep):
            try:
                ctrl.handle_adc()
            except KeyboardInterrupt:
                pass

        ctrl.alsa_controller.set_volume.assert_called_with(55)
        assert seen and seen[0] == 55

    def test_display_error_does_not_break_volume_loop(self):
        import adc_controller as adc_mod

        ctrl = self._harness(mock.Mock(side_effect=RuntimeError("display down")))
        ctrl.read_adc_volume = mock.Mock(return_value=30)
        ctrl.read_adc_switch = mock.Mock(return_value=False)
        ctrl.read_adc_buttons = mock.Mock(return_value=None)

        calls = {"n": 0}

        def _fake_sleep(*_a, **_k):
            calls["n"] += 1
            if calls["n"] > 2:
                raise KeyboardInterrupt

        with mock.patch.object(adc_mod, "sleep", _fake_sleep):
            try:
                ctrl.handle_adc()
            except KeyboardInterrupt:
                pass

        # The ALSA volume was still applied despite the display callback error.
        ctrl.alsa_controller.set_volume.assert_called_with(30)
        assert ctrl.volume_callback.called

    def test_max_volume_cap_clamps_knob(self):
        # The knob reading is clamped to the cap before
        # it is applied, so the output never exceeds the configured ceiling.
        import adc_controller as adc_mod

        ctrl = self._harness(mock.Mock())
        ctrl.max_volume = 70
        ctrl.read_adc_volume = mock.Mock(return_value=95)
        ctrl.read_adc_switch = mock.Mock(return_value=False)
        ctrl.read_adc_buttons = mock.Mock(return_value=None)

        calls = {"n": 0}

        def _fake_sleep(*_a, **_k):
            calls["n"] += 1
            if calls["n"] > 2:
                raise KeyboardInterrupt

        with mock.patch.object(adc_mod, "sleep", _fake_sleep):
            try:
                ctrl.handle_adc()
            except KeyboardInterrupt:
                pass

        ctrl.alsa_controller.set_volume.assert_called_with(70)
