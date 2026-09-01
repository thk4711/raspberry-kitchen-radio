"""Shared pytest fixtures and hardware/system stubs.

This project targets a Raspberry Pi 3A+ and imports several hardware/system
libraries at module scope (``RPi.GPIO``, ``alsaaudio``, ``dbus``, ``spidev``,
``gpiozero``, ``smbus2`` and the ADS1115 ``ADS1x15`` driver). None of those
exist on a plain development or CI machine, so we install lightweight stub
modules into ``sys.modules`` *before* the tests import the code under test.

Stubbing (rather than skipping the tests) means the pure-logic units get real
coverage everywhere, including CI.
"""
import sys
import types
from unittest import mock

import pytest


def _install_stub(name: str, module: types.ModuleType) -> None:
    """Register ``module`` under ``name`` only if not already importable."""
    if name not in sys.modules:
        sys.modules[name] = module


def _make_rpi_gpio() -> None:
    """Stub ``RPi`` and ``RPi.GPIO`` with harmless no-op attributes."""
    rpi = types.ModuleType("RPi")
    gpio = types.ModuleType("RPi.GPIO")
    gpio.BCM = "BCM"
    gpio.OUT = "OUT"
    gpio.IN = "IN"
    gpio.HIGH = 1
    gpio.LOW = 0
    gpio.setmode = mock.Mock(name="GPIO.setmode")
    gpio.setup = mock.Mock(name="GPIO.setup")
    gpio.output = mock.Mock(name="GPIO.output")
    gpio.input = mock.Mock(name="GPIO.input")
    gpio.cleanup = mock.Mock(name="GPIO.cleanup")
    gpio.setwarnings = mock.Mock(name="GPIO.setwarnings")
    rpi.GPIO = gpio
    _install_stub("RPi", rpi)
    _install_stub("RPi.GPIO", gpio)


def _make_alsaaudio() -> None:
    """Stub ``alsaaudio`` with a ``Mixer`` and ``ALSAAudioError``."""
    alsaaudio = types.ModuleType("alsaaudio")

    class ALSAAudioError(Exception):
        pass

    class Mixer:  # pragma: no cover - replaced/mocked in tests
        def __init__(self, *args, **kwargs):
            pass

        def setvolume(self, *args, **kwargs):
            pass

    alsaaudio.ALSAAudioError = ALSAAudioError
    alsaaudio.Mixer = Mixer
    _install_stub("alsaaudio", alsaaudio)


def _make_dbus() -> None:
    """Stub ``dbus`` with the attributes ``utilities`` touches."""
    dbus = types.ModuleType("dbus")

    class DBusException(Exception):
        pass

    dbus.DBusException = DBusException
    dbus.SystemBus = mock.Mock(name="dbus.SystemBus")
    dbus.Interface = mock.Mock(name="dbus.Interface")
    _install_stub("dbus", dbus)


def _make_ads1x15() -> None:
    """Stub the ``ADS1x15`` driver package used by ``adc_controller``."""
    pkg = types.ModuleType("ADS1x15")
    driver = types.ModuleType("ADS1x15.Adafruit_ADS1x15")

    class ADS1x15:  # pragma: no cover - replaced/mocked in tests
        def __init__(self, *args, **kwargs):
            pass

        def readADCSingleEnded(self, *args, **kwargs):
            return 0

    driver.ADS1x15 = ADS1x15
    pkg.Adafruit_ADS1x15 = driver
    _install_stub("ADS1x15", pkg)
    _install_stub("ADS1x15.Adafruit_ADS1x15", driver)


def _make_simple_stub(name: str) -> None:
    """Register a bare module stub (enough to satisfy an ``import name``)."""
    _install_stub(name, types.ModuleType(name))


# Install all hardware/system stubs at import time, before any test module
# imports the code under test.
_make_rpi_gpio()
_make_alsaaudio()
_make_dbus()
_make_ads1x15()
for _name in ("spidev", "gpiozero", "smbus2"):
    _make_simple_stub(_name)


@pytest.fixture
def gpio_stub():
    """Convenience accessor for the stubbed ``RPi.GPIO`` module."""
    return sys.modules["RPi.GPIO"]
