"""Off-target tests for the ST7789 240x280 SPI panel driver."""

import pytest
from display import lcdconfig
from display.panel_st7789 import ST7789


class _FakeSpi:
    def __init__(self):
        self.writes = []
        self.max_speed_hz = 0
        self.mode = 0

    def writebytes(self, data):
        self.writes.append(list(data))

    def writebytes2(self, data):
        self.writes.append(bytes(data))

    def close(self):
        pass


class _FakePin:
    def __init__(self, *args, **kwargs):
        self.value = 0

    def on(self):
        self.value = 1

    def off(self):
        self.value = 0

    def close(self):
        pass


@pytest.fixture
def panel(monkeypatch):
    monkeypatch.setattr(lcdconfig, "DigitalOutputDevice", _FakePin, raising=False)
    monkeypatch.setattr(lcdconfig, "DigitalInputDevice", _FakePin, raising=False)
    monkeypatch.setattr(lcdconfig, "PWMOutputDevice", _FakePin, raising=False)
    spi = _FakeSpi()
    display = ST7789(spi=spi, rst=24, dc=25, bl=12, spi_freq=40_000_000)
    display._spi = spi
    return display


def _stream(panel):
    return [byte for write in panel._spi.writes if isinstance(write, list) for byte in write]


def test_geometry_and_madctl(panel):
    assert (panel.width, panel.height, panel.madctl) == (240, 280, 0x00)


def test_full_frame_size_and_write(panel):
    frame = bytes(panel.width * panel.height * 2)
    panel.ShowFullFrame(frame)
    assert frame in panel._spi.writes
    with pytest.raises(ValueError):
        panel.ShowFullFrame(frame[:-1])


def test_portrait_window_applies_twenty_row_offset(panel):
    panel.SetWindows(0, 0, 240, 280)
    stream = _stream(panel)
    assert stream[:5] == [0x2A, 0, 0, 0, 239]
    assert stream[5:10] == [0x2B, 0, 20, 1, 43]
    assert stream[10] == 0x2C


def test_landscape_window_applies_twenty_column_offset(panel):
    panel.SetWindows(0, 0, 280, 240, horizontal=1)
    stream = _stream(panel)
    assert stream[:5] == [0x2A, 0, 20, 1, 43]
    assert stream[5:10] == [0x2B, 0, 0, 0, 239]
    assert stream[10] == 0x2C


def test_init_runs_expected_terminal_sequence(panel, monkeypatch):
    monkeypatch.setattr(
        "display.panel_st7789.time.sleep", mock_sleep := __import__("unittest").mock.Mock()
    )
    panel.Init()
    stream = _stream(panel)
    assert stream[:2] == [0x36, panel.madctl]
    assert stream[-2:] == [0x11, 0x29]
    mock_sleep.assert_called_with(0.1)
