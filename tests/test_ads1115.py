"""Tests for the first-party ADS1115 driver in ``lib/ads1115.py``.

``conftest.py`` installs bare stubs for ``ads1115`` and ``smbus2`` so the rest
of the suite runs without hardware. This module needs the *real* ``ads1115``
implementation, so it removes those stubs and installs a recording fake
``smbus2.SMBus`` before importing the driver under test.
"""

import importlib
import sys
import types
from typing import List, Tuple


class FakeSMBus:
    """Records config writes and replays a queued conversion result."""

    #: Two-byte big-endian value returned by ``read_i2c_block_data``.
    next_read: List[int] = [0x00, 0x00]

    def __init__(self, busnum: int) -> None:
        self.busnum = busnum
        self.writes: List[Tuple[int, int, List[int]]] = []

    def write_i2c_block_data(self, address: int, register: int, data: List[int]) -> None:
        self.writes.append((address, register, list(data)))

    def read_i2c_block_data(self, address: int, register: int, length: int) -> List[int]:
        return list(self.next_read)


def _load_driver():
    """Import the real ``ads1115`` module against the fake ``smbus2``.

    ``conftest.py`` installs bare stubs for both ``ads1115`` and ``smbus2`` for
    the rest of the suite. We swap in a fake ``smbus2`` with a real ``SMBus``
    class, import the genuine driver, then restore the previous ``sys.modules``
    entries so importing this module cannot disturb other tests.
    """
    saved_smbus2 = sys.modules.get("smbus2")
    saved_ads1115 = sys.modules.get("ads1115")

    fake_smbus2 = types.ModuleType("smbus2")
    fake_smbus2.SMBus = FakeSMBus
    sys.modules["smbus2"] = fake_smbus2
    sys.modules.pop("ads1115", None)
    try:
        module = importlib.import_module("ads1115")
    finally:
        if saved_smbus2 is not None:
            sys.modules["smbus2"] = saved_smbus2
        else:
            sys.modules.pop("smbus2", None)
        if saved_ads1115 is not None:
            sys.modules["ads1115"] = saved_ads1115
        else:
            sys.modules.pop("ads1115", None)
    return module


ads1115 = _load_driver()


def _make_adc(read_bytes):
    FakeSMBus.next_read = read_bytes
    return ads1115.ADS1115(i2c_address=0x48, i2c_bus=1)


class TestConfigWord:
    def test_channel_selects_correct_single_ended_mux(self):
        for channel, mux in ((0, 0x4000), (1, 0x5000), (2, 0x6000), (3, 0x7000)):
            adc = _make_adc([0x00, 0x00])
            adc.read_channel_mv(channel)
            address, register, data = adc.bus.writes[-1]
            assert address == 0x48
            assert register == ads1115._REG_POINTER_CONFIG
            config = (data[0] << 8) | data[1]
            # Start-single-shot bit set, correct MUX, single-shot mode, comparator off.
            assert config & 0x8000
            assert config & 0x7000 == mux
            assert config & 0x0100
            assert config & 0x0003 == 0x0003

    def test_out_of_range_channel_returns_none_without_io(self):
        adc = _make_adc([0x7F, 0xFF])
        assert adc.read_channel_mv(4) is None
        assert adc.read_channel_mv(-1) is None
        assert adc.bus.writes == []


class TestConversion:
    def test_zero_reads_as_zero_mv(self):
        adc = _make_adc([0x00, 0x00])
        assert adc.read_channel_mv(0) == 0.0

    def test_full_scale_positive(self):
        # 0x7FFF is the largest positive code -> just under +6.144 V.
        adc = _make_adc([0x7F, 0xFF])
        value = adc.read_channel_mv(0)
        assert abs(value - 6144.0 * 0x7FFF / 32768.0) < 1e-6

    def test_negative_code_is_signed(self):
        # 0xFFFF is -1 LSB.
        adc = _make_adc([0xFF, 0xFF])
        value = adc.read_channel_mv(0)
        assert value < 0
        assert abs(value - (-6144.0 / 32768.0)) < 1e-6

    def test_known_midscale_value(self):
        # 0x4000 = 16384 -> half of full scale = +3.072 V = 3072 mV.
        adc = _make_adc([0x40, 0x00])
        assert abs(adc.read_channel_mv(0) - 3072.0) < 1e-6


class TestIOErrors:
    def test_write_ioerror_returns_none(self):
        adc = _make_adc([0x00, 0x00])

        def _raise(*_a, **_k):
            raise OSError("bus error")

        adc.bus.write_i2c_block_data = _raise
        assert adc.read_channel_mv(0) is None

    def test_read_ioerror_returns_none(self):
        adc = _make_adc([0x00, 0x00])

        def _raise(*_a, **_k):
            raise OSError("bus error")

        adc.bus.read_i2c_block_data = _raise
        assert adc.read_channel_mv(0) is None
