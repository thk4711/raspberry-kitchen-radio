# Station logos: how they're rendered, customized, and added

This document explains how the radio turns a preset station's logo into the art
on the SPI display, what you can customize, and how to prepare and add your own
logos. For the station list itself (URLs, button order) see
[`stations.md`](stations.md); for the licensing/provenance of the *vendored*
logos and their checksums see [`assets.md`](assets.md).

## Where logos live

Preset station logos are ordinary image files under
[`lib/mpd_service/logos/`](../lib/mpd_service/logos/). Each preset in
[`lib/mpd_service/stations.conf`](../lib/mpd_service/stations.conf) references one
by filename:

```ini
[MDR AKTUELL]
url  = http://mdr-284340-0.cast.mdr.de/mdr/284340/0/mp3/high/stream.mp3
logo = MDR_AKTUELL.png          ; resolved under lib/mpd_service/logos/
name = MDR Aktuell
```

The whole `lib/` tree (fonts and logos included) is copied onto the Buildroot
appliance image as-is, so on a running radio the logos live at
`/opt/raspberry-kitchen-radio/lib/mpd_service/logos/`. **Nothing is fetched from
the internet at runtime** — the display always reads the logo from local disk.

## How a logo becomes screen art

A logo only appears in **radio mode** — i.e. when an internet-radio (MPD) station
is playing. Spotify/AirPlay instead show their real album cover full-bleed
(that is the `cover` art mode, chosen per source in
[`radio.py`](../radio.py); `art_mode` is deliberately *not* themeable).

The path from `stations.conf` to pixels:

1. **Resolve the file.** `MPDService.get_metadata()`
   ([`lib/mpd_service/mpd_service.py`](../lib/mpd_service/mpd_service.py)) turns
   the `logo=` filename into an absolute path
   (`…/lib/mpd_service/logos/<logo>`) and reports it as the track's cover.
2. **Pick the art treatment.** `Radio.update_metadata()` in `radio.py` tags MPD
   playback with `art_mode="radio"` and forwards the path to the display.
3. **Compose the frame.** `DisplayController`
   ([`lib/display_1_inch_69/display_control.py`](../lib/display_1_inch_69/display_control.py))
   builds a cached "art layer":
   - `_open_cover()` loads the logo as RGBA (a missing/unreadable file degrades
     gracefully — see the fallback below).
   - `_radio_backdrop()` samples the logo's **dominant colour over its opaque
     pixels** and paints a full-screen vertical gradient from it, then blends in
     a blurred, enlarged copy of the logo so the background reads as *branded*
     rather than flat.
   - `_scale_logo_to_box()` fits the logo (aspect-preserved, up to ~90% of the
     centred safe-area box) and pastes it in the middle using its own alpha.
   - Darkened top/bottom "scrim" bands are applied so the status strip and the
     title/artist text stay legible over any art.

### Why the backdrop is derived from the logo — and kept darker than it

Because the backdrop colour is **sampled from the logo itself**, a naive
gradient at the same brightness as the logo makes the logo melt into the
background — most station logos are bright, so a bright wash around a bright tile
has almost no edge contrast. The radio therefore builds the backdrop
**deliberately darker than the (usually bright) logo**, so the centred tile
stands out. This is tunable (see [Customizing the backdrop](#customizing-the-backdrop-via-ui)).

## Preparing a good logo

The display fits each logo into a centred box and samples it for the backdrop
colour, so the following renders best:

- **Format:** PNG (RGBA). A JPEG works too — the app converts to RGBA on load —
  but the `logo=` filename should still match the file you drop in.
- **Shape/size:** **square-ish and no larger than ~512×512.** The shipped logos
  are 512×512; that is a good template. Small logos are upscaled to fill the box,
  so start from something reasonably crisp.
- **Background — this is the contrast lever:** an **opaque, solid brand-colour
  tile** (like the Deutschlandfunk and MDR tiles that ship) reads best, because
  the tile carries its own internal contrast (e.g. white lettering on a colour
  block) *and* gives a clean colour for the backdrop to sample. A **transparent
  wordmark** (just the letters, transparent around them) is the classic failure
  case: the backdrop is sampled from those same letter pixels, so the wash ends
  up the same hue as the letters and the logo washes out. If you only have a
  transparent wordmark, place it on a solid brand-colour square before saving.

A handy way to normalize an arbitrary image onto a square, opaque tile (Pillow,
which the app already uses):

```python
from PIL import Image
CANVAS = 512
src = Image.open("my-logo.png").convert("RGB")   # or .jpg
src.thumbnail((int(CANVAS * 0.95), int(CANVAS * 0.95)), Image.Resampling.LANCZOS)
bg = (20, 20, 24)                                  # your brand background colour
canvas = Image.new("RGBA", (CANVAS, CANVAS), bg + (255,))
canvas.paste(src.convert("RGBA"),
             ((CANVAS - src.width) // 2, (CANVAS - src.height) // 2))
canvas.save("MY_STATION.png", "PNG")
```

## Adding or replacing a logo

1. Put the image in `lib/mpd_service/logos/` (e.g. `MY_STATION.png`).
2. Reference it from the station's section in `lib/mpd_service/stations.conf`
   with `logo = MY_STATION.png`.
3. Apply it:
   - **On a running target:** copy the file over (the appliance's SSH server has
     no SFTP subsystem, so use legacy-protocol scp):
     ```bash
     scp -O MY_STATION.png root@<radio-ip>:/opt/raspberry-kitchen-radio/lib/mpd_service/logos/
     ssh root@<radio-ip> /etc/init.d/S90radio restart
     ```
     The restart makes the app re-read `stations.conf` and reload the art.
   - **For the built image:** just add the files to the repo and rebuild/reflash;
     `radio-app.mk` ships everything under `lib/` via `cp -a`, so no build change
     is needed.
4. If the logo is a **vendored** asset you intend to commit, refresh its SHA-256
   and provenance row in [`assets.md`](assets.md)
   (`shasum -a 256 lib/mpd_service/logos/MY_STATION.png`).

## The generated fallback tile

A station whose `logo=` is **blank or points at a missing file** does not show an
empty screen. The display renders a **generated initials tile** via
[`lib/display_1_inch_69/logo_fallback.py`](../lib/display_1_inch_69/logo_fallback.py):
a rounded square in a deterministic, name-derived colour with the station's
initials (e.g. `Jazz Radio` → `JR`, `MDR JUMP` → `MJ`). The colour is a stable
hash of the name, so a given station always looks the same and the art cache
stays valid. This means you can add a station first and drop in a real logo
later. (All shipped presets have a real logo, so the tile is only seen for
user-added logo-less stations.)

The **Bluetooth** source is a special case of the same fallback: it never
carries cover art, so instead of name-derived initials it renders a fixed
**Bluetooth glyph on a muted-blue tile** (`render_bluetooth_tile`), keyed off the
active source name in `DisplayController._fallback_logo()`. The glyph is the
official Bluetooth mark: the public-domain `Bluetooth.svg` is a single stroked
polyline, so its exact path is transcribed (`logo_fallback._BT_PATH`) and stroked
with Pillow — reproducing the logo faithfully with no SVG rasteriser (none is on
the appliance image) and no new dependency.

## Customizing the backdrop via `[ui]`

The radio-mode backdrop is themeable through the optional `[ui]` section of
[`lib/display_1_inch_69/display.conf`](../lib/display_1_inch_69/display.conf).
Every key is optional; leaving it out keeps the shipped default. The keys that
affect the logo/backdrop:

| Key | Default | Effect |
| --- | --- | --- |
| `backdrop_top_scale` | `0.55` | Backdrop **top** row = `logo_dominant_colour × this`. Lower ⇒ darker top ⇒ **more contrast** with the (bright) logo. |
| `backdrop_bottom_scale` | `0.20` | Backdrop **bottom** row multiplier (the gradient fades down to this). |
| `backdrop_logo_blend` | `0.20` | How much of the blurred logo is mixed into the backdrop. Higher ⇒ the background looks more like a big blurry copy of the tile ⇒ **less** edge contrast. |
| `backdrop_blur` | `18` | Gaussian blur radius of that blurred-logo layer. |
| `scrim_opacity` | `0.55` | Darkening of the top/bottom chrome bands over the art. |
| `no_art_color` | `32, 32, 40` | Neutral backdrop colour used when a logo can't be colour-sampled. |

**Rule of thumb:** most station logos are *bright*, so **lower** top/bottom
scales (a darker backdrop) increase contrast. Raising the scales toward `1.0`+
brightens the background and *reduces* contrast — useful only if your logos are
dark. Colours accept `#RRGGBB`, an `r, g, b` triple, or the names `WHITE`/`BLACK`;
a malformed value is ignored (the default is used) and never blocks boot.

Example — a slightly brighter, lower-contrast look, tuned live on the target:

```ini
[ui]
backdrop_top_scale = 0.75
backdrop_bottom_scale = 0.30
```

```bash
# edit /opt/raspberry-kitchen-radio/lib/display_1_inch_69/display.conf, then:
ssh root@<radio-ip> /etc/init.d/S90radio restart
```

## Licensing

The bundled station logos are broadcaster trademarks included only to identify
the shipped preset stations (the Deutschlandfunk marks are additionally CC0 on
Wikimedia Commons). If you redistribute your build, replace them with your own
stations' assets. See [`assets.md`](assets.md) for provenance and checksums.

## See also

- [`stations.md`](stations.md) — add/edit the preset stations and button order.
- [`assets.md`](assets.md) — vendored-asset policy, provenance, licensing, checksums.
- [`display-test.md`](display-test.md) — standalone display smoke test.


