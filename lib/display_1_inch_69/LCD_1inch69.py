
import threading
import time

from . import lcdconfig


class LCD_1inch69(lcdconfig.RaspberryPi):
    width = 240
    height = 280

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The ST7789 command stream is stateful: a display update is a sequence
        # of GPIO DC toggles, commands, window coordinates, and pixel data. The
        # radio app updates partial windows from background threads, so protect
        # each complete display transaction from interleaving with another one.
        self._io_lock = threading.RLock()

    def command(self, cmd):
        self.digital_write(self.DC_PIN, False)
        self.spi_writebyte([cmd])

    def data(self, val):
        self.digital_write(self.DC_PIN, True)
        self.spi_writebyte([val])

    def reset(self):
        """Reset the display"""
        self.digital_write(self.RST_PIN,True)
        time.sleep(0.01)
        self.digital_write(self.RST_PIN,False)
        time.sleep(0.01)
        self.digital_write(self.RST_PIN,True)
        time.sleep(0.01)

    def Init(self):
        """Initialize dispaly"""
        with self._io_lock:
            self.module_init()
            self.reset()

            self.command(0x36)
            self.data(0x00)

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

    def SetWindows(self, Xstart, Ystart, Xend, Yend, horizontal = 0):
        if horizontal:
            #set the X coordinates
            self.command(0x2A)
            self.data(Xstart+20>>8)         #Set the horizontal starting point to the high octet
            self.data(Xstart+20 & 0xff)     #Set the horizontal starting point to the low octet
            self.data(Xend+20-1>>8)         #Set the horizontal end to the high octet
            self.data((Xend+20-1) & 0xff)   #Set the horizontal end to the low octet
            #set the Y coordinates
            self.command(0x2B)
            self.data(Ystart>>8)
            self.data((Ystart & 0xff))
            self.data(Yend-1>>8)
            self.data((Yend-1) & 0xff)
            self.command(0x2C)
        else:
            #set the X coordinates
            self.command(0x2A)
            self.data(Xstart>>8)        #Set the horizontal starting point to the high octet
            self.data(Xstart & 0xff)    #Set the horizontal starting point to the low octet
            self.data(Xend-1>>8)        #Set the horizontal end to the high octet
            self.data((Xend-1) & 0xff)  #Set the horizontal end to the low octet
            #set the Y coordinates
            self.command(0x2B)
            self.data(Ystart+20>>8)
            self.data((Ystart+20 & 0xff))
            self.data(Yend+20-1>>8)
            self.data((Yend+20-1) & 0xff)
            self.command(0x2C)


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

    def ShowWindow(self, Image, Xstart=0, Ystart=0):
        """Set buffer to value of Python Imaging Library image."""
        """Write display buffer to physical display"""
        imwidth, imheight = Image.size
        Xend = Xstart + imwidth
        Yend = Ystart + imheight

        pix = self._encode_rgb565(Image, imwidth, imheight)

        with self._io_lock:
            self.command(0x36)
            self.data(0x00)
            self.SetWindows(Xstart, Ystart, Xend, Yend, 0)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(pix)

    def ShowFullFrame(self, pix):
        """Write a full 240x280 portrait frame from a packed RGB565 buffer.

        This is the single write path used by the full-frame compositor
        (``display_control.DisplayController``). The caller packs the composed
        frame to big-endian RGB565 with numpy (GIL released) and passes the
        resulting ``bytes``/buffer here, so the frame is encoded exactly once.

        The portrait ``SetWindows`` (``horizontal=0``) applies the panel's
        +20px GRAM Y-offset, so callers compose in a clean ``width x height``
        (240x280) space and never deal with the offset themselves.

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
            self.data(0x00)
            self.SetWindows(0, 0, self.width, self.height, 0)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(pix)

    def ShowImage(self, Image):
        """Set buffer to value of Python Imaging Library image."""
        """Write display buffer to physical display"""
        imwidth, imheight = Image.size
        with self._io_lock:
            if imwidth == self.height and imheight == self.width:
                # Landscape screen
                pix = self._encode_rgb565(Image, self.height, self.width)
                self.command(0x36)
                self.data(0x70)
                self.SetWindows(0, 0, self.height, self.width, 1)
            else:
                # Portrait screen
                pix = self._encode_rgb565(Image, imwidth, imheight)
                self.command(0x36)
                self.data(0x00)
                self.SetWindows(0, 0, self.width, self.height, 0)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(pix)

    def clear(self):
        """Clear contents of image buffer"""
        _buffer = bytes([0xff]) * (self.width * self.height * 2)
        with self._io_lock:
            self.SetWindows(0, 0, self.width, self.height)
            self.digital_write(self.DC_PIN, True)
            self.spi_writebytes2(_buffer)
