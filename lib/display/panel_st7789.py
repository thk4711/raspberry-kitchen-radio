"""panel_st7789.py — ST7789 240x280 SPI panel driver.

Inherits all generic SPI/pixel/frame methods from ``PanelBase``; only the
ST7789-specific parts live here:

* ``Init()``       — ST7789 vendor power-on command sequence.
* ``SetWindows()`` — GRAM address window with the panel's +20 px Y offset
                     (the 240x280 visible area starts at GRAM row 20).

The ``width`` / ``height`` class attributes (240x280) are consumed by the
inherited ``ShowFullFrame``, ``ShowImage``, ``ShowWindow``, and ``clear``
methods in ``PanelBase``.
"""

import time

from .panel_base import PanelBase


class ST7789(PanelBase):
    """ST7789 1.69\" (240x280) SPI display driver."""

    width = 240
    height = 280
    # ST7789 portrait MADCTL. Matches the value written in ``Init()`` below, so
    # the per-frame re-assert in ``PanelBase`` is a no-op that keeps the panel
    # correct. Explicit here so the driver documents its own orientation byte.
    madctl = 0x00

    def Init(self):
        """Initialise the ST7789 panel (power-on command sequence)."""

        with self._io_lock:
            self.module_init()
            self.reset()

            self.command(0x36)
            self.data(self.madctl)

            self.command(0x3A)
            self.data(0x05)

            self.command(0xB2)
            self.data(0x0B)
            self.data(0x0B)
            self.data(0x00)
            self.data(0x33)
            self.data(0x35)

            self.command(0xB7)
            self.data(0x11)

            self.command(0xBB)
            self.data(0x35)

            self.command(0xC0)
            self.data(0x2C)

            self.command(0xC2)
            self.data(0x01)

            self.command(0xC3)
            self.data(0x0D)

            self.command(0xC4)
            self.data(0x20) # VDV, 0x20: 0V

            self.command(0xC6)
            self.data(0x13) # 0x13: 60Hz

            self.command(0xD0)
            self.data(0xA4)
            self.data(0xA1)

            self.command(0xD6)
            self.data(0xA1)

            self.command(0xE0)
            self.data(0xF0)
            self.data(0x06)
            self.data(0x0B)
            self.data(0x0A)
            self.data(0x09)
            self.data(0x26)
            self.data(0x29)
            self.data(0x33)
            self.data(0x41)
            self.data(0x18)
            self.data(0x16)
            self.data(0x15)
            self.data(0x29)
            self.data(0x2D)

            self.command(0xE1)
            self.data(0xF0)
            self.data(0x04)
            self.data(0x08)
            self.data(0x08)
            self.data(0x07)
            self.data(0x03)
            self.data(0x28)
            self.data(0x32)
            self.data(0x40)
            self.data(0x3B)
            self.data(0x19)
            self.data(0x18)
            self.data(0x2A)
            self.data(0x2E)

            self.command(0xE4)
            self.data(0x25)
            self.data(0x00)
            self.data(0x00)

            self.command(0x21)

            self.command(0x11)

            time.sleep(0.1)

            self.command(0x29)

    def SetWindows(self, Xstart, Ystart, Xend, Yend, horizontal=0):
        """Set the GRAM address window.

        The ST7789 240x280 panel's visible area starts at GRAM row 20, so a
        +20 px Y offset is added here.  Callers always use coordinates in the
        clean 0-based ``width x height`` space and never see the offset.
        """
        if horizontal:
            # Landscape: X axis carries the +20 offset
            self.command(0x2A)
            self.data(Xstart + 20 >> 8)
            self.data(Xstart + 20 & 0xFF)
            self.data(Xend + 20 - 1 >> 8)
            self.data((Xend + 20 - 1) & 0xFF)
            self.command(0x2B)
            self.data(Ystart >> 8)
            self.data(Ystart & 0xFF)
            self.data(Yend - 1 >> 8)
            self.data((Yend - 1) & 0xFF)
            self.command(0x2C)
        else:
            # Portrait: Y axis carries the +20 offset
            self.command(0x2A)
            self.data(Xstart >> 8)
            self.data(Xstart & 0xFF)
            self.data(Xend - 1 >> 8)
            self.data((Xend - 1) & 0xFF)
            self.command(0x2B)
            self.data(Ystart + 20 >> 8)
            self.data((Ystart + 20) & 0xFF)
            self.data(Yend + 20 - 1 >> 8)
            self.data((Yend + 20 - 1) & 0xFF)
            self.command(0x2C)

