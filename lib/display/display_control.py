# display_control.py
"""SPI display controller for the Kitchen Radio now-playing UI.

Rendering architecture (Workstream 1 of the display redesign): a single
background thread composes **one full frame** from shared state and pushes it
to the panel with a single write. It transmits **only when the composed frame
changes or an animation (scrolling text) is active**; when idle it sleeps with
no SPI traffic. Pushes are throttled to ~20 fps.

The active panel is selected by the ``[display] panel`` key in
``display.conf``: ``st7789`` (default, 240×280) or ``gc9a01`` (240×240 round).
The factory in ``panel_factory`` maps the name to the right driver class so
this module never needs to know which panels exist.

Concurrency model (unchanged): exactly one thread (the compositor loop) writes
to the serial SPI panel; the radio/ADC/metadata threads only mutate shared
state guarded by ``self._state_lock`` and flip the dirty flag. The public API
consumed by ``radio.py`` — :meth:`DisplayController.update_metadata` and
:meth:`DisplayController.toggle_backlight` — is preserved.

The heavy pixel math (RGB565 packing, gradients, scrims, dominant colour)
lives in ``compositor.py`` as GIL-releasing numpy helpers; the safe-area
rectangle geometry lives in ``layout.py``. Workstream 2 layers the frame as
full-bleed art (real cover, or a dominant-colour radio backdrop with a crisp
centred logo) plus darkened top/bottom chrome bands that keep text legible.

The round GC9A01 panel drops those chrome bands entirely: the active source
(e.g. ``RADIO`` / ``SPOTIFY``) is shown as plain centred text at the very top
of the circle — there is no clock and no play/pause glyph — and the two
metadata rows sit close together near the bottom of the circle, all drawn
straight onto the artwork (no scrim), which frees the whole centre of the
circle for the station logo / cover art. The rectangular ST7789 layout is
unchanged.
"""
import logging
import math
import os
import threading
from datetime import datetime
from time import monotonic, sleep
from typing import Optional, Tuple

import numpy as np
from display import compositor, logo_fallback, panel_factory, textformat
from display import layout as layout_mod
from display import theme as theme_mod
from display.transient_state import TransientState
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from utilities import MANAGED_CONFIG_DIR, UtilityLibrary

utility = UtilityLibrary()
logger = logging.getLogger(__name__)

# Target frame cadence for pushes while animating (~20 fps). Idle frames never
# transmit at all, so this only bounds the busy (scrolling) case.
_FRAME_INTERVAL = 0.05
# Force a full self-healing repaint roughly this often (in composed frames) to
# repair a rare transient SPI/display glitch without every-frame full repaints.
_SELF_HEAL_FRAMES = 600

# All display constants (safe-area geometry, typography, colours, motion
# timings) live in ``theme.py`` as the shipped ``Theme`` defaults and are read
# from the resolved theme at construction time (``self.theme.*``).  There are
# no module-level constant duplicates here.


class DisplayController:
    """Owns the SPI panel and composes one full frame from shared state."""

    def __init__(self) -> None:
        """Initialise the display, layout, render buffers and update thread."""
        self.module_location = os.path.dirname(os.path.abspath(__file__))
        conf = utility.read_config_layered(
            f'{self.module_location}/display.conf',
            os.path.join(MANAGED_CONFIG_DIR, 'display.ini'),
        )

        # Resolve the [ui] theme (Workstream 5). Absent section -> shipped
        # defaults (byte-identical to the pre-theme look); malformed values fall
        # back per key and never block boot. Every draw site below reads these
        # instance attributes so the whole UI is themeable from one section.
        self.theme = theme_mod.build_theme(conf.get('ui'))
        t = self.theme

        # Display settings
        self.width = conf['display']['width']
        self.height = conf['display']['height']
        panel_name = conf['display'].get('panel', 'st7789')
        # Shape drives the whole layout/OSD/idle geometry. The round GC9A01 gets
        # a circle-aware layout (centred text, top-arc symbols, ring gauge);
        # every other panel keeps the rectangular ST7789 layout byte-identical.
        self.shape = "round" if str(panel_name).strip().lower() == "gc9a01" else "rect"
        PanelClass = panel_factory.get_panel_class(panel_name)
        self.disp = PanelClass(
            rst=conf['display']['rst'],
            dc=conf['display']['dc'],
            bl=conf['display']['bl'],
            spi_bus=conf['display'].get('spi_bus', 0),
            spi_device=conf['display'].get('spi_device', 0),
            spi_freq=conf['display'].get('spi_freq', 40000000)
        )
        self.disp.Init()
        self.disp.bl_DutyCycle(0)
        self.disp.clear()
        # Font hierarchy (Workstream 3): bold title, regular artist, small badge.
        # Sizes come from the resolved theme (Workstream 5).
        fonts_dir = f'{self.module_location}/fonts'
        self.font_title = self._load_font(f'{fonts_dir}/Roboto-Condensed-Bold.ttf',
                                          f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                          t.title_size)
        self.font_artist = self._load_font(f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                           f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                           t.artist_size)
        self.font_small = self._load_font(f'{fonts_dir}/Roboto-Condensed-Bold.ttf',
                                          f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                          t.small_size)
        # Dedicated clock font for the top status strip: a little larger than
        # font_small so the time reads clearly, without enlarging the source
        # badge or the volume OSD (which stay on font_small).
        self.font_clock_status = self._load_font(f'{fonts_dir}/Roboto-Condensed-Bold.ttf',
                                                 f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                                 t.clock_size)
        # Large fonts for the idle clock screensaver (Workstream 4.4).
        self.font_clock = self._load_font(f'{fonts_dir}/Roboto-Condensed-Bold.ttf',
                                          f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                          t.clock_large_size)
        self.font_date = self._load_font(f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                         f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                         t.date_size)
        self.background_color = t.background_color
        self.font_color = t.text_color
        # Neutral backdrop colour used when radio mode has no logo to sample.
        self.no_art_color = t.no_art_color
        self.is_on = False

        # Shared display state is mutated by the radio/ADC/metadata threads and
        # consumed by the single compositor thread. ``_dirty`` marks that the
        # composed frame content changed and must be re-pushed; scrolling rows
        # additionally keep the loop animating (see ``_any_scrolling``).
        self._state_lock = threading.RLock()
        self._dirty = True

        # Safe-area layout: a full-bleed art layer with darkened top/bottom
        # chrome bands, everything inset from the rounded corners. The two text
        # rows live in the bottom band; the top band holds the status strip
        # (source badge + clock + play/pause glyph).
        self.layout = layout_mod.compute_layout(
            self.width, self.height,
            inset=t.safe_inset, band_height=t.top_band_height,
            bottom_band_height=t.bottom_band_height,
            shape=self.shape,
        )
        # Bottom band stacks the primary (track) row above the secondary
        # (artist/station) row. Give the bold title the larger share.
        band = self.layout.bottom_band
        self.title_row_h = int(band.h * 0.55)
        self.artist_row_h = band.h - self.title_row_h
        self.title_y = band.y
        self.name_y = band.y + self.title_row_h
        # The centred radio-mode logo fits inside the safe area between the
        # bands (never under the chrome).
        self.logo_box = layout_mod.Rect(
            self.layout.safe.x,
            self.layout.top_band.bottom,
            self.layout.safe.w,
            self.layout.bottom_band.y - self.layout.top_band.bottom,
        )

        # Metadata for scrolling text. Both rows advance a single pixel per due
        # step (``speed`` == 1) for smooth motion; ``interval`` is the number of
        # composed-frame ticks between advances (title every tick, name every
        # other tick, so the title scrolls twice as fast). ``scrolling`` marks a
        # row whose text overflows its window and must keep animating.
        #
        # The two rows are populated from split_artist_title(): ``title`` holds
        # the primary (track) line and ``name`` the secondary (artist/station).
        # ``art_mode`` selects the art layer, ``state`` the play/pause flag, and
        # ``source`` the backend name for the status-strip badge.
        self.metadata = {
            "title": {"text": " ", "position": 0, "speed": 1, "interval": 1,
                      "direction": "left", "scrolling": False,
                      "y": self.title_y, "row_h": self.title_row_h,
                      "font": self.font_title, "color": self.font_color},
            "name": {"text": " ", "position": 0, "speed": 1, "interval": 2,
                     "direction": "left", "scrolling": False,
                     "y": self.name_y, "row_h": self.artist_row_h,
                     "font": self.font_artist, "color": t.subtext_color},
            "raw_name": "",
            "raw_title": "",
            "cover": "",
            "md5": "0",
            "art_mode": "radio",
            "state": False,
            "source": "",
        }
        # Last clock string pushed (HH:MM); a change flags the frame dirty so the
        # clock updates about once a minute without any other SPI traffic.
        self._last_clock = ""

        # Transient overlay state (Workstream 4). ``monotonic()`` deadlines in
        # the future keep the compositor loop animating and are re-checked in
        # ``_transient_active``; when a deadline lapses the loop repaints once
        # more (a "timed dirty") so the overlay clears itself with no lingering
        # SPI traffic. Mutated under ``_state_lock`` like all shared state.
        # All time-based overlays (volume OSD, preset toast, art crossfade and
        # the idle-activity timestamp) live in this dependency-free state
        # machine. DisplayController still owns ``_state_lock`` and calls into
        # it while holding the lock, so the concurrency model is unchanged.
        self._transient = TransientState(clock=lambda: monotonic())

        # Cache of the composed static art layer (background + logo/cover +
        # scrims). It only changes when the cover/md5/mode changes, so animating
        # (scrolling-text) frames just redraw text over a copy of this cached
        # layer instead of re-fitting art / recomputing the dominant colour.
        self._art_cache_key: Tuple[Optional[str], Optional[str], Optional[str]] = (None, None, None)
        self._art_layer: Optional[Image.Image] = None

        # Adaptive-shadow bookkeeping (round panel only). The round layout draws
        # white text straight onto the artwork (no scrim bars). To keep it
        # readable over a light background without changing the text colour, the
        # background luminance under each text zone is sampled once per art layer
        # (keyed by ``_art_cache_key``) and used to fade in a soft glyph outline.
        # ``None`` means "not sampled yet / dark art" and yields the historical
        # 1px drop shadow, so the shipped look is unchanged over dark art.
        self._top_bg_luma: Optional[float] = None
        self._bottom_bg_luma: Optional[float] = None


        # Reusable draw scratch for measuring text without allocating per call.
        self._measure_img = Image.new('RGB', (self.width, self.layout.bottom_band.h))
        self._measure_draw = ImageDraw.Draw(self._measure_img)

        # Signature of the last frame actually pushed to the panel, used to
        # suppress a redundant SPI transmit when a recompose yields an identical
        # frame (e.g. a metadata poll that changed nothing on screen).
        self._last_frame_sig: Optional[bytes] = None

        # Paint a branded boot splash so the panel shows the product identity
        # immediately instead of the clear() white until the first metadata
        # arrives (Workstream 4.6). It is a normal composed full frame, so it
        # counts as the single initial push.
        self._push_frame(self._render_splash(), force=True)

        # Start the single compositor/writer thread.
        self.update_text_thread = threading.Thread(target=self.update_text, daemon=True)
        self.update_text_thread.start()

    @staticmethod
    def _load_font(primary_path: str, fallback_path: str, size: int) -> ImageFont.FreeTypeFont:
        """Load ``primary_path`` at ``size``, falling back to ``fallback_path``.

        Keeps the app running if the bold weight is somehow missing (e.g. a
        partial deploy): the regular weight is used instead of crashing.
        """
        try:
            return ImageFont.truetype(primary_path, size)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Font '{primary_path}' unavailable ({e}); using fallback")
            return ImageFont.truetype(fallback_path, size)


    def toggle_backlight(self, status: bool) -> None:
        """Turn the backlight on/off and flag a repaint when turning on.

        Args:
            status (bool): True to power the panel on, False to blank it.
        """
        with self._state_lock:
            self.is_on = status
            if status:
                self._dirty = True
        if status:
            self.disp.bl_DutyCycle(100)
        else:
            self.disp.bl_DutyCycle(0)

    def show_volume(self, pct: int) -> None:
        """Show the auto-hiding volume OSD at ``pct`` (0..100).

        Called from the ADC/volume thread (via ``radio.py``) whenever the knob
        moves. Records the level and a fresh ``monotonic()`` deadline so the
        bottom band shows a progress bar for ``theme.osd_duration`` seconds, then
        auto-restores the title/artist rows. Only flips shared state + the dirty
        flag; the single compositor thread does the drawing.

        Args:
            pct (int): The new volume level, clamped to 0..100.
        """
        pct = max(0, min(100, int(pct)))
        with self._state_lock:
            self._transient.show_volume(pct, self.theme.osd_duration)
            self._dirty = True

    def show_toast(self, text: str) -> None:
        """Show a brief centred preset toast naming the selected station.

        Called from the button thread (via ``radio.py``) when a preset is
        pressed. Records the text and a fresh ``monotonic()`` deadline; the
        compositor thread draws a centred pill over the current frame for
        ``theme.toast_duration`` seconds, then repaints once to clear it. Pressing a
        preset also counts as activity, so it dismisses the idle screensaver.

        Args:
            text (str): Station name to display (blank clears any pending toast).
        """
        text = (text or "").strip()
        with self._state_lock:
            self._transient.show_toast(text, self.theme.toast_duration)
            self._dirty = True

    def update_metadata(self, name: str, title: str, cover: str, md5: str = '0',
                        state: Optional[bool] = None,
                        art_mode: Optional[str] = None,
                        source: Optional[str] = None) -> None:
        """Update the shared metadata that the compositor thread renders.

        The raw ``name``/``title`` are split into the two bottom-band rows by
        :func:`textformat.split_artist_title`: the primary (track) line goes to
        the ``title`` row, the secondary (artist/station) line to the ``name``
        row. A row's scroll position only resets when its displayed text
        actually changes.

        Args:
            name (str): Source ``name`` field (artist for Spotify/AirPlay, or
                station for MPD).
            title (str): Source ``title`` field (track, or radio stream title).
            cover (str): Path to the cover-art/logo image (or "" for none).
            md5 (str): Hash of the cover; used to skip redundant redraws.
            state (bool, optional): Play/pause state; drives the status glyph.
                ``None`` leaves the previous value unchanged.
            art_mode (str, optional): ``"radio"`` or ``"cover"``; ``None``
                leaves the previous mode unchanged.
            source (str, optional): Active backend name (``"mpd"``/``"spotify"``
                /``"airplay"``) for the status-strip badge. ``None`` leaves it
                unchanged.
        """
        with self._state_lock:
            if art_mode is not None and art_mode != self.metadata['art_mode']:
                self.metadata['art_mode'] = art_mode
                self._dirty = True
            if source is not None and source != self.metadata['source']:
                self.metadata['source'] = source
                self._dirty = True
            if cover != self.metadata['cover'] or md5 != self.metadata['md5']:
                self.metadata['cover'] = cover
                self.metadata['md5'] = md5
                self._dirty = True
            if state is not None and state != self.metadata['state']:
                self.metadata['state'] = state
                self._dirty = True

            # Idle-screensaver bookkeeping (4.4): any *playback* keeps the radio
            # "active" and resets the idle timer, so the clock only appears after
            # a stretch of not playing. A fresh metadata push while playing also
            # counts as activity.
            if self.metadata['state']:
                self._transient.mark_activity()

            # Recompute the primary/secondary rows whenever the raw inputs or
            # the mode changed (the mode affects the split for radio vs cover).
            if (name != self.metadata['raw_name']
                    or title != self.metadata['raw_title']
                    or art_mode is not None):
                self.metadata['raw_name'] = name
                self.metadata['raw_title'] = title
                primary, secondary = textformat.split_artist_title(
                    name, title, self.metadata['art_mode'])
                if primary != self.metadata['title']['text']:
                    self.metadata['title'].update({
                            'text': primary, 'position': 0, 'direction': 'left'})
                    self._dirty = True
                if secondary != self.metadata['name']['text']:
                    self.metadata['name'].update({
                            'text': secondary, 'position': 0, 'direction': 'left'})
                    self._dirty = True


    def _round_center_radius(self) -> Tuple[int, int, int]:
        """Return the round-panel inscribed-circle ``(cx, cy, radius)``.

        Reads the geometry ``compute_layout`` stored on the round ``Layout``;
        falls back to the panel centre / half-min-dimension if (defensively) the
        round fields are missing, so a draw site never crashes.
        """
        center = self.layout.center
        radius = self.layout.radius
        if center is not None and radius is not None:
            return center.x, center.y, radius
        return self.width // 2, self.height // 2, min(self.width, self.height) // 2

    def _chord_at(self, y: int) -> int:
        """Return the inscribed-circle chord width at row ``y`` (round mode).

        In rectangular mode there is no circle to clamp to, so the full frame
        width is returned. Round mode clamps to :func:`layout.chord_width` so no
        element crosses the circular edge.
        """
        if self.shape != "round":
            return self.width
        cx, cy, radius = self._round_center_radius()
        return layout_mod.chord_width(y, radius, cy)

    def _sample_band_luma(self, arr: np.ndarray, top: int, bottom: int) -> Optional[float]:
        """Return the mean luminance of the art rows a text zone covers.

        Samples ``arr`` (the composed art RGB buffer) over rows ``[top, bottom)``
        clamped to the inscribed circle's chord at the band centre so the
        transparent/black corners outside the circle are not averaged in. Returns
        ``None`` when the region is empty, in which case callers keep the plain
        drop shadow (dark-art behaviour).

        Args:
            arr: ``HxWx3`` ``uint8`` art buffer.
            top: First row of the text zone (inclusive).
            bottom: Row just past the zone (exclusive).
        """
        height, width = arr.shape[:2]
        top = max(0, min(int(top), height))
        bottom = max(0, min(int(bottom), height))
        if bottom <= top:
            return None
        chord = max(1, self._chord_at((top + bottom) // 2))
        x0 = max(0, (width - chord) // 2)
        x1 = min(width, x0 + chord)
        if x1 <= x0:
            return None
        region = arr[top:bottom, x0:x1]
        return compositor.relative_luminance(compositor.dominant_color(region))

    def _shadow_alpha_for(self, y: int) -> int:
        """Return the glyph-outline alpha (0..255) for text drawn at row ``y``.

        Zero means "no adaptive outline — use the plain 1px drop shadow" (the
        shipped look). Above the theme's ``adaptive_shadow_luma`` threshold the
        outline fades in linearly with the sampled background luminance, capped
        at a deliberately gentle maximum so the effect only *improves
        readability* over light art rather than looking like a hard outline.
        Always 0 on the rectangular panel or when the feature is disabled.
        """
        if self.shape != "round" or not self.theme.adaptive_shadow:
            return 0
        # Pick the zone whose luminance we sampled: the bottom text band covers
        # the title/artist rows; anything higher up (the top source label) uses
        # the top sample.
        luma = self._top_bg_luma
        if y >= self.layout.bottom_band.y:
            luma = self._bottom_bg_luma
        if luma is None:
            return 0
        threshold = float(self.theme.adaptive_shadow_luma)
        span = max(1.0, 255.0 - threshold)
        # 0 at the threshold, 1 as the background approaches pure white.
        frac = max(0.0, min(1.0, (luma - threshold) / span))
        # Gentle cap: even fully white art only gets a soft ~55% outline so the
        # white text keeps its character instead of gaining a hard black edge.
        return int(round(frac * 140))

    def _draw_text_with_shadow(self, draw: ImageDraw.ImageDraw,
                               pos: Tuple[int, int], text: str,
                               font: ImageFont.FreeTypeFont, fill) -> None:
        """Draw ``text`` with a shadow whose strength adapts to the background.

        Over dark art (or on the rectangular panel) this is byte-identical to the
        historical single 1px drop shadow. Over light art on the round panel it
        additionally lays down a soft, semi-transparent dark outline around the
        glyphs — faded in with the background luminance (see
        :meth:`_shadow_alpha_for`) — so white text keeps a readable edge without
        changing colour.
        """
        x, y = pos
        # Historical 1px drop shadow (kept in all cases as the base layer).
        draw.text((x + 1, y + 1), text, font=font, fill=self.theme.shadow_color)

        alpha = self._shadow_alpha_for(y)
        if alpha > 0:
            # Soft 8-way outline in the shadow colour at the computed alpha. The
            # outline is composited via an RGBA overlay so it stays translucent
            # (a plain draw.text would be fully opaque). One overlay covers the
            # whole frame the caller is drawing into.
            base = self._draw_image(draw)
            if base is not None:
                sc = self.theme.shadow_color
                overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
                odraw = ImageDraw.Draw(overlay)
                for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0),
                               (1, 0), (-1, 1), (0, 1), (1, 1)):
                    odraw.text((x + dx, y + dy), text, font=font,
                               fill=(sc[0], sc[1], sc[2], alpha))
                base.paste(overlay, (0, 0), overlay)

        draw.text((x, y), text, font=font, fill=fill)


    def _measure(self, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int, int]:
        """Return ``(width, height, top)`` of ``text`` in ``font``.

        ``top`` is the bbox's top offset (``bbox[1]``); subtracting it when
        drawing makes vertical centring exact instead of biased downward by the
        font's internal top bearing.
        """
        bbox = self._measure_draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox[1]

    def _advance_scroll(self, key: str) -> dict:
        """Advance one text row's scroll state and return a render snapshot.

        Called by the compositor thread under ``self._state_lock``. Computes
        whether the row's text overflows the safe area (and therefore scrolls),
        advances the 1px-per-due-tick position, bounces direction at the ends,
        and returns the data :meth:`_draw_row` needs. Mutates ``self.metadata``
        in place (scrolling flag / position / direction). Each row measures with
        its own font (the bold title vs the regular artist row).

        Args:
            key: ``"name"`` or ``"title"``.

        Returns:
            dict with keys ``text``, ``position``, ``text_width``,
            ``text_height``, ``text_top``, ``font``, ``color``, ``y``,
            ``row_h`` and ``scrolling``.
        """
        row = self.metadata[key]
        font = row['font']
        text = row['text']
        text_width, text_height, text_top = self._measure(text, font)

        # The available width is the safe-area width on the rectangular panel,
        # or the inscribed circle's chord at this row on the round panel, so a
        # row scrolls exactly when its text would otherwise cross that edge.
        if self.shape == "round":
            row_mid = row['y'] + row['row_h'] // 2
            safe_w = max(1, self._chord_at(row_mid))
        else:
            safe_w = self.layout.safe.w
        overflow = text_width > safe_w
        if overflow:
            # Pad so the wrap-around leaves a gap instead of jamming words.
            text = f" {text} "
            text_width, text_height, text_top = self._measure(text, font)
        row['scrolling'] = overflow

        if overflow:
            position = row['position']
            speed = row['speed']
            direction = row['direction']
            position += speed if direction == 'right' else -speed
            if position <= safe_w - text_width:
                direction = 'right'
            elif position >= 0:
                direction = 'left'
            row['position'] = position
            row['direction'] = direction

        return {
            'text': text,
            'position': row['position'],
            'text_width': text_width,
            'text_height': text_height,
            'text_top': text_top,
            'font': font,
            'color': row['color'],
            'y': row['y'],
            'row_h': row['row_h'],
            'scrolling': overflow,
        }

    def _draw_row(self, frame: Image.Image, draw: ImageDraw.ImageDraw,
                  snap: dict) -> None:
        """Draw one text row onto the full-frame canvas.

        Rows are anchored inside the safe-area bottom band; a scrolling row
        clamps its horizontal travel to the safe-area width so text never
        drifts into the rounded corners. A scrolling row additionally gets a
        soft ``theme.edge_fade_px`` fade at each end (Workstream 4.3) so text
        dissolves into the background instead of hard-clipping at the safe-area
        edge.

        Args:
            frame: The full 240x280 RGB frame (needed to sample the background
                under a scrolling row for the edge fade).
            draw: Draw context bound to ``frame``.
            snap: Row snapshot from :meth:`_advance_scroll` (carries its own
                font, colour, top ``y`` and row height).
        """
        text = snap['text']
        text_width = snap['text_width']
        text_height = snap['text_height']
        text_top = snap['text_top']
        font = snap['font']
        # Centre within the row and undo the font's top bearing (``text_top``)
        # so descenders are not clipped by the tight row.
        text_y = snap['y'] + (snap['row_h'] - text_height) // 2 - text_top

        if not snap['scrolling']:
            text_x = (self.width - text_width) // 2
            # Subtle shadow keeps text legible over the scrim/art. On the round
            # panel the shadow strength additionally adapts to the background
            # luminance so white text stays readable over light artwork.
            self._draw_text_with_shadow(draw, (text_x, text_y), text, font,
                                        snap['color'])
            return

        # Scrolling row: render the text (with shadow) onto a copy of the
        # safe-area-wide band region, then composite it back with faded left/
        # right edges so glyphs entering/leaving dissolve rather than clip.
        # On the round panel the window is clamped to the inscribed circle's
        # chord at this row (centred on the panel) instead of the full square
        # safe area, so scrolling text never crosses the circular edge.
        if self.shape == "round":
            row_mid = snap['y'] + snap['row_h'] // 2
            chord = max(1, self._chord_at(row_mid))
            rx0 = (self.width - chord) // 2
            rx1 = rx0 + chord
        else:
            safe = self.layout.safe
            rx0, rx1 = safe.x, safe.right
        ry0 = snap['y']
        ry1 = snap['y'] + snap['row_h']
        base_region = frame.crop((rx0, ry0, rx1, ry1))
        text_layer = base_region.copy()
        tdraw = ImageDraw.Draw(text_layer)
        text_x = snap['position']  # relative to the region's left (safe.x)
        tdraw.text((text_x + 1, text_y - ry0 + 1), text, font=font,
                   fill=self.theme.shadow_color)
        # Adaptive outline (round panel, light art): a soft semi-transparent
        # dark rim around the glyphs, faded in with the background luminance,
        # rendered into the region so it moves with the scrolling text.
        alpha = self._shadow_alpha_for(snap['y'])
        if alpha > 0:
            sc = self.theme.shadow_color
            overlay = Image.new("RGBA", text_layer.size, (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0),
                           (1, 0), (-1, 1), (0, 1), (1, 1)):
                odraw.text((text_x + dx, text_y - ry0 + dy), text, font=font,
                           fill=(sc[0], sc[1], sc[2], alpha))
            text_layer = text_layer.convert("RGBA")
            text_layer.paste(overlay, (0, 0), overlay)
            text_layer = text_layer.convert("RGB")
            tdraw = ImageDraw.Draw(text_layer)
        tdraw.text((text_x, text_y - ry0), text, font=font, fill=snap['color'])

        fade = self.theme.edge_fade_px
        blended = compositor.horizontal_edge_fade(
            np.asarray(base_region), np.asarray(text_layer),
            fade, fade,
        )
        frame.paste(Image.fromarray(blended, "RGB"), (rx0, ry0))


    def _open_cover(self) -> Tuple[Optional[Image.Image], str, str, str]:
        """Open the current cover image (RGBA), returning it plus cache keys.

        Returns ``(image_or_None, cover_path, md5, art_mode)`` where the image
        is ``None`` when there is no cover or it fails to load.
        """
        with self._state_lock:
            cover_path = self.metadata['cover']
            md5 = self.metadata['md5']
            art_mode = self.metadata['art_mode']
        image: Optional[Image.Image] = None
        if cover_path:
            try:
                with Image.open(cover_path) as img:
                    image = img.convert("RGBA")
            except Exception as e:
                logger.error(f"Unable to load cover '{cover_path}': {e}")
                image = None
        return image, cover_path, md5, art_mode

    def _fit_cover(self, img: Image.Image) -> Image.Image:
        """Return ``img`` fitted full-bleed to the whole panel (fill + crop)."""
        return ImageOps.fit(
            img.convert("RGB"), (self.width, self.height),
            method=Image.Resampling.LANCZOS, centering=(0.5, 0.5),
        )

    def _radio_backdrop(self, img: Optional[Image.Image]) -> Image.Image:
        """Build the radio-mode full-bleed backdrop for a station ``img``.

        A vertical gradient derived from the logo's dominant colour (over its
        opaque pixels), softened with a blurred, enlarged copy of the logo so
        the backdrop reads as branded rather than flat.
        """
        if img is not None:
            arr = np.asarray(img)  # RGBA
            rgb = arr[..., :3]
            alpha = arr[..., 3] if arr.shape[-1] == 4 else None
            dom = compositor.dominant_color(rgb, alpha)
        else:
            dom = self.no_art_color
        top = compositor.scale_color(dom, self.theme.backdrop_top_scale)
        bottom = compositor.scale_color(dom, self.theme.backdrop_bottom_scale)
        grad = compositor.vertical_gradient(self.width, self.height, top, bottom)
        backdrop = Image.fromarray(grad, "RGB")

        if img is not None:
            blur = ImageOps.fit(
                img, (self.width, self.height),
                method=Image.Resampling.LANCZOS, centering=(0.5, 0.5),
            ).convert("RGB").filter(ImageFilter.GaussianBlur(self.theme.backdrop_blur))
            backdrop = Image.blend(backdrop, blur, self.theme.backdrop_logo_blend)
        return backdrop

    def _scale_logo_to_box(self, img: Image.Image) -> Image.Image:
        """Scale a station logo to fit the centred safe-area logo box.

        Preserves aspect ratio and upscales small logos so they are crisp and
        prominent instead of a tiny stretched 160x120 tile.
        """
        box_w = max(1, int(self.logo_box.w * 0.9))
        box_h = max(1, int(self.logo_box.h * 0.9))
        ow, oh = img.size
        scale = min(box_w / ow, box_h / oh)
        nw = max(1, int(ow * scale))
        nh = max(1, int(oh * scale))
        return img.resize((nw, nh), Image.Resampling.LANCZOS)


    def _fallback_logo(self) -> Image.Image:
        """Render a placeholder art tile for a source with no cover/logo.

        For the Bluetooth source (which never carries cover art) this is a
        dedicated Bluetooth-glyph tile in a calm blue. For every other source
        it is a branded initials tile synthesised from the station name
        (Workstream 6.3): a logoless radio station shows its initials.

        Uses the raw station name and a bold font sized to the tile so the
        initials are prominent. Returned RGBA so ``_build_art_layer`` treats it
        exactly like a real station logo (dominant-colour backdrop + centred
        paste). Deterministic in the name/source, so it never invalidates the
        cache.

        Returns:
            An RGBA tile sized to the safe-area logo box.
        """
        with self._state_lock:
            name = self.metadata.get('raw_name', '') or ''
            source = self.metadata.get('source', '') or ''
        size = max(1, min(self.logo_box.w, self.logo_box.h))
        if source == "bluetooth":
            return logo_fallback.render_bluetooth_tile(
                size, glyph_color=self.theme.text_color)
        fonts_dir = f'{self.module_location}/fonts'
        tile_font = self._load_font(f'{fonts_dir}/Roboto-Condensed-Bold.ttf',
                                    f'{fonts_dir}/Roboto-Condensed-Regular.ttf',
                                    max(6, int(size * 0.42)))
        return logo_fallback.render_initials_tile(
            name, size, tile_font, text_color=self.theme.text_color)

    def _build_art_layer(self) -> Image.Image:
        """Compose (and cache) the static art layer: background + art + scrims.

        This is everything on the frame *except* the text rows: the full-bleed
        cover or the radio backdrop with its centred logo, then the darkened
        top/bottom chrome bands. Cached by ``(cover, md5, art_mode)`` so
        animating frames redraw only the text over a copy of this layer.
        """
        img, cover_path, md5, art_mode = self._open_cover()
        cache_key = (cover_path, md5, art_mode)
        if cache_key == self._art_cache_key and self._art_layer is not None:
            return self._art_layer

        if art_mode == "cover" and img is not None:
            art = self._fit_cover(img)
        else:
            # Radio mode. When the station has no usable logo, synthesise a
            # branded initials tile from its name (Workstream 6.3) so the
            # backdrop has something to sample and the centre is not empty.
            if img is None:
                img = self._fallback_logo()
            art = self._radio_backdrop(img)
            if img is not None:
                logo = self._scale_logo_to_box(img)
                ox = self.logo_box.x + max(0, (self.logo_box.w - logo.width) // 2)
                oy = self.logo_box.y + max(0, (self.logo_box.h - logo.height) // 2)
                art.paste(logo, (ox, oy), logo)

        # Darken the top and bottom chrome bands so text stays legible. The
        # round GC9A01 layout has no chrome bars (clock sits at the very top,
        # metadata hugs the bottom, both drawn straight onto the artwork), so
        # skip the scrims there; the rectangular ST7789 path is byte-identical.
        arr = np.asarray(art.convert("RGB"))
        if self.shape != "round":
            tb, bb = self.layout.top_band, self.layout.bottom_band
            scrim = self.theme.scrim_opacity
            arr = compositor.apply_scrim(arr, tb.y, tb.bottom, (0, 0, 0), scrim)
            arr = compositor.apply_scrim(arr, bb.y, bb.bottom, (0, 0, 0), scrim)
        else:
            # No scrim bars on the round panel: sample the background luminance
            # under each text zone once per art layer so the adaptive shadow can
            # fade in over light artwork (see ``_draw_text_with_shadow``). This
            # runs only when the art changes (keyed by ``_art_cache_key``), so
            # per-frame rendering stays cheap.
            tb, bb = self.layout.top_band, self.layout.bottom_band
            self._top_bg_luma = self._sample_band_luma(arr, tb.y, tb.bottom)
            self._bottom_bg_luma = self._sample_band_luma(arr, bb.y, bb.bottom)
        art = Image.fromarray(arr, "RGB")

        with self._state_lock:
            # Start an art-layer crossfade (4.1) from the outgoing layer to the
            # new one, unless this is the very first art layer (nothing to fade
            # from) or crossfades are disabled (animations off / crossfade_ms 0).
            # The new layer is cached immediately; the fade only affects what
            # _render_frame composites this window.
            if self._art_layer is not None and self.theme.crossfade_ms > 0:
                self._transient.start_crossfade(
                    self._art_layer, self.theme.crossfade_ms / 1000.0)
            self._art_cache_key = cache_key
            self._art_layer = art
        return art

    def _draw_status_strip(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the top-band status strip: source badge, clock, play/pause.

        On the rectangular ST7789 the three widgets share one row inside
        ``top_inner`` (an inner ~70% of the band): the badge is a small rounded
        pill on the left, the clock is centred, and a vector play/pause glyph
        sits on the right — everything clear of the rounded corners.

        The round GC9A01 uses a two-row layout instead (:meth:`_draw_status_strip_round`):
        the clock rides at the very top of the circle and the source badge
        (left) / play-pause glyph (right) sit on the row directly beneath it.

        Args:
            draw: Draw context bound to the full frame image.
        """
        if self.shape == "round":
            self._draw_status_strip_round(draw)
            return
        with self._state_lock:
            source = self.metadata['source']
            art_mode = self.metadata['art_mode']
            playing = self.metadata['state']
        band = self.layout.top_band
        # Chrome anchors start from the inner (never-cornered) region of the top
        # band, then spread outward by ``clock_spacing`` px on each side so the
        # centred clock gets more breathing room from the source badge (left)
        # and the play/pause glyph (right). Both anchors are clamped to the safe
        # area so, however far they spread, nothing lands in the rounded corners.
        top_inner = self.layout.top_inner
        clock_spacing = 20
        cy = band.cy
        safe = self.layout.safe
        edge = max(safe.x + 2, top_inner.x + 2 - clock_spacing)
        right_edge = min(safe.right - 2, top_inner.right - 2 + clock_spacing)

        # Source badge (left) — a rounded pill with small bold uppercase label.
        label = textformat.source_label(source, art_mode)
        lw, lh, ltop = self._measure(label, self.font_small)
        pad_x, pad_y = 7, 3
        pill_w = lw + 2 * pad_x
        pill_h = lh + 2 * pad_y
        px = edge
        py = cy - pill_h // 2
        draw.rounded_rectangle((px, py, px + pill_w, py + pill_h),
                               radius=pill_h // 2, fill=(255, 255, 255))
        draw.text((px + pad_x, py + pad_y - ltop), label,
                  font=self.font_small, fill=(20, 20, 20))
        badge_right = px + pill_w

        # Play/pause glyph (right) — vector shapes so no glyph font is needed.
        g = 14  # glyph box size
        gx = right_edge - g
        gy = cy - g // 2
        text_color = self.theme.text_color
        if playing:
            # Right-pointing triangle = play (shown while playing).
            draw.polygon([(gx, gy), (gx, gy + g), (gx + g, gy + g // 2)],
                         fill=text_color)
        else:
            # Two vertical bars = pause (shown while paused/stopped).
            bw = g // 3
            draw.rectangle((gx, gy, gx + bw, gy + g), fill=text_color)
            draw.rectangle((gx + g - bw, gy, gx + g, gy + g), fill=text_color)

        # Clock (centre) — HH:MM, small bold with a shadow. Centre it in the gap
        # between the badge and the glyph so it never overlaps either, even for
        # the wider "SPOTIFY"/"AIRPLAY" labels on the narrow panel.
        clock = datetime.now().strftime("%H:%M")
        cw, ch, ctop = self._measure(clock, self.font_clock_status)
        gap_left = badge_right + 6
        gap_right = gx - 6
        cxp = gap_left + max(0, (gap_right - gap_left - cw) // 2)
        cyp = cy - ch // 2 - ctop
        draw.text((cxp + 1, cyp + 1), clock, font=self.font_clock_status,
                  fill=self.theme.shadow_color)
        draw.text((cxp, cyp), clock, font=self.font_clock_status, fill=text_color)

    def _draw_status_strip_round(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the round-panel status strip: the active source, centred at top.

        The round GC9A01 layout drops the dark top bar, the clock, and the
        play/pause glyph entirely. All that remains is the **active source**
        (e.g. ``RADIO`` / ``SPOTIFY`` / ``AIRPLAY``) rendered as plain centred
        text — styled like the old clock (bold, with a shadow so it stays
        legible straight on the artwork) — near the very top of the circle.
        The text is clamped to the inscribed circle's chord at that row so it
        never crosses the circular bezel.

        Args:
            draw: Draw context bound to the full 240x240 frame image.
        """
        with self._state_lock:
            source = self.metadata['source']
            art_mode = self.metadata['art_mode']
        band = self.layout.top_band
        text_color = self.theme.text_color

        # Active source as plain centred text near the top of the circle.
        label = textformat.source_label(source, art_mode)
        lw, lh, ltop = self._measure(label, self.font_clock_status)
        lx = (self.width - lw) // 2
        ly = band.cy - lh // 2 - ltop
        self._draw_text_with_shadow(draw, (lx, ly), label,
                                    self.font_clock_status, text_color)

    def _osd_visible(self) -> bool:
        """Return True while the volume OSD is within its display window."""
        with self._state_lock:
            return self._transient.osd_visible()

    def _draw_volume_osd(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the volume OSD across the bottom band (Workstream 4.2).

        While visible the OSD replaces the title/artist rows with a labelled
        progress bar: a small "VOL" tag, a rounded track/fill bar, and the
        percentage. Everything stays inside the safe area so nothing lands in
        the rounded corners.

        Args:
            draw: Draw context bound to the full 240x280 frame image.
        """
        with self._state_lock:
            pct = self._transient.volume_pct
        band = self.layout.bottom_band
        safe = self.layout.safe
        cy = band.cy

        # "VOL" tag on the left, percentage on the right, bar spanning between.
        tag = "VOL"
        tw, th, ttop = self._measure(tag, self.font_small)
        pct_text = f"{pct}%"
        pw, ph, ptop = self._measure(pct_text, self.font_small)

        left = safe.x + 2
        right = safe.right - 2
        draw.text((left, cy - th // 2 - ttop), tag,
                  font=self.font_small, fill=self.theme.text_color)
        draw.text((right - pw, cy - ph // 2 - ptop), pct_text,
                  font=self.font_small, fill=self.theme.text_color)

        bar_h = self.theme.osd_bar_height
        bar_x0 = left + tw + 8
        bar_x1 = right - pw - 8
        bar_y0 = cy - bar_h // 2
        bar_y1 = bar_y0 + bar_h
        radius = bar_h // 2
        if bar_x1 - bar_x0 > 2 * radius:
            draw.rounded_rectangle((bar_x0, bar_y0, bar_x1, bar_y1),
                                   radius=radius, fill=self.theme.osd_track_color)
            fill_w = int(round((bar_x1 - bar_x0) * pct / 100))
            if fill_w >= 2 * radius:
                draw.rounded_rectangle((bar_x0, bar_y0, bar_x0 + fill_w, bar_y1),
                                       radius=radius, fill=self.theme.osd_fill_color)
            elif fill_w > 0:
                draw.rectangle((bar_x0, bar_y0, bar_x0 + fill_w, bar_y1),
                               fill=self.theme.osd_fill_color)

    def _draw_image(self, draw: ImageDraw.ImageDraw):
        """Return the ``PIL.Image`` a draw context is bound to, or ``None``.

        Pillow exposes the target image on ``ImageDraw`` as the private ``_image``
        attribute (``.im`` is the lower-level core object). We read it so the
        round OSD can composite a supersampled overlay onto the frame the caller
        already handed us, without threading the frame through the signature.
        Defensive: returns ``None`` if the attribute is unavailable so the caller
        can fall back to non-antialiased drawing.
        """
        img = getattr(draw, "_image", None)
        if isinstance(img, Image.Image):
            return img
        return None

    @staticmethod
    def _as_rgba(color: Tuple[int, ...]) -> Tuple[int, int, int, int]:
        """Return ``color`` as an opaque RGBA 4-tuple for RGBA overlays."""
        if len(color) >= 4:
            return (color[0], color[1], color[2], color[3])
        return (color[0], color[1], color[2], 255)

    def _draw_volume_osd_round(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the round-panel volume OSD as a ring gauge (Step 5.2 / Step 6).

        The circular GC9A01 replaces the horizontal bar with an arc/ring gauge
        centred on the panel.  The sweep angle (``theme.osd_arc_span``, default
        270°) and stroke width (``theme.osd_ring_thickness``, default 0 = auto
        from ``osd_bar_height``) are configurable from ``display.conf`` so the
        gauge can be tuned without code changes.  The filled portion is
        proportional to ``pct`` and the percentage is drawn in the middle.

        No background track arc is drawn — only the filled portion is visible.
        Both ends of the fill arc are capped with a small filled circle so the
        arc appears with rounded rather than flat endpoints.

        Args:
            draw: Draw context bound to the full 240x240 frame image.
        """
        with self._state_lock:
            pct = self._transient.volume_pct
        cx, cy, radius = self._round_center_radius()

        # Ring geometry: an inset arc so the thick stroke stays clear of the
        # circular bezel. Thickness scales with the themed bar height unless the
        # caller has supplied an explicit osd_ring_thickness override (Step 6).
        thickness = (self.theme.osd_ring_thickness
                     if self.theme.osd_ring_thickness > 0
                     else max(4, self.theme.osd_bar_height))
        ring_inset = max(thickness, radius // 5)
        rr = max(1, radius - ring_inset)

        # Arc gauge: configurable sweep angle from theme (Step 6).  The gap at
        # the bottom (360 - osd_arc_span degrees) is split evenly left/right so
        # the gauge is always symmetric.  Default is 270° (135° start).
        span_deg = self.theme.osd_arc_span
        gap_half = (360 - span_deg) // 2
        start_deg = 90 + gap_half
        fill_deg = start_deg + int(round(span_deg * max(0, min(100, pct)) / 100))

        # Draw only the proportional fill arc — no grey background track.
        # Rounded caps: a filled circle of radius (thickness // 2) placed at
        # each endpoint of the fill arc so the ends look rounded instead of flat.
        #
        # Antialiasing (supersampling): Pillow's arc/ellipse have no antialiasing
        # so a thick ring on artwork looks jagged. We therefore render the ring
        # and its caps into a transparent RGBA overlay scaled by
        # ``theme.osd_supersample`` and downscale it with LANCZOS before
        # compositing, which averages the hard edges into smooth ones. The caps
        # are centred on the stroke's centreline (radius ``rr - thickness/2``),
        # not on ``rr``; Pillow strokes an arc *inward* from the bounding radius,
        # so placing caps at ``rr`` left them poking past the arc as separate
        # circles.
        if fill_deg > start_deg:
            scale = max(1, int(self.theme.osd_supersample))
            cap_r = thickness // 2
            cap_center_r = rr - thickness / 2.0

            frame = self._draw_image(draw)
            if frame is not None and scale > 1:
                overlay = Image.new("RGBA", (frame.width * scale,
                                             frame.height * scale), (0, 0, 0, 0))
                odraw = ImageDraw.Draw(overlay)
                sx, sy = cx * scale, cy * scale
                srr = rr * scale
                obox = (sx - srr, sy - srr, sx + srr, sy + srr)
                fill_rgba = self._as_rgba(self.theme.osd_fill_color)
                odraw.arc(obox, start_deg, fill_deg, fill=fill_rgba,
                          width=thickness * scale)
                s_cap_r = cap_r * scale
                s_cap_center_r = cap_center_r * scale
                for angle_deg in (start_deg, fill_deg):
                    rad = math.radians(angle_deg)
                    px = sx + s_cap_center_r * math.cos(rad)
                    py = sy + s_cap_center_r * math.sin(rad)
                    odraw.ellipse(
                        (px - s_cap_r, py - s_cap_r, px + s_cap_r, py + s_cap_r),
                        fill=fill_rgba,
                    )
                smooth = overlay.resize((frame.width, frame.height),
                                        Image.Resampling.LANCZOS)
                frame.paste(smooth, (0, 0), smooth)
            else:
                # No overlay handle (defensive) or supersampling disabled:
                # draw straight onto the frame with centred caps.
                box = (cx - rr, cy - rr, cx + rr, cy + rr)
                draw.arc(box, start_deg, fill_deg,
                         fill=self.theme.osd_fill_color, width=thickness)
                for angle_deg in (start_deg, fill_deg):
                    rad = math.radians(angle_deg)
                    cap_x = cx + cap_center_r * math.cos(rad)
                    cap_y = cy + cap_center_r * math.sin(rad)
                    draw.ellipse(
                        (cap_x - cap_r, cap_y - cap_r,
                         cap_x + cap_r, cap_y + cap_r),
                        fill=self.theme.osd_fill_color,
                    )


        # Percentage centred inside the ring, with a subtle shadow.
        pct_text = f"{pct}%"
        pw, ph, ptop = self._measure(pct_text, self.font_title)
        tx = cx - pw // 2
        ty = cy - ph // 2 - ptop
        draw.text((tx + 1, ty + 1), pct_text, font=self.font_title,
                  fill=self.theme.shadow_color)
        draw.text((tx, ty), pct_text, font=self.font_title,
                  fill=self.theme.text_color)

    def _render_frame(self) -> Image.Image:
        """Compose the full 240x280 frame from the current shared state.

        Priority of what fills the frame:

        1. Idle clock screensaver (4.4) — when the radio is on but has not been
           playing for ``_IDLE_TIMEOUT``: a large clock, the date, and the last
           source, over a dark gradient.
        2. Otherwise the now-playing layout: the cached static art layer (with a
           brief crossfade from the previous art, 4.1), the top-band status
           strip, then either the volume OSD (4.2) or the two text rows.

        The preset toast (4.5) is drawn last, on top of everything.
        """
        if self._screensaver_active():
            frame = self._render_screensaver()
        else:
            frame = self._compose_now_playing()

        if self._toast_visible():
            self._draw_toast(ImageDraw.Draw(frame))
        return frame

    def _compose_now_playing(self) -> Image.Image:
        """Compose the standard now-playing frame (art + chrome + text/OSD)."""
        art = self._build_art_layer()
        # Crossfade (4.1): dissolve from the previous art layer to the current
        # one over the fade window, then fall back to the plain current art.
        crossfade_ms = self.theme.crossfade_ms
        with self._state_lock:
            fade_from = self._transient.crossfade_from
            t = self._transient.crossfade_progress(crossfade_ms / 1000.0)
        if fade_from is not None and t is not None:
            frame = Image.blend(fade_from, art, t)
        else:
            if fade_from is not None:
                with self._state_lock:
                    self._transient.clear_crossfade()
            frame = art.copy()

        draw = ImageDraw.Draw(frame)
        self._draw_status_strip(draw)
        if self._osd_visible():
            if self.shape == "round":
                self._draw_volume_osd_round(draw)
            else:
                self._draw_volume_osd(draw)
        else:
            with self._state_lock:
                title_snap = self._advance_scroll('title')
                name_snap = self._advance_scroll('name')
            self._draw_row(frame, draw, title_snap)
            self._draw_row(frame, draw, name_snap)
        return frame

    def _crossfade_active(self) -> bool:
        """Return True while an art-layer crossfade is still in progress."""
        with self._state_lock:
            return self._transient.crossfade_active()

    def _toast_visible(self) -> bool:
        """Return True while the preset toast is within its display window."""
        with self._state_lock:
            return self._transient.toast_visible()

    def _screensaver_active(self) -> bool:
        """Return True when the idle clock screensaver should be shown.

        Active when the panel is on, nothing is playing, and there has been no
        playback activity for ``theme.idle_timeout`` — but never while the volume OSD
        is up, so adjusting volume wakes the now-playing view.
        """
        with self._state_lock:
            if not self.metadata['state'] and not self._transient.osd_visible():
                return self._transient.idle_elapsed(self.theme.idle_timeout)
            return False

    def _render_screensaver(self) -> Image.Image:
        """Render the idle screensaver: big clock, date and last-source line."""
        arr = compositor.vertical_gradient(self.width, self.height,
                                           self.theme.idle_bg_top,
                                           self.theme.idle_bg_bottom)
        frame = Image.fromarray(arr, "RGB")
        draw = ImageDraw.Draw(frame)

        now = datetime.now()
        clock = now.strftime("%H:%M")
        date = now.strftime("%a %d %b")
        with self._state_lock:
            source = textformat.source_label(self.metadata['source'],
                                             self.metadata['art_mode'])

        cw, chh, ctop = self._measure(clock, self.font_clock)
        dw, dh, dtop = self._measure(date, self.font_date)
        sw, sh, stop = self._measure(source, self.font_small)
        block_h = chh + 8 + dh + 10 + sh
        y = (self.height - block_h) // 2

        draw.text(((self.width - cw) // 2, y - ctop), clock,
                  font=self.font_clock, fill=self.theme.text_color)
        y += chh + 8
        draw.text(((self.width - dw) // 2, y - dtop), date,
                  font=self.font_date, fill=self.theme.subtext_color)
        y += dh + 10
        draw.text(((self.width - sw) // 2, y - stop), source,
                  font=self.font_small, fill=self.theme.subtext_color)
        return frame

    def _draw_toast(self, draw: ImageDraw.ImageDraw) -> None:
        """Draw the centred preset toast pill over the current frame (4.5)."""
        with self._state_lock:
            text = self._transient.toast_text
        if not text:
            return
        tw, th, ttop = self._measure(text, self.font_artist)
        safe = self.layout.safe
        pad_x, pad_y = 16, 10
        cx = self.width // 2
        cy = self.logo_box.cy if self.logo_box.h > 0 else self.height // 2
        # Cap the pill to the safe-area width on the rectangular panel, or to the
        # inscribed circle's chord at the toast's row on the round panel, so the
        # pill never crosses the circular edge.
        max_w = self._chord_at(cy) if self.shape == "round" else safe.w
        pill_w = min(max_w, tw + 2 * pad_x)
        pill_h = th + 2 * pad_y
        x0 = cx - pill_w // 2
        y0 = cy - pill_h // 2
        x1 = x0 + pill_w
        y1 = y0 + pill_h

        # Blend a dark rounded pill over the art so the label stays legible.
        region = np.asarray(draw._image.crop((x0, y0, x1, y1)).convert("RGB"))
        scrimmed = compositor.apply_scrim(region, 0, region.shape[0],
                                          self.theme.toast_bg_color,
                                          self.theme.toast_opacity)
        draw._image.paste(Image.fromarray(scrimmed, "RGB"), (x0, y0))
        draw.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=pill_h // 2,
                               outline=(90, 90, 96), width=1)
        draw.text((cx - tw // 2, cy - th // 2 - ttop), text,
                  font=self.font_artist, fill=self.theme.toast_text_color)

    def _render_splash(self) -> Image.Image:
        """Render the branded boot splash shown before the first metadata (4.6).

        A dark vertical gradient with the product name centred and a small
        subtitle, so the panel shows product identity at power-on instead of the
        driver's clear() white. Uses only the vendored fonts and numpy helpers.
        """
        arr = compositor.vertical_gradient(self.width, self.height,
                                           self.theme.idle_bg_top,
                                           self.theme.idle_bg_bottom)
        frame = Image.fromarray(arr, "RGB")
        draw = ImageDraw.Draw(frame)

        title = "RADIO"
        subtitle = "starting…"
        tw, th, ttop = self._measure(title, self.font_clock)
        sw, sh, stop = self._measure(subtitle, self.font_date)
        block_h = th + 10 + sh
        y = (self.height - block_h) // 2
        draw.text(((self.width - tw) // 2, y - ttop), title,
                  font=self.font_clock, fill=self.theme.text_color)
        y += th + 10
        draw.text(((self.width - sw) // 2, y - stop), subtitle,
                  font=self.font_date, fill=self.theme.subtext_color)
        return frame


    def _push_frame(self, frame: Image.Image, force: bool = False) -> bool:
        """Pack ``frame`` to RGB565 and push it to the panel in one write.

        Suppresses the SPI transmit when the packed bytes are identical to the
        last frame pushed (unless ``force`` is set for the self-healing
        refresh), so a recompose that changes nothing costs no bus traffic.

        Args:
            frame: The composed 240x280 RGB image.
            force: Push even if identical to the last frame (self-heal / init).

        Returns:
            bool: ``True`` if the frame was transmitted to the panel.
        """
        if self.theme.rotate_180:
            frame = frame.rotate(180)
        pix = compositor.pack_rgb565(np.asarray(frame))
        if not force and pix == self._last_frame_sig:
            return False
        self.disp.ShowFullFrame(pix)
        self._last_frame_sig = pix
        return True

    def _any_scrolling(self) -> bool:
        """Return True if either text row is currently overflow-scrolling."""
        with self._state_lock:
            return (self.metadata['name']['scrolling']
                    or self.metadata['title']['scrolling'])

    def update_text(self) -> None:
        """Compositor loop: the single SPI writer thread.

        Recomposes and pushes a full frame only when the content changed
        (``_dirty``) or an animation is active (a text row is scrolling),
        throttled to ~20 fps. When the panel is on but nothing is animating and
        nothing changed, it sleeps without touching the bus (idle = no SPI
        traffic). A periodic forced repaint self-heals rare transient glitches.
        """
        frames = 0
        tick = 0
        transient_was_active = False
        while True:
            with self._state_lock:
                is_on = self.is_on
            if not is_on:
                sleep(1)
                continue

            # The clock in the status strip changes once a minute; flag the
            # frame dirty when HH:MM rolls over so it refreshes without any
            # other SPI traffic in between.
            clock = datetime.now().strftime("%H:%M")
            if clock != self._last_clock:
                self._last_clock = clock
                with self._state_lock:
                    self._dirty = True

            with self._state_lock:
                dirty = self._dirty
                self._dirty = False
            animating = self._any_scrolling()

            # Transient overlays & motion (Workstream 4). The volume OSD, preset
            # toast and art crossfade each keep the loop ticking at frame cadence
            # while active so they appear promptly and animate smoothly; when the
            # last of them lapses (active -> inactive) we force one more compose
            # so the underlying now-playing view is restored with no further SPI
            # traffic (a "timed dirty").
            osd_visible = self._osd_visible()
            toast_visible = self._toast_visible()
            crossfading = self._crossfade_active()
            transient = osd_visible or toast_visible or crossfading
            if transient_was_active and not transient:
                dirty = True
            transient_was_active = transient

            # Advance the per-row scroll cadence: the title advances every tick,
            # the name every other tick (its ``interval``). A row that is not
            # yet marked scrolling still needs one compose to learn it overflows,
            # which ``dirty`` covers. Text rows are hidden while the OSD is up,
            # so only advance them when the OSD is not visible.
            due = dirty
            if animating and not osd_visible:
                with self._state_lock:
                    for key in ('name', 'title'):
                        row = self.metadata[key]
                        if row['scrolling'] and (tick % row.get('interval', 1) == 0):
                            due = True
            # A live transient (OSD tracking the knob, toast, crossfade) refreshes
            # at frame cadence so its animation/value stays current.
            if transient:
                due = True

            if due:
                frames += 1
                force = (frames % _SELF_HEAL_FRAMES == 0)
                self._push_frame(self._render_frame(), force=force)

            tick += 1
            if (animating and not osd_visible) or dirty or transient:
                sleep(_FRAME_INTERVAL)
            else:
                # Idle: nothing to draw. Poll the dirty flag gently, no SPI.
                sleep(0.2)

