# display_control.py
"""SPI display controller for the ST7789 1.69 inch panel.

Rendering architecture (Workstream 1 of the display redesign): a single
background thread composes **one full 240x280 frame** from shared state and
pushes it to the panel with a single write. It transmits **only when the
composed frame changes or an animation (scrolling text) is active**; when idle
it sleeps with no SPI traffic. Pushes are throttled to ~20 fps.

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
"""
import logging
import os
import threading
from datetime import datetime
from time import monotonic, sleep
from typing import Optional, Tuple

import numpy as np
from display_1_inch_69 import LCD_1inch69, compositor, logo_fallback, textformat
from display_1_inch_69 import layout as layout_mod
from display_1_inch_69 import theme as theme_mod
from display_1_inch_69.transient_state import TransientState
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from utilities import UtilityLibrary

utility = UtilityLibrary()
logger = logging.getLogger(__name__)

# Target frame cadence for pushes while animating (~20 fps). Idle frames never
# transmit at all, so this only bounds the busy (scrolling) case.
_FRAME_INTERVAL = 0.05
# Force a full self-healing repaint roughly this often (in composed frames) to
# repair a rare transient SPI/display glitch without every-frame full repaints.
_SELF_HEAL_FRAMES = 600

# Safe-area / chrome constants (Workstream 2/3). These are now the **defaults**
# for the ``[ui]`` theme (Workstream 5): ``theme.Theme`` mirrors every value
# below, and :class:`DisplayController` reads the resolved theme into instance
# attributes at construction. They remain here (and in ``theme.py``) as the
# single source of the shipped look, so an absent/empty ``[ui]`` section renders
# byte-identically to before.
_SAFE_INSET = 14          # px kept clear of the rounded physical corners
_TOP_BAND_HEIGHT = 44     # px height of the top chrome band (clock / badge)
_BOTTOM_BAND_HEIGHT = 82  # px height of the bottom band (title + artist rows)
_SCRIM_OPACITY = 0.55     # darkening strength of the chrome bands over art
_BACKDROP_BLUR = 18       # gaussian blur radius for the radio-mode backdrop
# Radio-mode backdrop contrast (WS8). The backdrop gradient is derived from the
# logo's dominant colour but kept deliberately *darker* than the (usually
# bright) logo so the centred tile stands out instead of washing into a
# same-colour field. Top row = dom * _BACKDROP_TOP_SCALE, bottom row = dom *
# _BACKDROP_BOTTOM_SCALE; _BACKDROP_LOGO_BLEND is how much of the blurred logo
# is mixed in (higher = lower edge contrast). Mirrored in theme.Theme.
_BACKDROP_TOP_SCALE = 0.55     # was 1.15 (top brighter than logo) -> now darker
_BACKDROP_BOTTOM_SCALE = 0.20  # was 0.35
_BACKDROP_LOGO_BLEND = 0.20    # was 0.35

# Typography (Workstream 3). A three-level hierarchy over the art: a large bold
# title, a medium artist/subtitle, and a small weight for the status strip.
_TITLE_SIZE = 30          # px, bold — the primary (track) line
_ARTIST_SIZE = 22         # px, regular — the secondary (artist/station) line
_SMALL_SIZE = 17          # px, bold — status-strip badge / clock
_TEXT_COLOR = "WHITE"
_SUBTEXT_COLOR = (200, 200, 200)   # slightly dimmer for the secondary line
_SHADOW_COLOR = (0, 0, 0)

# Motion & transient states (Workstream 4). Durations are in seconds and become
# part of the [ui] theme in Workstream 5. Each auto-hiding overlay records a
# ``monotonic()`` deadline; the compositor loop keeps painting until it passes,
# then repaints once more to clear the overlay (a "timed dirty"). No extra
# threads are introduced — all motion is advanced inside the single writer loop.
_OSD_DURATION = 1.5       # s the volume OSD stays visible after the last change
_EDGE_FADE_PX = 12        # px soft fade at each end of a scrolling text row
_OSD_BAR_HEIGHT = 12      # px height of the volume OSD progress bar
_OSD_TRACK_COLOR = (70, 70, 78)     # unfilled portion of the volume bar
_OSD_FILL_COLOR = (255, 255, 255)   # filled portion of the volume bar

# Preset toast: a brief centred banner naming the station on a button press.
_TOAST_DURATION = 1.6     # s the preset toast stays visible
_TOAST_BG_COLOR = (0, 0, 0)         # pill background (blended at _TOAST_OPACITY)
_TOAST_OPACITY = 0.72     # pill background opacity over the art
_TOAST_TEXT_COLOR = "WHITE"

# Cover crossfade: when the art layer changes (new cover/logo/mode) the old and
# new art cross-dissolve over this window so switches feel smooth, not abrupt.
_CROSSFADE_MS = 150       # ms art-layer cross-dissolve duration

# Idle clock screensaver: when the radio is on but not playing for this long,
# replace the now-playing layout with a large clock + date + last source.
_IDLE_TIMEOUT = 30.0      # s of no playback before the screensaver appears
_CLOCK_LARGE_SIZE = 64    # px, bold — the big idle clock
_DATE_SIZE = 20           # px, regular — the idle date line
_IDLE_BG_TOP = (18, 18, 24)         # idle backdrop gradient (top)
_IDLE_BG_BOTTOM = (6, 6, 10)        # idle backdrop gradient (bottom)


class DisplayController:
    """Owns the SPI panel and composes one full frame from shared state."""

    def __init__(self) -> None:
        """Initialise the display, layout, render buffers and update thread."""
        self.module_location = os.path.dirname(os.path.abspath(__file__))
        conf = utility.read_config(f'{self.module_location}/display.conf')

        # Resolve the [ui] theme (Workstream 5). Absent section -> shipped
        # defaults (byte-identical to the pre-theme look); malformed values fall
        # back per key and never block boot. Every draw site below reads these
        # instance attributes so the whole UI is themeable from one section.
        self.theme = theme_mod.build_theme(conf.get('ui'))
        t = self.theme

        # Display settings
        self.width = conf['display']['width']
        self.height = conf['display']['height']
        self.disp = LCD_1inch69.LCD_1inch69(
            rst=conf['display']['rst'],
            dc=conf['display']['dc'],
            bl=conf['display']['bl'],
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
        bottom band shows a progress bar for ``_OSD_DURATION`` seconds, then
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
        ``_TOAST_DURATION`` seconds, then repaints once to clear it. Pressing a
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
        soft ``_EDGE_FADE_PX`` fade at each end (Workstream 4.3) so text
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
            # Subtle shadow keeps text legible over the scrim/art.
            draw.text((text_x + 1, text_y + 1), text, font=font,
                      fill=self.theme.shadow_color)
            draw.text((text_x, text_y), text, font=font, fill=snap['color'])
            return

        # Scrolling row: render the text (with shadow) onto a copy of the
        # safe-area-wide band region, then composite it back with faded left/
        # right edges so glyphs entering/leaving dissolve rather than clip.
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

        # Darken the top and bottom chrome bands so text stays legible.
        arr = np.asarray(art.convert("RGB"))
        tb, bb = self.layout.top_band, self.layout.bottom_band
        scrim = self.theme.scrim_opacity
        arr = compositor.apply_scrim(arr, tb.y, tb.bottom, (0, 0, 0), scrim)
        arr = compositor.apply_scrim(arr, bb.y, bb.bottom, (0, 0, 0), scrim)
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

        Everything is placed inside ``top_inner`` (an inner ~70% of the band)
        so nothing lands in the rounded corners. The badge is a small rounded
        pill on the left, the clock is centred, and a vector play/pause glyph
        sits on the right.

        Args:
            draw: Draw context bound to the full 240x280 frame image.
        """
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
        safe = self.layout.safe
        clock_spacing = 20
        edge = max(safe.x + 2, top_inner.x + 2 - clock_spacing)
        right_edge = min(safe.right - 2, top_inner.right - 2 + clock_spacing)
        cy = band.cy

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
        playback activity for ``_IDLE_TIMEOUT`` — but never while the volume OSD
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
        pill_w = min(safe.w, tw + 2 * pad_x)
        pill_h = th + 2 * pad_y
        cx = self.width // 2
        cy = self.logo_box.cy if self.logo_box.h > 0 else self.height // 2
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

