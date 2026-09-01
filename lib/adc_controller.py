import logging
import threading
from time import sleep
from typing import Callable, Optional

from ADS1x15.Adafruit_ADS1x15 import ADS1x15
from alsa_controller import ALSAController

logger = logging.getLogger(__name__)

ADS1115 = 0x01
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
SWITCH_DEBOUNCE = 0.05    # confirmation delay before accepting a switch change
BUTTON_DEBOUNCE = 0.5     # delay after a button press before accepting the next
ERROR_RETRY_DELAY = 0.5   # delay before retrying the loop after an exception

class ADCController:
    """
    ADCController handles an ADS1115 ADC chip connected to a Raspberry Pi.
    It monitors and processes inputs from a volume knob, buttons, and a power switch.

    Attributes:
        ads (ADS1x15): Instance of the ADS1x15 ADC controller.
        alsa_controller (ALSAController): Manages audio mixer volume settings.
        switch_callback (function): Callback function invoked when the switch state changes.
        button_callback (function): Callback function invoked when a button press is detected.
        adc_thread (threading.Thread): Thread running the ADC handling loop.
    """

    def __init__(self, mixer_name: str,
                 switch_callback: Callable[[int, bool], None],
                 button_callback: Callable[[int], None],
                 volume_callback: Optional[Callable[[int], None]] = None,
                 i2c_address: int = I2C_ADDRESS, i2c_bus: int = 1,
                 volume_min_input: float = 0.93, volume_max_input: float = 3282,
                 button_min: float = 100, button_max: float = 3100,
                 button_tolerance: float = 150) -> None:
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
        """
        self.ads = ADS1x15(address=i2c_address, ic=ADS1115, busnum=i2c_bus)
        self.alsa_controller = ALSAController(mixer_name=mixer_name)
        self.switch_callback = switch_callback
        self.button_callback = button_callback
        self.volume_callback = volume_callback

        # Calibration constants (see radio.conf [adc]).
        self.volume_min_input = volume_min_input
        self.volume_max_input = volume_max_input
        self.button_min = button_min
        self.button_max = button_max
        self.button_tolerance = button_tolerance

        # Start ADC handling thread
        self.adc_thread = threading.Thread(target=self.handle_adc, daemon=True)
        self.adc_thread.start()

    @staticmethod
    def map_value(input_value: float, min_input: float = 0.93,
                  max_input: float = 3282, min_output: int = 0,
                  max_output: int = 100) -> int:
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

    def read_adc_volume(self, channel: int = 0) -> Optional[int]:
        """
        Reads the ADC value for the volume knob and maps it to a volume level.

        Args:
            channel (int): The ADC channel to read from. Defaults to 0.

        Returns:
            int or None: Mapped volume level, or None if the reading fails.
        """
        value = self.ads.readADCSingleEnded(channel=channel)
        if value:
            volume = self.map_value(
                value,
                min_input=self.volume_min_input,
                max_input=self.volume_max_input,
            )
            return volume
        return None

    def read_adc_switch(self, channel: int = 2, threshold: int = 300) -> bool:
        """
        Reads the ADC value for the power switch and determines its state.

        Args:
            channel (int): The ADC channel to read from. Defaults to 2.
            threshold (int): Threshold value to determine the switch state. Defaults to 300.

        Returns:
            bool: True if the switch is active (below threshold), False otherwise.
        """
        value = self.ads.readADCSingleEnded(channel=channel)
        return value <= threshold

    @staticmethod
    def find_button(value: float, min_val: float, max_val: float,
                    tolerance: float) -> Optional[int]:
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

    def read_adc_buttons(self, channel: int = 1, min_val: Optional[float] = None,
                         max_val: Optional[float] = None,
                         tolerance: Optional[float] = None) -> Optional[int]:
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
        value = self.ads.readADCSingleEnded(channel=channel)
        return self.find_button(value, min_val, max_val, tolerance)

    def handle_adc(self) -> None:
        """
        The main loop that handles ADC readings for volume, switch, and buttons.
        It continuously monitors the ADC channels and triggers corresponding callbacks.
        """
        current_volume = 0
        current_switch_state = False
        current_button = None

        while True:
            try:
                desired_volume = self.read_adc_volume()
                if desired_volume is not None and desired_volume != current_volume:
                    current_volume = desired_volume
                    self.alsa_controller.set_volume(desired_volume) # adjust ALSA volume
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
                        self.switch_callback(1, current_switch_state)  # Callback for switch state change

                sleep(ADC_POLL_INTERVAL)

                button = self.read_adc_buttons()
                if button and button != current_button:
                    current_button = button
                    self.button_callback(button)  # Callback for button press
                    sleep(BUTTON_DEBOUNCE)  # Debounce delay

            except Exception as e:
                logger.error(f"Error in ADC handling loop: {e}")
                sleep(ERROR_RETRY_DELAY)  # Delay before retrying the loop
