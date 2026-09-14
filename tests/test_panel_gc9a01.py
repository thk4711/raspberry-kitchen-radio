"""Off-target tests for the GC9A01 240x240 round panel driver.

The GC9A01 driver (``lib/display/panel_gc9a01.py``) is the second selectable
panel alongside the ST7789. These tests exercise it *without* a Raspberry Pi by
constructing a real ``GC9A01`` and feeding it the collaborators the conftest
stubs deliberately leave empty:

* a fake ``spi`` object is passed to the constructor (``RaspberryPi.__init__``
  skips ``spidev.SpiDev`` when ``spi`` is not ``None``); it records the byte
  stream so we can assert on the command/data sequence, and
* the ``gpiozero`` device classes (imported into ``lcdconfig`` via a wildcard
  import) are monkeypatched with tiny fakes.

Covered invariants (Step 7 of the GC9A01 plan):

* geometry is 240x240;
* ``ShowFullFrame`` accepts a ``240*240*2``-byte buffer and rejects wrong sizes
  (inherited from ``PanelBase``);
* ``SetWindows`` emits a plain 0,0 origin with **no +20 px offset** (the key
  difference from the ST7789 driver); and
* ``Init()`` runs to completion against the stubbed SPI/GPIO and issues the
  sleep-out (0x11) + display-on (0x29) commands.
"""
import pytest
from display import lcdconfig
from display.panel_gc9a01 import GC9A01


class _FakeSpi:
    """Records the byte stream written by the driver's command/data helpers."""

    def __init__(self):
        self.max_speed_hz = 0
        self.mode = 0
        self.writes = []

    def writebytes(self, data):
        self.writes.append(list(data))

    def writebytes2(self, data):
        self.writes.append(bytes(data))

    def close(self):
        pass


class _FakePin:
    """Minimal stand-in for a gpiozero DigitalOutputDevice / PWMOutputDevice."""

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
    """Construct a real ``GC9A01`` wired to recording SPI/GPIO fakes."""
    # ``lcdconfig`` does ``from gpiozero import *``; the conftest stub leaves
    # those names undefined, so patch them onto the lcdconfig namespace.
    monkeypatch.setattr(lcdconfig, "DigitalOutputDevice", _FakePin, raising=False)
    monkeypatch.setattr(lcdconfig, "DigitalInputDevice", _FakePin, raising=False)
    monkeypatch.setattr(lcdconfig, "PWMOutputDevice", _FakePin, raising=False)

    spi = _FakeSpi()
    # Passing spi= bypasses spidev.SpiDev entirely.
    disp = GC9A01(spi=spi, rst=24, dc=25, bl=12, spi_freq=40_000_000)
    disp._spi = spi  # convenience handle for assertions
    return disp


def test_geometry_is_240_square(panel):
    assert panel.width == 240
    assert panel.height == 240


def test_show_full_frame_accepts_exact_size(panel):
    pix = bytes(240 * 240 * 2)
    panel.ShowFullFrame(pix)  # must not raise
    # The exact frame buffer is the last (largest) write pushed to SPI.
    assert any(isinstance(w, bytes) and len(w) == 240 * 240 * 2
               for w in panel._spi.writes)


@pytest.mark.parametrize("size", [240 * 240 * 2 - 1, 240 * 240 * 2 + 1, 240 * 280 * 2])
def test_show_full_frame_rejects_wrong_size(panel, size):
    with pytest.raises(ValueError):
        panel.ShowFullFrame(bytes(size))


def _byte_stream(spi):
    """Flatten the per-byte command/data writes into one list of ints."""
    out = []
    for w in spi.writes:
        if isinstance(w, list):
            out.extend(w)
    return out


def test_setwindows_has_no_offset(panel):
    panel._spi.writes.clear()
    panel.SetWindows(0, 0, panel.width, panel.height)
    stream = _byte_stream(panel._spi)

    # Column address set (0x2A): Xstart=0 .. Xend-1=239 (0x00EF), no +20.
    assert stream[0:5] == [0x2A, 0x00, 0x00, 0x00, 0xEF]
    # Row address set (0x2B): Ystart=0 .. Yend-1=239 (0x00EF), no +20 either.
    assert stream[5:10] == [0x2B, 0x00, 0x00, 0x00, 0xEF]
    # Memory write (0x2C) follows.
    assert stream[10] == 0x2C
    # A +20-offset panel would encode Ystart as 20 (0x14); assert it does not.
    assert 0x14 not in stream[6:8]


def test_init_runs_and_turns_display_on(panel):
    panel._spi.writes.clear()
    panel.Init()  # must not raise against the stubbed SPI/GPIO
    stream = _byte_stream(panel._spi)
    # Sleep-out then display-on are issued near the end of the sequence.
    assert 0x11 in stream
    assert 0x29 in stream
    # COLMOD set to RGB565 (0x3A -> 0x05) somewhere in the init.
    idx = stream.index(0x3A)
    assert stream[idx + 1] == 0x05
