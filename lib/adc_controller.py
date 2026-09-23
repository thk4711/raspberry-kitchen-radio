import logging
import threading
import time
from time import sleep
from typing import Callable, Optional

from ads1115 import ADS1115
from alsa_controller import ALSAController

logger = logging.getLogger(__name__)

I2C_ADDRESS = 0x48

# Polling / debounce timing for the ADC input loop.
#
# Note on interrupt/edge-driven input: the volume knob, button ladder and power
# switch are all read as analog voltages through the ADS1115 over I2C, not via
# discrete GPIO lines. GPIO edge detection therefore cannot be used for these
# inputs, so the loop polls the ADC on a fixed cadence. The cadence and
# debounce windows are named here to keep the latency/debounce behaviour
# explicit and easy to tune.
ADC_POLL_INTERVAL = 0.05  # seconds between successive channel reads
SWITCH_DEBOUNCE = 0.05  # confirmation delay before accepting a switch change
BUTTON_DEBOUNCE = 0.5  # delay after a button press before accepting the next
ERROR_RETRY_DELAY = 0.5  # delay before retrying the loop after an exception
SNAPSHOT_INTERVAL = 0.2  # at most five diagnostics snapshots per second


class ADCController:
    """
    ADCController handles an ADS1115 ADC chip connected to a Raspberry Pi.
    It monitors and processes inputs from a volume knob, buttons, and a power switch.

    Attributes:
        ads (ADS1115): Instance of the ADS1115 ADC driver.
        alsa_controller (ALSAController): Manages audio mixer volume settings.
        switch_callback (function): Callback function invoked when the switch state changes.
        button_callback (function): Callback function invoked when a button press is detected.
        adc_thread (threading.Thread): Thread running the ADC handling loop.
    """

    def __init__(
        self,
        mixer_name: str,
        switch_callback: Callable[[int, bool], None],
        button_callback: Callable[[int], None],
        volume_callback: Optional[Callable[[int], None]] = None,
        i2c_address: int = I2C_ADDRESS,
        i2c_bus: int = 1,
        volume_min_input: float = 0.93,
        volume_max_input: float = 3282,
        button_min: float = 100,
        button_max: float = 3100,
        button_tolerance: float = 150,
        switch_threshold: float = 300,
        max_volume: int = 100,
        mixer_max_percent: int = 100,
        snapshot_callback: Optional[Callable[[dict], None]] = None,
        start_thread: bool = True,
    ) -> None:
        """
        Initializes the ADCController class.

        Args:
            mixer_name (str): The name of the ALSA mixer to control.
            switch_callback (function): Callback function for switch state changes.
            button_callback (function): Callback function for button presses.
            volume_callback (function, optional): Called with the new 0..100
                volume level whenever the knob moves, in addition to setting the
                ALSA mixer. Used to drive the on-screen volume OSD. ``None``
                (the default) disables the notification.
            i2c_address (int): I2C address of the ADS1115. Defaults to 0x48.
            i2c_bus (int): Linux I2C bus number. Defaults to 1 (/dev/i2c-1).
            volume_min_input (float): Minimum ADC value (mV) for the volume knob,
                mapped to 0. Defaults to 0.93.
            volume_max_input (float): Maximum ADC value (mV) for the volume knob,
                mapped to 100. Defaults to 3282.
            button_min (float): Minimum ADC value (mV) of the button ladder.
                Defaults to 100.
            button_max (float): Maximum ADC value (mV) of the button ladder.
                Defaults to 3100.
            button_tolerance (float): Tolerance (mV) for button detection.
                Defaults to 150.
            max_volume (int): Maximum ALSA output volume (0..100) the knob is
                allowed to reach. The mapped knob level is clamped to this cap
                before it is applied, so the physical knob can never exceed it.
                Defaults to 100 (uncapped). Managed by the web UI.
        """
        self.ads = ADS1115(i2c_address=i2c_address, i2c_bus=i2c_bus)
        self.alsa_controller = ALSAController(
            mixer_name=mixer_name, mixer_max_percent=mixer_max_percent
        )
        self.switch_callback = switch_callback
        self.button_callback = button_callback
        self.volume_callback = volume_callback

        # Calibration constants (see radio.conf [adc]).
        self.volume_min_input = volume_min_input
        self.volume_max_input = volume_max_input
        self.button_min = button_min
        self.button_max = button_max
        self.button_tolerance = button_tolerance
        self.switch_threshold = switch_threshold
        # Maximum output volume cap (0..100). Clamped defensively so an
        # out-of-range managed value can never disable audio or exceed 100.
        self.max_volume = max(0, min(100, int(max_volume)))
        self.snapshot_callback = snapshot_callback
        self._last_snapshot_at = 0.0
        self._raw_values = {0: None, 1: None, 2: None, 3: None}

        self.adc_thread: Optional[threading.Thread] = None
        if start_thread:
            self.start_monitoring()

    def start_monitoring(self) -> None:
        """Start the input loop once, after initial volume has been applied."""
        if self.adc_thread is not None and self.adc_thread.is_alive():
            return
        self.adc_thread = threading.Thread(target=self.handle_adc, daemon=True)
        self.adc_thread.start()

    @staticmethod
    def map_value(
        input_value: float,
        min_input: float = 0.93,
        max_input: float = 3282,
        min_output: int = 0,
        max_output: int = 100,
    ) -> int:
        """
        Maps an input ADC value to a corresponding output range.

        Args:
            input_value (float): The input ADC value to be mapped.
            min_input (float): Minimum input value for normalization. Defaults to 0.93.
            max_input (float): Maximum input value for normalization. Defaults to 3282.
            min_output (int): Minimum value of the output range. Defaults to 0.
            max_output (int): Maximum value of the output range. Defaults to 100.

        Returns:
            int: Mapped output value in the specified range.
        """
        normalized_value = (input_value - min_input) / (max_input - min_input)
        mapped_value = normalized_value * (max_output - min_output) + min_output
        return int(mapped_value)

    def _read_channel(self, channel: int) -> float:
        value = self.ads.read_channel_mv(channel)
        if not hasattr(self, "_raw_values"):
            self._raw_values = {0: None, 1: None, 2: None, 3: None}
        self._raw_values[channel] = value
        return value

    def read_adc_volume(self, channel: int = 0) -> Optional[int]:
        """
        Reads the ADC value for the volume knob and maps it to a volume level.

        Args:
            channel (int): The ADC channel to read from. Defaults to 0.

        Returns:
            int or None: Mapped volume level, or None if the reading fails.
        """
        value = self._read_channel(channel)
        return max(
            0,
            min(
                100,
                self.map_value(
                    value,
                    min_input=self.volume_min_input,
                    max_input=self.volume_max_input,
                ),
            ),
        )

    def initialize_volume(self) -> Optional[int]:
        """Read and explicitly apply the knob before playback is permitted."""
        try:
            volume = self.read_adc_volume()
        except Exception as exc:
            logger.error("Unable to read initial volume: %s", exc)
            return None
        if volume is None:
            return None
        volume = min(volume, self.max_volume)
        self.alsa_controller.set_volume(volume)
        return volume

    def read_adc_switch(self, channel: int = 2, threshold: Optional[float] = None) -> bool:
        """
        Reads the ADC value for the power switch and determines its state.

        Args:
            channel (int): The ADC channel to read from. Defaults to 2.
            threshold (int): Threshold value to determine the switch state. Defaults to 300.

        Returns:
            bool: True if the switch is active (below threshold), False otherwise.
        """
        value = self._read_channel(channel)
        if threshold is None:
            threshold = getattr(self, "switch_threshold", 300)
        return value <= threshold

    @staticmethod
    def find_button(
        value: float, min_val: float, max_val: float, tolerance: float
    ) -> Optional[int]:
        """
        Identifies which button is pressed based on the ADC value.

        Args:
            value (float): The ADC value to evaluate.
            min_val (float): The minimum ADC value corresponding to a button.
            max_val (float): The maximum ADC value corresponding to a button.
            tolerance (float): The tolerance range to identify button presses.

        Returns:
            int or None: The identified button number (1-6), or None if no button is detected.
        """
        categories = range(1, 7)
        step = (max_val - min_val) / 6
        for i in categories:
            central_value = min_val + step * (i - 0.5)
            lower_bound = central_value - tolerance
            upper_bound = central_value + tolerance
            if lower_bound <= value <= upper_bound:
                return i
        return None

    def read_adc_buttons(
        self,
        channel: int = 1,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None,
        tolerance: Optional[float] = None,
    ) -> Optional[int]:
        """
        Reads the ADC value for the buttons and identifies which button is pressed.

        Args:
            channel (int): The ADC channel to read from. Defaults to 1.
            min_val (float): Minimum ADC value corresponding to the first button.
                Falls back to the configured ``button_min`` when None.
            max_val (float): Maximum ADC value corresponding to the last button.
                Falls back to the configured ``button_max`` when None.
            tolerance (float): Tolerance range to identify button presses.
                Falls back to the configured ``button_tolerance`` when None.

        Returns:
            int or None: The identified button number (1-6), or None if no button is detected.
        """
        if min_val is None:
            min_val = self.button_min
        if max_val is None:
            max_val = self.button_max
        if tolerance is None:
            tolerance = self.button_tolerance
        value = self._read_channel(channel)
        return self.find_button(value, min_val, max_val, tolerance)

    def _publish_snapshot(self, volume: Optional[int], switch: bool, button: Optional[int]) -> None:
        callback = getattr(self, "snapshot_callback", None)
        now = time.monotonic()
        last_snapshot = getattr(self, "_last_snapshot_at", 0.0)
        if callback is None or now - last_snapshot < SNAPSHOT_INTERVAL:
            return
        self._last_snapshot_at = now
        try:
            unused = self._read_channel(3)
            raw_volume = self._raw_values[0]
            mapped = (
                None
                if raw_volume is None
                else max(
                    0,
                    min(
                        100,
                        self.map_value(raw_volume, self.volume_min_input, self.volume_max_input),
                    ),
                )
            )
            callback(
                {
                    "channels": {
                        "0": {
                            "name": "Volume",
                            "raw_mv": raw_volume,
                            "mapped_percent": mapped,
                            "applied_percent": volume,
                        },
                        "1": {"name": "Buttons", "raw_mv": self._raw_values[1], "button": button},
                        "2": {"name": "Power", "raw_mv": self._raw_values[2], "on": bool(switch)},
                        "3": {"name": "Unused", "raw_mv": unused},
                    },
                    "calibration": {
                        "volume_min_input": self.volume_min_input,
                        "volume_max_input": self.volume_max_input,
                        "button_min": self.button_min,
                        "button_max": self.button_max,
                        "button_tolerance": self.button_tolerance,
                        "switch_threshold": self.switch_threshold,
                        "max_volume": self.max_volume,
                    },
                }
            )
        except Exception as exc:
            logger.debug("Unable to publish ADC diagnostics: %s", exc)

    def handle_adc(self) -> None:
        """
        The main loop that handles ADC readings for volume, switch, and buttons.
        It continuously monitors the ADC channels and triggers corresponding callbacks.
        """
        current_volume = None
        current_switch_state = False
        current_button = None

        while True:
            try:
                desired_volume = self.read_adc_volume()
                if desired_volume is not None:
                    # Enforce the maximum-volume cap
                    # before applying, so the physical knob can never drive the
                    # output above the configured ceiling. The OSD reflects the
                    # capped value too. getattr keeps this robust if the
                    # controller was built without the cap (e.g. in tests).
                    cap = getattr(self, "max_volume", 100)
                    desired_volume = min(desired_volume, cap)
                if desired_volume is not None and desired_volume != current_volume:
                    current_volume = desired_volume
                    self.alsa_controller.set_volume(desired_volume)  # adjust ALSA volume
                    if self.volume_callback is not None:
                        # Notify the display so it can show the volume OSD. Never
                        # let a display error break the audio-volume loop.
                        try:
                            self.volume_callback(desired_volume)
                        except Exception as e:
                            logger.error(f"volume_callback failed: {e}")

                sleep(ADC_POLL_INTERVAL)

                desired_switch_state = self.read_adc_switch()
                if desired_switch_state != current_switch_state:
                    sleep(SWITCH_DEBOUNCE)  # Debounce delay
                    if self.read_adc_switch() == desired_switch_state:
                        current_switch_state = desired_switch_state
                        self.switch_callback(
                            1, current_switch_state
                        )  # Callback for switch state change

                sleep(ADC_POLL_INTERVAL)

                button = self.read_adc_buttons()
                if button and button != current_button:
                    current_button = button
                    self.button_callback(button)  # Callback for button press
                    sleep(BUTTON_DEBOUNCE)  # Debounce delay

                self._publish_snapshot(desired_volume, desired_switch_state, button)

            except Exception as e:
                logger.error(f"Error in ADC handling loop: {e}")
                sleep(ERROR_RETRY_DELAY)  # Delay before retrying the loop
