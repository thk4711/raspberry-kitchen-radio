"""Minimal single-shot ADS1115 ADC driver used by :mod:`adc_controller`.

This is a small, first-party replacement for the previously vendored
``ADS1x15`` library (``lib/ADS1x15``). The radio only ever performs
single-ended, single-shot reads in millivolts on the four ADS1115 channels
(volume knob, button ladder, power switch, and one unused channel), always at
the +/-6.144 V full-scale range and 50 samples per second. Every other feature
of the old library (ADS1015 support, differential reads, the comparator and
threshold registers, and continuous-conversion mode) was unused, so this
driver implements only what the appliance needs.

The conversion result is returned in millivolts so the calibration constants in
``radio.conf`` (``[adc]``) remain valid without change.

The chip is driven over I2C via ``smbus2`` (already shipped in the Buildroot
appliance image as ``BR2_PACKAGE_PYTHON_SMBUS2``).
"""

import logging
import time
from typing import Optional

import smbus2

logger = logging.getLogger(__name__)

# Default I2C address of the ADS1115 (ADDR pin tied to GND).
DEFAULT_I2C_ADDRESS = 0x48
# Linux I2C bus number (/dev/i2c-1 on the Raspberry Pi 3A+).
DEFAULT_I2C_BUS = 1

# Pointer register addresses (datasheet section 9.6).
_REG_POINTER_CONVERT = 0x00
_REG_POINTER_CONFIG = 0x01

# Config register fields (datasheet Table 8). Only the bits the radio uses are
# defined here; everything else stays at its power-on default of 0.
_CONFIG_OS_SINGLE = 0x8000  # Write 1 to start a single conversion.

# Input multiplexer: single-ended AINx measured against GND, indexed by channel.
_CONFIG_MUX_SINGLE = {
    0: 0x4000,
    1: 0x5000,
    2: 0x6000,
    3: 0x7000,
}

# Programmable gain amplifier: +/-6.144 V full scale (matches the old default).
_CONFIG_PGA_6_144V = 0x0000
# Full-scale voltage, in millivolts, corresponding to _CONFIG_PGA_6_144V.
_PGA_FULL_SCALE_MV = 6144.0

_CONFIG_MODE_SINGLE = 0x0100  # Power-down single-shot mode.

# Data rate: 8 samples per second is the closest ADS1115 rate to the 50 SPS the
# old library requested; the ADS1115 has no 50 SPS setting, so it silently fell
# back to its 250 SPS default. We keep 250 SPS for identical behaviour and time
# the conversion wait against the effective rate below.
_CONFIG_DR_250SPS = 0x00A0
_SAMPLE_RATE_SPS = 250

# Comparator disabled, ALERT/RDY left in its high-impedance state (the pin is
# not wired on this board).
_CONFIG_CQUE_NONE = 0x0003


class ADS1115:
    """Single-shot, single-ended reader for an ADS1115 on I2C.

    Attributes:
        i2c_address: 7-bit I2C address of the ADS1115.
        bus: The open :class:`smbus2.SMBus` handle.
    """

    def __init__(
        self,
        i2c_address: int = DEFAULT_I2C_ADDRESS,
        i2c_bus: int = DEFAULT_I2C_BUS,
    ) -> None:
        """Open the I2C bus for the ADS1115 at ``i2c_address``.

        Args:
            i2c_address: 7-bit I2C address of the ADS1115. Defaults to 0x48.
            i2c_bus: Linux I2C bus number. Defaults to 1 (/dev/i2c-1).
        """
        self.i2c_address = i2c_address
        self.bus = smbus2.SMBus(i2c_bus)
        logger.debug("ADS1115: opened bus %d at address 0x%02X", i2c_bus, i2c_address)

    def read_channel_mv(self, channel: int) -> Optional[float]:
        """Read one single-ended channel and return the voltage in millivolts.

        Performs a single-shot conversion on ``channel`` at the +/-6.144 V range
        and 250 SPS, waits for it to finish, then reads and converts the raw
        signed 16-bit result to millivolts.

        Args:
            channel: The single-ended input to read (0..3).

        Returns:
            The measured voltage in millivolts, or ``None`` if the channel is
            out of range or the I2C transfer fails.
        """
        if channel not in _CONFIG_MUX_SINGLE:
            logger.debug("ADS1115: invalid channel specified: %r", channel)
            return None

        config = (
            _CONFIG_OS_SINGLE
            | _CONFIG_MUX_SINGLE[channel]
            | _CONFIG_PGA_6_144V
            | _CONFIG_MODE_SINGLE
            | _CONFIG_DR_250SPS
            | _CONFIG_CQUE_NONE
        )

        try:
            # The config register is written big-endian (MSB first).
            self.bus.write_i2c_block_data(
                self.i2c_address,
                _REG_POINTER_CONFIG,
                [(config >> 8) & 0xFF, config & 0xFF],
            )

            # Wait for the conversion to complete. The minimum delay is 1/sps;
            # add a small margin to be safe.
            time.sleep(1.0 / _SAMPLE_RATE_SPS + 0.0001)

            hi, lo = self.bus.read_i2c_block_data(self.i2c_address, _REG_POINTER_CONVERT, 2)
        except OSError as exc:
            logger.error("ADS1115: I2C error reading channel %d: %s", channel, exc)
            return None

        # The conversion register is a signed 16-bit big-endian value.
        raw = (hi << 8) | lo
        if raw > 0x7FFF:
            raw -= 0x10000
        return raw * _PGA_FULL_SCALE_MV / 32768.0
