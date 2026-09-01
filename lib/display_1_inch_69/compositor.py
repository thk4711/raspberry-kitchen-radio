# compositor.py
"""Pure, hardware-free pixel-math helpers for the full-frame compositor.

Workstream 1 of the display redesign moves rendering from per-band partial
``ShowWindow`` writes to a single 240x280 full-frame compositor. This module
holds the numpy-vectorised building blocks that composition needs:

* :func:`vertical_gradient` / :func:`solid_rgb` — background fills.
* :func:`alpha_blend` / :func:`apply_scrim` — compositing an overlay (or a flat
  darkening band) over the art layer so chrome text stays legible over any
  image (the "scrims" from Workstream 2).
* :func:`dominant_color` — the average colour of an image (optionally over its
  opaque pixels only) that drives the radio-mode backdrop (Workstream 2).
* :func:`pack_rgb565` — RGB888 -> big-endian RGB565 packing matching the
  ST7789 driver's own encoder, exposed here as a testable pure function.

Everything here operates on ``numpy.ndarray`` buffers (``uint8`` HxWx3 for
colour, ``uint8`` HxW for alpha masks) and releases the GIL inside numpy, so
the heavy math can use spare cores on the 4-core Pi 3A+. None of these
functions touch the panel, Pillow, GPIO or SPI, which keeps them unit-testable
on a plain workstation / CI (no Raspberry Pi attached), per the redesign plan.

Design constraints honoured here:

* No new runtime dependencies — numpy only (already on the image).
* No I/O, no logging, no global state — pure functions.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

# A colour is an ``(R, G, B)`` triple of 0..255 ints.
Color = Tuple[int, int, int]


def _as_rgb_array(color: Sequence[int]) -> np.ndarray:
    """Return ``color`` as a clipped ``uint8`` array of shape ``(3,)``."""
    arr = np.asarray(color, dtype=np.int32)
    if arr.shape != (3,):
        raise ValueError(f"color must be an (R, G, B) triple, got shape {arr.shape}")
    return np.clip(arr, 0, 255).astype(np.uint8)


def solid_rgb(width: int, height: int, color: Sequence[int]) -> np.ndarray:
    """Build an ``HxWx3`` ``uint8`` buffer filled with a single colour.

    Args:
        width: Buffer width in pixels.
        height: Buffer height in pixels.
        color: ``(R, G, B)`` fill colour, each channel 0..255.

    Returns:
        ``numpy.ndarray`` of shape ``(height, width, 3)`` and dtype ``uint8``.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    rgb = _as_rgb_array(color)
    buf = np.empty((height, width, 3), dtype=np.uint8)
    buf[:, :] = rgb
    return buf


def vertical_gradient(
    width: int,
    height: int,
    top_color: Sequence[int],
    bottom_color: Sequence[int],
) -> np.ndarray:
    """Build an ``HxWx3`` buffer interpolating top->bottom down the Y axis.

    Used for the radio-mode dominant-colour backdrop (Workstream 2). The
    interpolation is computed once per row and broadcast across the width, so
    cost scales with ``height`` not ``height*width``.

    Args:
        width: Buffer width in pixels.
        height: Buffer height in pixels.
        top_color: ``(R, G, B)`` colour at ``y = 0``.
        bottom_color: ``(R, G, B)`` colour at ``y = height - 1``.

    Returns:
        ``numpy.ndarray`` of shape ``(height, width, 3)`` and dtype ``uint8``.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    top = _as_rgb_array(top_color).astype(np.float32)
    bottom = _as_rgb_array(bottom_color).astype(np.float32)

    # t goes 0..1 down the rows; a single row of height==1 stays at the top.
    if height == 1:
        t = np.zeros((1,), dtype=np.float32)
    else:
        t = np.linspace(0.0, 1.0, height, dtype=np.float32)
    # (height, 3) column of interpolated colours, then broadcast over width.
    rows = top[None, :] + (bottom - top)[None, :] * t[:, None]
    rows = np.clip(np.rint(rows), 0, 255).astype(np.uint8)
    return np.broadcast_to(rows[:, None, :], (height, width, 3)).copy()


def alpha_blend(
    base: np.ndarray,
    overlay: np.ndarray,
    alpha: np.ndarray,
) -> np.ndarray:
    """Alpha-composite ``overlay`` over ``base`` using a per-pixel ``alpha``.

    ``out = base*(1-a) + overlay*a`` computed in ``uint16`` to avoid overflow,
    then rounded back to ``uint8``. All three inputs must share the same
    ``HxW`` footprint; ``base``/``overlay`` are ``HxWx3`` colour buffers and
    ``alpha`` is an ``HxW`` mask of 0..255 (0 = keep base, 255 = full overlay).

    Args:
        base: Background ``HxWx3`` ``uint8`` buffer.
        overlay: Foreground ``HxWx3`` ``uint8`` buffer.
        alpha: ``HxW`` ``uint8`` opacity mask for ``overlay``.

    Returns:
        A new ``HxWx3`` ``uint8`` buffer (inputs are not mutated).
    """
    if base.shape != overlay.shape or base.ndim != 3 or base.shape[2] != 3:
        raise ValueError("base and overlay must be identical HxWx3 buffers")
    if alpha.shape != base.shape[:2]:
        raise ValueError("alpha must be an HxW mask matching base")

    a = alpha.astype(np.uint16)[:, :, None]  # (H, W, 1) broadcast over channels
    inv = np.uint16(255) - a
    b = base.astype(np.uint16)
    o = overlay.astype(np.uint16)
    # +127 gives round-to-nearest instead of truncation when dividing by 255.
    out = (b * inv + o * a + 127) // 255
    return out.astype(np.uint8)


def apply_scrim(
    base: np.ndarray,
    top: int,
    bottom: int,
    color: Sequence[int] = (0, 0, 0),
    opacity: float = 0.55,
) -> np.ndarray:
    """Darken a horizontal band of ``base`` so overlaid text stays legible.

    A "scrim" is a semi-opaque flat band (top/bottom chrome strips in the
    redesign). Rows ``[top, bottom)`` are blended toward ``color`` at
    ``opacity``; rows outside the band are untouched.

    Args:
        base: The ``HxWx3`` ``uint8`` art buffer to darken (copied, not mutated).
        top: First row of the band (inclusive), clamped to the buffer.
        bottom: Row just past the band (exclusive), clamped to the buffer.
        color: Scrim colour to blend toward (default black).
        opacity: Blend strength 0.0 (no-op) .. 1.0 (solid ``color``).

    Returns:
        A new ``HxWx3`` ``uint8`` buffer (``base`` is not mutated).
    """
    if base.ndim != 3 or base.shape[2] != 3:
        raise ValueError("base must be an HxWx3 buffer")
    height = base.shape[0]
    top = max(0, min(int(top), height))
    bottom = max(0, min(int(bottom), height))
    out = base.copy()
    if bottom <= top or opacity <= 0.0:
        return out

    a = float(np.clip(opacity, 0.0, 1.0))
    scrim = _as_rgb_array(color).astype(np.float32)
    band = out[top:bottom].astype(np.float32)
    blended = band * (1.0 - a) + scrim[None, None, :] * a
    out[top:bottom] = np.clip(np.rint(blended), 0, 255).astype(np.uint8)
    return out


def dominant_color(
    rgb: np.ndarray,
    alpha: Optional[np.ndarray] = None,
    alpha_threshold: int = 16,
) -> Color:
    """Return the representative ``(R, G, B)`` colour of an image.

    Implements the plan's "downscale-to-1px" idea as a mean over pixels: the
    average colour of an image is what you get by shrinking it to a single
    pixel. When an ``alpha`` mask is supplied (e.g. a transparent station
    logo), only pixels with ``alpha > alpha_threshold`` are averaged so the
    huge transparent margins of the 160x120 logos do not wash the hue toward
    black. Falls back to the unmasked mean when an image is fully transparent.

    Args:
        rgb: ``HxWx3`` ``uint8`` colour buffer.
        alpha: Optional ``HxW`` ``uint8`` opacity mask.
        alpha_threshold: Pixels with alpha at or below this are ignored.

    Returns:
        A clipped ``(R, G, B)`` int triple.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb must be an HxWx3 buffer")
    flat = rgb.reshape(-1, 3).astype(np.float64)

    if alpha is not None:
        if alpha.shape != rgb.shape[:2]:
            raise ValueError("alpha must be an HxW mask matching rgb")
        mask = alpha.reshape(-1) > alpha_threshold
        if mask.any():
            flat = flat[mask]

    mean = flat.mean(axis=0)
    rounded = np.clip(np.rint(mean), 0, 255).astype(int)
    return (int(rounded[0]), int(rounded[1]), int(rounded[2]))


def scale_color(color: Sequence[int], factor: float) -> Color:
    """Return ``color`` multiplied by ``factor`` and clipped to 0..255.

    Used to derive the lighter top / darker bottom shades of the radio-mode
    dominant-colour gradient from a single dominant colour.

    Args:
        color: ``(R, G, B)`` triple.
        factor: Multiplier (``>1`` brightens, ``<1`` darkens).

    Returns:
        A clipped ``(R, G, B)`` int triple.
    """
    arr = _as_rgb_array(color).astype(np.float64) * float(factor)
    rounded = np.clip(np.rint(arr), 0, 255).astype(int)
    return (int(rounded[0]), int(rounded[1]), int(rounded[2]))


def horizontal_edge_fade(
    base: np.ndarray,
    region: np.ndarray,
    left_px: int,
    right_px: int,
) -> np.ndarray:
    """Blend ``region`` over ``base`` with faded left/right edges.

    Motion polish for scrolling text (Workstream 4.3): instead of a scrolling
    row hard-clipping where it meets the safe-area edge, its outermost
    ``left_px`` / ``right_px`` columns fade linearly into the background so the
    text appears to slide under a soft mask rather than being chopped.

    The interior columns are copied from ``region`` verbatim (alpha 1.0); the
    edge columns ramp their alpha from 0 at the very edge to 1 just inside, so a
    glyph entering or leaving the row dissolves smoothly.

    Args:
        base: ``HxWx3`` ``uint8`` background the region sits on (e.g. a slice of
            the composed art layer). Not mutated.
        region: ``HxWx3`` ``uint8`` foreground of the same shape as ``base``
            (the rendered text row).
        left_px: Width in pixels of the left fade ramp (clamped to the width).
        right_px: Width in pixels of the right fade ramp (clamped to the width).

    Returns:
        A new ``HxWx3`` ``uint8`` buffer of ``region`` composited over ``base``
        with the edge alpha ramps applied.
    """
    if base.shape != region.shape or base.ndim != 3 or base.shape[2] != 3:
        raise ValueError("base and region must be matching HxWx3 buffers")
    height, width = base.shape[:2]
    left = max(0, min(int(left_px), width))
    right = max(0, min(int(right_px), width))

    alpha = np.ones(width, dtype=np.float32)
    if left > 0:
        # 0 at column 0 rising toward 1 just inside the ramp.
        alpha[:left] = np.linspace(0.0, 1.0, left, endpoint=False, dtype=np.float32)
    if right > 0:
        # Mirror of the left ramp: ~1 just inside falling to 0 at the outer edge.
        ramp = np.linspace(0.0, 1.0, right, endpoint=False, dtype=np.float32)[::-1]
        alpha[width - right:] = ramp

    a = alpha[None, :, None]  # broadcast over rows and channels
    blended = region.astype(np.float32) * a + base.astype(np.float32) * (1.0 - a)
    return np.clip(np.rint(blended), 0, 255).astype(np.uint8)


def pack_rgb565(rgb: np.ndarray) -> bytes:
    """Pack an ``HxWx3`` RGB888 buffer to big-endian RGB565 bytes.

    Byte layout matches the ST7789 driver's ``_encode_rgb565`` so a composed
    frame can be handed straight to ``writebytes2``. Kept here as a pure
    function so the packing math is unit-testable without the SPI stack.

    Args:
        rgb: ``HxWx3`` ``uint8`` buffer (R, G, B channels).

    Returns:
        ``bytes`` of length ``H * W * 2`` in big-endian RGB565 order.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb must be an HxWx3 buffer")
    img = rgb.astype(np.uint8)
    height, width = img.shape[:2]
    pix = np.zeros((height, width, 2), dtype=np.uint8)
    # High byte: RRRRR GGG ; low byte: GGG BBBBB (big-endian RGB565).
    pix[..., 0] = (img[..., 0] & 0xF8) | (img[..., 1] >> 5)
    pix[..., 1] = ((img[..., 1] << 3) & 0xE0) | (img[..., 2] >> 3)
    return pix.tobytes()

