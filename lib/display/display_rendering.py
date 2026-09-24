"""Artwork, status, and transient-overlay rendering for DisplayController."""

import logging
import math
from datetime import datetime
from typing import Any, Optional, Tuple

import numpy as np
from display import compositor, logo_fallback, textformat
from PIL import Image, ImageDraw, ImageFilter, ImageOps

logger = logging.getLogger(__name__)
Font = Any
Color = Tuple[int, int, int]


class DisplayRenderingMixin:
    """Cohesive pixel rendering mixed into the state-owning display controller."""

    width: int
    height: int
    shape: str
    metadata: Any
    layout: Any
    theme: Any
    logo_box: Any
    no_art_color: Color
    module_location: str
    _state_lock: Any
    _transient: Any
    _art_cache_key: Any
    _art_layer: Any
    _top_bg_luma: Optional[float]
    _bottom_bg_luma: Optional[float]
    font_clock_status: Font
    font_small: Font
    font_title: Font
    font_toast: Font

    @staticmethod
    def _load_font(primary_path: str, fallback_path: str, size: int) -> Font:
        raise NotImplementedError

    def _sample_band_luma(self, arr: np.ndarray, top: int, bottom: int) -> Optional[float]:
        raise NotImplementedError

    def _measure(self, text: str, font: Font) -> Tuple[int, int, int]:
        raise NotImplementedError

    def _draw_text_with_shadow(
        self, draw: ImageDraw.ImageDraw, pos: Tuple[int, int], text: str, font: Font, fill: Color
    ) -> None:
        raise NotImplementedError

    def _round_center_radius(self) -> Tuple[int, int, int]:
        raise NotImplementedError

    def _open_cover(self) -> Tuple[Optional[Image.Image], str, str, str]:
        """Open the current cover image (RGBA), returning it plus cache keys.

        Returns ``(image_or_None, cover_path, md5, art_mode)`` where the image
        is ``None`` when there is no cover or it fails to load.
        """
        with self._state_lock:
            cover_path = self.metadata["cover"]
            md5 = self.metadata["md5"]
            art_mode = self.metadata["art_mode"]
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
            img.convert("RGB"),
            (self.width, self.height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
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
            blur = (
                ImageOps.fit(
                    img,
                    (self.width, self.height),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
                .convert("RGB")
                .filter(ImageFilter.GaussianBlur(self.theme.backdrop_blur))
            )
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
        dedicated Bluetooth-glyph tile in a calm blue, and for the USB source a
        dedicated USB-glyph tile in a muted teal. Both glyphs are rasterised at
        build time from the source SVGs (see ``logo_fallback`` and
        ``scripts/render-source-glyphs.py``). For every other source it is a
        branded initials tile synthesised from the station name
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
            name = self.metadata.get("raw_name", "") or ""
            source = self.metadata.get("source", "") or ""
        size = max(1, min(self.logo_box.w, self.logo_box.h))
        if source == "bluetooth":
            return logo_fallback.render_bluetooth_tile(size, glyph_color=self.theme.text_color)
        if source == "usb":
            return logo_fallback.render_usb_tile(size, glyph_color=self.theme.text_color)
        fonts_dir = f"{self.module_location}/fonts"
        tile_font = self._load_font(
            f"{fonts_dir}/Roboto-Condensed-Bold.ttf",
            f"{fonts_dir}/Roboto-Condensed-Regular.ttf",
            max(6, int(size * 0.42)),
        )
        return logo_fallback.render_initials_tile(
            name, size, tile_font, text_color=self.theme.text_color
        )

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
                self._transient.start_crossfade(self._art_layer, self.theme.crossfade_ms / 1000.0)
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
            source = self.metadata["source"]
            art_mode = self.metadata["art_mode"]
            playing = self.metadata["state"]
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
        draw.rounded_rectangle(
            (px, py, px + pill_w, py + pill_h), radius=pill_h // 2, fill=(255, 255, 255)
        )
        draw.text((px + pad_x, py + pad_y - ltop), label, font=self.font_small, fill=(20, 20, 20))
        badge_right = px + pill_w

        # Play/pause glyph (right) — vector shapes so no glyph font is needed.
        g = 14  # glyph box size
        gx = right_edge - g
        gy = cy - g // 2
        text_color = self.theme.text_color
        if playing:
            # Right-pointing triangle = play (shown while playing).
            draw.polygon([(gx, gy), (gx, gy + g), (gx + g, gy + g // 2)], fill=text_color)
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
        draw.text(
            (cxp + 1, cyp + 1), clock, font=self.font_clock_status, fill=self.theme.shadow_color
        )
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
            source = self.metadata["source"]
            art_mode = self.metadata["art_mode"]
        band = self.layout.top_band
        text_color = self.theme.text_color

        # Active source as plain centred text near the top of the circle.
        label = textformat.source_label(source, art_mode)
        lw, lh, ltop = self._measure(label, self.font_clock_status)
        lx = (self.width - lw) // 2
        ly = band.cy - lh // 2 - ltop
        self._draw_text_with_shadow(draw, (lx, ly), label, self.font_clock_status, text_color)

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
        draw.text(
            (left, cy - th // 2 - ttop), tag, font=self.font_small, fill=self.theme.text_color
        )
        draw.text(
            (right - pw, cy - ph // 2 - ptop),
            pct_text,
            font=self.font_small,
            fill=self.theme.text_color,
        )

        bar_h = self.theme.osd_bar_height
        bar_x0 = left + tw + 8
        bar_x1 = right - pw - 8
        bar_y0 = cy - bar_h // 2
        bar_y1 = bar_y0 + bar_h
        radius = bar_h // 2
        if bar_x1 - bar_x0 > 2 * radius:
            draw.rounded_rectangle(
                (bar_x0, bar_y0, bar_x1, bar_y1), radius=radius, fill=self.theme.osd_track_color
            )
            fill_w = int(round((bar_x1 - bar_x0) * pct / 100))
            if fill_w >= 2 * radius:
                draw.rounded_rectangle(
                    (bar_x0, bar_y0, bar_x0 + fill_w, bar_y1),
                    radius=radius,
                    fill=self.theme.osd_fill_color,
                )
            elif fill_w > 0:
                draw.rectangle(
                    (bar_x0, bar_y0, bar_x0 + fill_w, bar_y1), fill=self.theme.osd_fill_color
                )

    def _draw_image(self, draw: ImageDraw.ImageDraw) -> Optional[Image.Image]:
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
        thickness = (
            self.theme.osd_ring_thickness
            if self.theme.osd_ring_thickness > 0
            else max(4, self.theme.osd_bar_height)
        )
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
                overlay = Image.new(
                    "RGBA", (frame.width * scale, frame.height * scale), (0, 0, 0, 0)
                )
                odraw = ImageDraw.Draw(overlay)
                sx, sy = cx * scale, cy * scale
                srr = rr * scale
                obox = (sx - srr, sy - srr, sx + srr, sy + srr)
                fill_rgba = self._as_rgba(self.theme.osd_fill_color)
                odraw.arc(obox, start_deg, fill_deg, fill=fill_rgba, width=thickness * scale)
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
                smooth = overlay.resize((frame.width, frame.height), Image.Resampling.LANCZOS)
                frame.paste(smooth, (0, 0), smooth)
            else:
                # No overlay handle (defensive) or supersampling disabled:
                # draw straight onto the frame with centred caps.
                box = (cx - rr, cy - rr, cx + rr, cy + rr)
                draw.arc(box, start_deg, fill_deg, fill=self.theme.osd_fill_color, width=thickness)
                for angle_deg in (start_deg, fill_deg):
                    rad = math.radians(angle_deg)
                    cap_x = cx + cap_center_r * math.cos(rad)
                    cap_y = cy + cap_center_r * math.sin(rad)
                    draw.ellipse(
                        (cap_x - cap_r, cap_y - cap_r, cap_x + cap_r, cap_y + cap_r),
                        fill=self.theme.osd_fill_color,
                    )

        # Percentage centred inside the ring, with a subtle shadow.
        pct_text = f"{pct}%"
        pw, ph, ptop = self._measure(pct_text, self.font_title)
        tx = cx - pw // 2
        ty = cy - ph // 2 - ptop
        draw.text((tx + 1, ty + 1), pct_text, font=self.font_title, fill=self.theme.shadow_color)
        draw.text((tx, ty), pct_text, font=self.font_title, fill=self.theme.text_color)
