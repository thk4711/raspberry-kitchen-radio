"""panel_gc9a01.py — GC9A01 240x240 round SPI panel driver.

Inherits all generic SPI/pixel/frame methods from ``PanelBase``; only the
GC9A01-specific parts live here:

* ``Init()``       — GC9A01 vendor power-on command sequence (based on the
                     Waveshare 1.28" GC9A01 reference, transcribed into the
                     vendored ``command()`` / ``data()`` style used by the
                     ST7789 driver). MADCTL / COLMOD select RGB565.
* ``SetWindows()`` — GRAM address window with a **plain 0,0 origin (NO +20 px
                     offset)**. This is the key difference from the ST7789
                     driver, whose 240x280 visible area starts at GRAM row 20.

The ``width`` / ``height`` class attributes (240x240) are consumed by the
inherited ``ShowFullFrame``, ``ShowImage``, ``ShowWindow``, and ``clear``
methods in ``PanelBase``.

Hardware note: the exact init bytes, the MADCTL orientation byte, and the
colour order can only be fully verified on real hardware. If the round panel
comes up mirrored/rotated or with swapped colours, adjust the MADCTL value
(command ``0x36``) below — that is the single on-device tuning point.
"""

import time

from .panel_base import PanelBase


class GC9A01(PanelBase):
    """GC9A01 1.28\" (240x240) round SPI display driver."""

    width = 240
    height = 240

    def Init(self):
        """Initialise the GC9A01 panel (power-on command sequence)."""

        with self._io_lock:
            self.module_init()
            self.reset()

            # --- Inter-register enable / vendor-specific unlock ---
            self.command(0xEF)

            self.command(0xEB)
            self.data(0x14)

            self.command(0xFE)
            self.command(0xEF)

            self.command(0xEB)
            self.data(0x14)

            self.command(0x84)
            self.data(0x40)

            self.command(0x85)
            self.data(0xFF)

            self.command(0x86)
            self.data(0xFF)

            self.command(0x87)
            self.data(0xFF)

            self.command(0x88)
            self.data(0x0A)

            self.command(0x89)
            self.data(0x21)

            self.command(0x8A)
            self.data(0x00)

            self.command(0x8B)
            self.data(0x80)

            self.command(0x8C)
            self.data(0x01)

            self.command(0x8D)
            self.data(0x01)

            self.command(0x8E)
            self.data(0xFF)

            self.command(0x8F)
            self.data(0xFF)

            # --- Display function control ---
            self.command(0xB6)
            self.data(0x00)
            self.data(0x20)

            # MADCTL — memory access / orientation. 0x08 keeps the BGR bit
            # clear (RGB order) with the default scan direction. Flip this
            # byte on-device if the round panel is mirrored/rotated.
            self.command(0x36)
            self.data(0x08)

            # COLMOD — pixel format: 0x05 = 16 bits/pixel (RGB565).
            self.command(0x3A)
            self.data(0x05)

            self.command(0x90)
            self.data(0x08)
            self.data(0x08)
            self.data(0x08)
            self.data(0x08)

            self.command(0xBD)
            self.data(0x06)

            self.command(0xBC)
            self.data(0x00)

            self.command(0xFF)
            self.data(0x60)
            self.data(0x01)
            self.data(0x04)

            # --- Power control ---
            self.command(0xC3)
            self.data(0x13)
            self.command(0xC4)
            self.data(0x13)

            self.command(0xC9)
            self.data(0x22)

            self.command(0xBE)
            self.data(0x11)

            self.command(0xE1)
            self.data(0x10)
            self.data(0x0E)

            self.command(0xDF)
            self.data(0x21)
            self.data(0x0C)
            self.data(0x02)
            self._init_gamma_and_on()

    def _init_gamma_and_on(self):
        """Second half of ``Init()``: gamma curves, GIP timing, display-on.

        Split out of ``Init()`` purely to keep each method a manageable size;
        it must be called with ``self._io_lock`` already held (from ``Init``).
        """
        # --- Gamma ---
        self.command(0xF0)
        self.data(0x45)
        self.data(0x09)
        self.data(0x08)
        self.data(0x08)
        self.data(0x26)
        self.data(0x2A)

        self.command(0xF1)
        self.data(0x43)
        self.data(0x70)
        self.data(0x72)
        self.data(0x36)
        self.data(0x37)
        self.data(0x6F)

        self.command(0xF2)
        self.data(0x45)
        self.data(0x09)
        self.data(0x08)
        self.data(0x08)
        self.data(0x26)
        self.data(0x2A)

        self.command(0xF3)
        self.data(0x43)
        self.data(0x70)
        self.data(0x72)
        self.data(0x36)
        self.data(0x37)
        self.data(0x6F)

        self.command(0xED)
        self.data(0x1B)
        self.data(0x0B)

        self.command(0xAE)
        self.data(0x77)

        self.command(0xCD)
        self.data(0x63)

        # --- GIP / source timing ---
        self.command(0x70)
        self.data(0x07)
        self.data(0x07)
        self.data(0x04)
        self.data(0x0E)
        self.data(0x0F)
        self.data(0x09)
        self.data(0x07)
        self.data(0x08)
        self.data(0x03)

        self.command(0xE8)
        self.data(0x34)

        self.command(0x62)
        self.data(0x18)
        self.data(0x0D)
        self.data(0x71)
        self.data(0xED)
        self.data(0x70)
        self.data(0x70)
        self.data(0x18)
        self.data(0x0F)
        self.data(0x71)
        self.data(0xEF)
        self.data(0x70)
        self.data(0x70)

        self.command(0x63)
        self.data(0x18)
        self.data(0x11)
        self.data(0x71)
        self.data(0xF1)
        self.data(0x70)
        self.data(0x70)
        self.data(0x18)
        self.data(0x13)
        self.data(0x71)
        self.data(0xF3)
        self.data(0x70)
        self.data(0x70)

        self.command(0x64)
        self.data(0x28)
        self.data(0x29)
        self.data(0xF1)
        self.data(0x01)
        self.data(0xF1)
        self.data(0x00)
        self.data(0x07)

        self.command(0x66)
        self.data(0x3C)
        self.data(0x00)
        self.data(0xCD)
        self.data(0x67)
        self.data(0x45)
        self.data(0x45)
        self.data(0x10)
        self.data(0x00)
        self.data(0x00)
        self.data(0x00)

        self.command(0x67)
        self.data(0x00)
        self.data(0x3C)
        self.data(0x00)
        self.data(0x00)
        self.data(0x00)
        self.data(0x01)
        self.data(0x54)
        self.data(0x10)
        self.data(0x32)
        self.data(0x98)

        self.command(0x74)
        self.data(0x10)
        self.data(0x85)
        self.data(0x80)
        self.data(0x00)
        self.data(0x00)
        self.data(0x4E)
        self.data(0x00)

        self.command(0x98)
        self.data(0x3E)
        self.data(0x07)

        self.command(0x35)
        self.command(0x21)

        self.command(0x11)  # sleep-out
        time.sleep(0.12)
        self.command(0x29)  # display-on
        time.sleep(0.02)

    def SetWindows(self, Xstart, Ystart, Xend, Yend, horizontal=0):
        """Set the GRAM address window.

        Unlike the ST7789 240x280 panel, the GC9A01 maps its 240x240 GRAM
        one-to-one with the visible area, so there is **no +20 px offset**.
        The panel is square, so the ``horizontal`` flag (kept for signature
        compatibility with ``PanelBase`` callers) does not change the window.
        """
        self.command(0x2A)
        self.data(Xstart >> 8)
        self.data(Xstart & 0xFF)
        self.data((Xend - 1) >> 8)
        self.data((Xend - 1) & 0xFF)
        self.command(0x2B)
        self.data(Ystart >> 8)
        self.data(Ystart & 0xFF)
        self.data((Yend - 1) >> 8)
        self.data((Yend - 1) & 0xFF)
        self.command(0x2C)
