"""panel_base.py — geometry-generic base class for SPI panel drivers.

``PanelBase`` extends the hardware abstraction layer (``lcdconfig.RaspberryPi``)
with all the display methods that depend only on ``self.width``, ``self.height``,
and the subclass-supplied ``SetWindows`` / ``Init`` implementations.

Concrete panel classes (``ST7789``, ``GC9A01``, …) inherit from ``PanelBase``
and provide:

* ``width`` / ``height`` class attributes
* ``Init()`` — panel-specific power-on command sequence
* ``SetWindows(Xstart, Ystart, Xend, Yend, horizontal=0)`` — GRAM address window
  (may apply a panel-specific GRAM offset)

Everything else — locking, RGB565 encoding, full-frame writes, PIL-image writes,
and clear — is inherited from here unchanged.
"""

import threading
import time

from . import lcdconfig


class PanelBase(lcdconfig.RaspberryPi):
    """Geometry-generic SPI panel methods, shared across all panel drivers.

    Subclasses must define:
        width  (int) — panel width in pixels
        height (int) — panel height in pixels
        Init() — vendor init sequence
        SetWindows(Xstart, Ystart, Xend, Yend, horizontal=0)
    """

    # Subclasses override these:
    width: int
    height: int

    # MADCTL (register 0x36) memory-access / colour-order byte. Every frame
    # write re-asserts MADCTL so a rare transient SPI glitch self-heals, but
    # the *value* is panel-specific: the ST7789 needs 0x00 while the GC9A01
    # needs 0x08 (its BGR/scan configuration). Re-sending a hard-coded 0x00
    # used to clobber the GC9A01's vendor-init value, mixing up the colour
    # order of station logos. Subclasses override ``madctl`` to match their
    # own ``Init()`` so the per-frame re-assert stays correct.
    madctl: int = 0x00
    # Landscape MADCTL used only by ``ShowImage`` when it detects a rotated
    # (height x width) image. Rectangular panels flip row/column exchange here.
    madctl_landscape: int = 0x70

    def Init(self):
        """Run the concrete panel's power-on command sequence."""
        raise NotImplementedError

    def SetWindows(self, Xstart, Ystart, Xend, Yend, horizontal=0):
        """Select a concrete panel's GRAM address window."""
        raise NotImplementedError

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The SPI command stream is stateful: a display update is a sequence
        # of GPIO DC toggles, commands, window coordinates, and pixel data.
        # The radio app updates partial windows from background threads, so
        # protect each complete display transaction from interleaving.
        self._io_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Low-level command helpers (called by Init / SetWindows in subclasses)
    # ------------------------------------------------------------------

    def command(self, cmd):
        self.digital_write(self.DC_PIN, False)
        self.spi_writebyte([cmd])

    def data(self, val):
        self.digital_write(self.DC_PIN, True)
        self.spi_writebyte([val])

    def reset(self):
        """Hardware reset pulse on the RST line."""
        self.digital_write(self.RST_PIN, True)
        time.sleep(0.01)
        self.digital_write(self.RST_PIN, False)
        time.sleep(0.01)
        self.digital_write(self.RST_PIN, True)
        time.sleep(0.01)

    # ------------------------------------------------------------------
    # Pixel encoding
    # ------------------------------------------------------------------

    def _encode_rgb565(self, Image, imwidth, imheight):
        """Convert a PIL image to a packed RGB565 big-endian byte buffer.

        Returns a ``bytes`` object suitable for ``writebytes2`` (no per-byte
        Python int boxing, no manual chunking).
        """
        img = self.np.asarray(Image)
        pix = self.np.zeros((imheight, imwidth, 2), dtype=self.np.uint8)
        # RGB888 >> RGB565
        pix[..., [0]] = self.np.add(
            self.np.bitwise_and(img[..., [0]], 0xF8),
            self.np.right_shift(img[..., [1]], 5),
        )
        pix[..., [1]] = self.np.add(
            self.np.bitwise_and(self.np.left_shift(img[..., [1]], 3), 0xE0),
            self.np.right_shift(img[..., [2]], 3),
        )
        return pix.astype(self.np.uint8).tobytes()

    # ------------------------------------------------------------------
    # Frame-write methods
    # ------------------------------------------------------------------

    def ShowWindow(self, Image, Xstart=0, Ystart=0):
        """Write a sub-region PIL image to the panel at (Xstart, Ystart)."""
        imwidth, imheight = Image.size
        Xend = Xstart + imwidth
        Yend = Ystart + imheight
        pix = self._encode_rgb565(Image, imwidth, imheight)
        with self._io_lock:
            self.command(0x36)
            self.data(self.madctl)
            self.SetWindows(Xstart, Ystart, Xend, Yend, 0)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(pix)

    def ShowFullFrame(self, pix):
        """Write a full ``width x height`` portrait frame from a packed RGB565 buffer.

        This is the single write path used by the full-frame compositor
        (``display_control.DisplayController``). The caller packs the composed
        frame to big-endian RGB565 with numpy (GIL released) and passes the
        resulting ``bytes``/buffer here, so the frame is encoded exactly once.

        The portrait ``SetWindows`` (``horizontal=0``) applies any panel-specific
        GRAM offset internally, so callers compose in a clean ``width x height``
        space and never deal with the offset themselves.

        Args:
            pix: A bytes-like RGB565 buffer of length ``width * height * 2``
                (big-endian), e.g. from ``compositor.pack_rgb565``.
        """
        expected = self.width * self.height * 2
        if len(pix) != expected:
            raise ValueError(
                f"full-frame buffer must be {expected} bytes "
                f"({self.width}x{self.height} RGB565), got {len(pix)}"
            )
        with self._io_lock:
            self.command(0x36)
            self.data(self.madctl)
            self.SetWindows(0, 0, self.width, self.height, 0)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(pix)

    def ShowImage(self, Image):
        """Write a full PIL image to the panel (portrait or landscape)."""
        imwidth, imheight = Image.size
        with self._io_lock:
            if imwidth == self.height and imheight == self.width:
                # Landscape orientation
                pix = self._encode_rgb565(Image, self.height, self.width)
                self.command(0x36)
                self.data(self.madctl_landscape)
                self.SetWindows(0, 0, self.height, self.width, 1)
            else:
                # Portrait orientation
                pix = self._encode_rgb565(Image, imwidth, imheight)
                self.command(0x36)
                self.data(self.madctl)
                self.SetWindows(0, 0, self.width, self.height, 0)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(pix)

    def clear(self):
        """Fill the entire panel with white (0xFF bytes)."""
        _buffer = bytes([0xFF]) * (self.width * self.height * 2)
        with self._io_lock:
            self.SetWindows(0, 0, self.width, self.height)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(_buffer)
