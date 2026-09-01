# theme.py
"""Pure theming layer for the now-playing display (Workstream 5).

The display's colours, safe-area geometry, font sizes, scrim/opacity, motion
timings and an overall ``animations`` on/off switch are collected here into a
single immutable :class:`Theme`. :func:`build_theme` turns the raw ``[ui]``
section of ``display.conf`` (as returned by ``UtilityLibrary.read_config`` — a
plain ``dict`` or ``None`` when the section is absent) into a fully-populated
``Theme``, coercing each value and **falling back to the built-in default on any
missing or invalid key**. Nothing here touches Pillow, numpy or hardware, so it
is unit-testable on any machine (consistent with ``layout.py`` /
``textformat.py``).

Design constraints honoured:

* **No new runtime dependencies** — standard library only.
* **Never raises / never blocks boot** — a malformed value is logged and the
  default is used, matching the appliance "a bad config never blocks boot"
  policy. Colours accept ``#RRGGBB``, ``r,g,b`` triples, or the named colours
  the UI actually uses (``WHITE`` / ``BLACK``).
* **Backward compatible** — with no ``[ui]`` section every field equals the
  historical module constant, so the rendered frame is byte-identical.

``art_mode`` is intentionally **not** part of the theme: it is decided per
source by ``radio.py`` (radio for MPD, cover for Spotify/AirPlay).
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, NamedTuple, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

Color = Tuple[int, int, int]

# Named colours the UI uses. Kept intentionally tiny — the theme is presentation
# tuning, not a full CSS colour set.
_NAMED_COLORS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}


class Theme(NamedTuple):
    """Immutable set of themeable display values.

    Field defaults are the historical ``display_control`` module constants, so
    ``Theme()`` reproduces the shipped look exactly.
    """

    # Safe-area / chrome geometry.
    safe_inset: int = 14
    top_band_height: int = 44
    bottom_band_height: int = 82
    scrim_opacity: float = 0.55
    backdrop_blur: int = 18
    # Radio-mode backdrop contrast (WS8). The full-bleed backdrop behind a
    # station logo is a vertical gradient derived from the logo's dominant
    # colour, softened with a blurred copy of the logo. To keep the (usually
    # bright) logo tile legible, the backdrop is deliberately *darker* than the
    # logo: the top row is ``dom * backdrop_top_scale`` and the bottom row is
    # ``dom * backdrop_bottom_scale``. ``backdrop_logo_blend`` is how much of the
    # blurred logo is mixed into that gradient (higher = the background looks
    # more like a big blurry copy of the tile, which lowers edge contrast).
    backdrop_top_scale: float = 0.55
    backdrop_bottom_scale: float = 0.20
    backdrop_logo_blend: float = 0.20

    # Typography sizes (px).
    title_size: int = 30
    artist_size: int = 22
    small_size: int = 17
    clock_size: int = 24
    clock_large_size: int = 64
    date_size: int = 20

    # Core colours.
    text_color: Color = (255, 255, 255)
    subtext_color: Color = (200, 200, 200)
    shadow_color: Color = (0, 0, 0)
    background_color: Color = (0, 0, 0)
    no_art_color: Color = (32, 32, 40)

    # Volume OSD (Workstream 4.2).
    osd_duration: float = 1.5
    osd_bar_height: int = 12
    osd_track_color: Color = (70, 70, 78)
    osd_fill_color: Color = (255, 255, 255)

    # Preset toast (Workstream 4.5).
    toast_duration: float = 1.6
    toast_bg_color: Color = (0, 0, 0)
    toast_opacity: float = 0.72
    toast_text_color: Color = (255, 255, 255)

    # Motion (Workstream 4.1 / 4.3). Zeroed when ``animations`` is off.
    crossfade_ms: int = 150
    edge_fade_px: int = 12

    # Idle clock screensaver (Workstream 4.4).
    idle_timeout: float = 30.0
    idle_bg_top: Color = (18, 18, 24)
    idle_bg_bottom: Color = (6, 6, 10)

    # Master motion switch. False forces ``crossfade_ms``/``edge_fade_px`` to 0.
    animations: bool = True


# The default instance, reused as the per-key fallback source.
_DEFAULT = Theme()


def parse_color(value: Any, default: Color) -> Color:
    """Coerce ``value`` to an ``(r, g, b)`` triple, or return ``default``.

    Accepts an existing ``(r, g, b)`` sequence, a ``"#RRGGBB"`` hex string, a
    ``"r, g, b"`` comma triple, or a known named colour (case-insensitive).
    Any unrecognised or out-of-range input falls back to ``default``.
    """
    if value is None:
        return default
    # Already a triple (e.g. a default passed through).
    if isinstance(value, (tuple, list)) and not isinstance(value, str):
        return _clamp_triple(value, default)
    text = str(value).strip()
    if not text:
        return default
    lowered = text.lower()
    if lowered in _NAMED_COLORS:
        return _NAMED_COLORS[lowered]
    if text.startswith("#"):
        hexpart = text[1:]
        if len(hexpart) == 6:
            try:
                r = int(hexpart[0:2], 16)
                g = int(hexpart[2:4], 16)
                b = int(hexpart[4:6], 16)
                return (r, g, b)
            except ValueError:
                logger.warning("theme: invalid hex colour %r; using default", value)
                return default
        logger.warning("theme: invalid hex colour %r; using default", value)
        return default
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) == 3:
            try:
                triple = tuple(int(p) for p in parts)
            except ValueError:
                logger.warning("theme: invalid colour triple %r; using default", value)
                return default
            return _clamp_triple(triple, default)
    logger.warning("theme: unrecognised colour %r; using default", value)
    return default


def _clamp_triple(value: Sequence[Any], default: Color) -> Color:
    """Return a 3-tuple of 0..255 ints, or ``default`` if not shaped right."""
    try:
        if len(value) != 3:
            return default
        return tuple(max(0, min(255, int(c))) for c in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return default


def parse_float(value: Any, default: float, lo: float, hi: float) -> float:
    """Coerce ``value`` to a float clamped to ``[lo, hi]``, else ``default``."""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        logger.warning("theme: invalid number %r; using default %s", value, default)
        return default
    return max(lo, min(hi, f))


def parse_int(value: Any, default: int, lo: int = 0, hi: int = 100000) -> int:
    """Coerce ``value`` to an int clamped to ``[lo, hi]``, else ``default``."""
    if value is None:
        return default
    try:
        i = int(value)
    except (TypeError, ValueError):
        logger.warning("theme: invalid integer %r; using default %s", value, default)
        return default
    return max(lo, min(hi, i))


def parse_bool(value: Any, default: bool) -> bool:
    """Coerce ``value`` to a bool.

    ``read_config`` yields real bools already; this also accepts the usual
    truthy/falsey strings so a hand-edited value never crashes the build.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    logger.warning("theme: invalid boolean %r; using default %s", value, default)
    return default


def build_theme(ui: Optional[Mapping[str, Any]]) -> Theme:
    """Build a :class:`Theme` from a raw ``[ui]`` config mapping.

    Args:
        ui: The ``[ui]`` section from ``read_config`` (a ``dict`` of raw string/
            int/bool values), or ``None`` when the section is absent.

    Returns:
        A fully-populated :class:`Theme`. Missing keys keep their default; a
        malformed value is logged and the default is used for that key. With no
        ``[ui]`` section the shipped ``Theme()`` default is returned unchanged.
    """
    if not ui:
        return _DEFAULT
    d = _DEFAULT

    animations = parse_bool(ui.get("animations"), d.animations)
    crossfade_ms = parse_int(ui.get("crossfade_ms"), d.crossfade_ms, 0, 5000)
    edge_fade_px = parse_int(ui.get("edge_fade_px"), d.edge_fade_px, 0, 120)
    # Master switch: turning animations off suppresses the motion effects while
    # leaving functional overlays (volume OSD, preset toast) intact.
    if not animations:
        crossfade_ms = 0
        edge_fade_px = 0

    return Theme(
        safe_inset=parse_int(ui.get("safe_inset"), d.safe_inset, 0, 120),
        top_band_height=parse_int(ui.get("top_band_height"), d.top_band_height, 1, 279),
        bottom_band_height=parse_int(ui.get("bottom_band_height"),
                                     d.bottom_band_height, 1, 279),
        scrim_opacity=parse_float(ui.get("scrim_opacity"), d.scrim_opacity, 0.0, 1.0),
        backdrop_blur=parse_int(ui.get("backdrop_blur"), d.backdrop_blur, 0, 100),
        backdrop_top_scale=parse_float(ui.get("backdrop_top_scale"),
                                       d.backdrop_top_scale, 0.0, 4.0),
        backdrop_bottom_scale=parse_float(ui.get("backdrop_bottom_scale"),
                                          d.backdrop_bottom_scale, 0.0, 4.0),
        backdrop_logo_blend=parse_float(ui.get("backdrop_logo_blend"),
                                        d.backdrop_logo_blend, 0.0, 1.0),
        title_size=parse_int(ui.get("title_size"), d.title_size, 6, 200),
        artist_size=parse_int(ui.get("artist_size"), d.artist_size, 6, 200),
        small_size=parse_int(ui.get("small_size"), d.small_size, 6, 200),
        clock_size=parse_int(ui.get("clock_size"), d.clock_size, 6, 200),
        clock_large_size=parse_int(ui.get("clock_large_size"), d.clock_large_size, 6, 240),
        date_size=parse_int(ui.get("date_size"), d.date_size, 6, 200),
        text_color=parse_color(ui.get("text_color"), d.text_color),
        subtext_color=parse_color(ui.get("subtext_color"), d.subtext_color),
        shadow_color=parse_color(ui.get("shadow_color"), d.shadow_color),
        background_color=parse_color(ui.get("background_color"), d.background_color),
        no_art_color=parse_color(ui.get("no_art_color"), d.no_art_color),
        osd_duration=parse_float(ui.get("osd_duration"), d.osd_duration, 0.0, 30.0),
        osd_bar_height=parse_int(ui.get("osd_bar_height"), d.osd_bar_height, 2, 80),
        osd_track_color=parse_color(ui.get("osd_track_color"), d.osd_track_color),
        osd_fill_color=parse_color(ui.get("osd_fill_color"), d.osd_fill_color),
        toast_duration=parse_float(ui.get("toast_duration"), d.toast_duration, 0.0, 30.0),
        toast_bg_color=parse_color(ui.get("toast_bg_color"), d.toast_bg_color),
        toast_opacity=parse_float(ui.get("toast_opacity"), d.toast_opacity, 0.0, 1.0),
        toast_text_color=parse_color(ui.get("toast_text_color"), d.toast_text_color),
        crossfade_ms=crossfade_ms,
        edge_fade_px=edge_fade_px,
        idle_timeout=parse_float(ui.get("idle_timeout"), d.idle_timeout, 0.0, 86400.0),
        idle_bg_top=parse_color(ui.get("idle_bg_top"), d.idle_bg_top),
        idle_bg_bottom=parse_color(ui.get("idle_bg_bottom"), d.idle_bg_bottom),
        animations=animations,
    )


