"""panel_factory.py — Panel-class selector for the Kitchen Radio display.

Maps the ``[display] panel`` config key to the correct driver class so the
three call sites (``display_control``, ``boot_splash``, ``display_test``) do
not need to know which panels exist.

Usage::

    from display import panel_factory
    PanelClass = panel_factory.get_panel_class("gc9a01")
    disp = PanelClass(rst=24, dc=25, bl=12, spi_bus=0, spi_device=0, spi_freq=40_000_000)

Supported names (case-insensitive):

* ``"st7789"``  — 240×280 rectangular panel (default)
* ``"gc9a01"``  — 240×240 round panel

Any unknown or blank name falls back silently to ``ST7789`` after logging a
warning, so a misconfigured ``display.conf`` never prevents boot.
"""
import logging

from .panel_gc9a01 import GC9A01
from .panel_st7789 import ST7789

logger = logging.getLogger(__name__)

# Registry of all supported panel names.  Keys are lower-cased.
_PANELS: dict[str, type] = {
    "st7789": ST7789,
    "gc9a01": GC9A01,
}

_DEFAULT_PANEL = ST7789


def get_panel_class(name: str) -> type:
    """Return the panel driver class for *name*.

    Args:
        name: The ``[display] panel`` value from ``display.conf`` (e.g.
            ``"st7789"`` or ``"gc9a01"``).  Leading/trailing whitespace is
            stripped; comparison is case-insensitive.

    Returns:
        The driver class (``ST7789`` or ``GC9A01``).  Falls back to
        ``ST7789`` and logs a ``WARNING`` for any unknown or blank value so
        a misconfigured entry never prevents boot.
    """
    normalised = (name or "").strip().lower()
    cls = _PANELS.get(normalised)
    if cls is None:
        logger.warning(
            "Unknown display panel %r — falling back to ST7789 (240x280). "
            "Supported values: %s",
            name,
            ", ".join(sorted(_PANELS)),
        )
        return _DEFAULT_PANEL
    return cls
