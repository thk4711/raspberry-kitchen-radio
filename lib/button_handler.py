# button_handler.py
"""Optional I2C push-button reader for a PCF8574 GPIO expander.

This provides a hardware button path parallel to the ADC button ladder in
:mod:`adc_controller`. It polls a PCF8574 over I2C, debounces the readings, and
invokes a callback with the pressed button number (1..6) to match the
``RadioController.handle_button_press`` / ``MPDService.play_index`` contract.
"""

import logging
import time
from typing import Callable, Optional

import smbus2

logger = logging.getLogger(__name__)


class ButtonHandler:
    """Debounced reader for six buttons wired to a PCF8574 I2C expander."""

    def __init__(self, i2c_bus: int, address: int) -> None:
        """Open the I2C bus and initialise the debounce state.

        Args:
            i2c_bus (int): Linux I2C bus number (e.g. ``1`` for ``/dev/i2c-1``).
            address (int): I2C address of the PCF8574 expander.
        """
        self.bus = smbus2.SMBus(i2c_bus)
        self.address = address
        self.last_data = 0b00111111
        self.debounce_timer = 0.0
        self.debounce_time = 0.5
        self.check_interval = 0.01

    def read_pcf8574(self) -> Optional[int]:
        """Read one byte from the PCF8574.

        Returns:
            int or None: The raw byte read from the expander, or ``None`` when
            the read fails.
        """
        try:
            data = self.bus.read_byte(self.address)
            return data
        except Exception as e:
            logger.error(f"Error reading from PCF8574: {e}")
            return None

    def monitor_buttons(self, callback: Callable[[int], None]) -> None:
        """Continuously poll the expander and dispatch button presses.

        Args:
            callback (Callable[[int], None]): Invoked with the pressed button
                number (1..6) each time a debounced press is detected.
        """
        while True:
            self.poll_once(callback)
            time.sleep(self.check_interval)

    def poll_once(self, callback: Callable[[int], None], now: Optional[float] = None) -> None:
        """Poll and dispatch one reading, allowing deterministic host tests.

        ``monitor_buttons`` remains the production loop. Keeping one iteration
        here makes debounce, active-low mapping, and I2C failures testable
        without starting an unbounded thread. ``now`` defaults to the system
        clock and is injectable only for tests.
        """
        data = self.read_pcf8574()
        if data is None or data == self.last_data:
            return

        self.last_data = data
        current_time = time.time() if now is None else now
        if current_time - self.debounce_timer < self.debounce_time:
            return

        pressed_buttons = ~data & 0b00111111
        for i in range(6):
            if pressed_buttons & (1 << i):
                # Buttons are reported 1..6 to match the ADC button path and
                # RadioController/MPDService contracts.
                callback(i + 1)
        self.debounce_timer = current_time

    def cleanup(self) -> None:
        """Close the underlying I2C bus handle."""
        self.bus.close()
