# Asset management policy

This project **vendors** (commits) a small number of static, non-code assets so
the radio works out of the box — no extra download step is required to build the
[Buildroot appliance image](buildroot.md).

The media backends (shairport-sync, nqptp, go-librespot) are **not** vendored:
Buildroot builds them from source and the app finds them on `PATH`. The repo
therefore ships **no third-party binaries** — only the two asset groups below.

## Vendored artifacts

| Artifact | Path | Size | Role |
| --- | --- | --- | --- |
| UI font (regular) | `lib/display_1_inch_69/fonts/Roboto-Condensed-Regular.ttf` | ~48 KB | display body text |
| UI font (bold) | `lib/display_1_inch_69/fonts/Roboto-Condensed-Bold.ttf` | ~300 KB | display title / status-strip text |
| Station logos | `lib/mpd_service/logos/*.png` | ~144 KB total (6 files) | cover art for the preset radio stations |

Both are copied onto the Buildroot image as-is (`cp -a lib/.` in
`buildroot/external/package/radio-app/radio-app.mk`) and loaded at runtime by
the display and MPD services.

> **No runtime network dependency.** Every logo (and font) is vendored — committed
> to this repo and baked into the image — and read from local disk at runtime
> (`/opt/raspberry-kitchen-radio/lib/...`). The running radio never fetches artwork
> from the internet. The source URLs below are for **provenance and one-time
> regeneration only**.

## Provenance & licensing

These assets are **not** covered by this repository's MIT license — each is
governed by its own upstream license/terms.

| Artifact | Path | Upstream | License | Source URL |
| --- | --- | --- | --- | --- |
| UI font (regular) | `lib/display_1_inch_69/fonts/Roboto-Condensed-Regular.ttf` | Roboto Condensed (Google Fonts) | Apache-2.0 | https://fonts.google.com/specimen/Roboto+Condensed |
| UI font (bold) | `lib/display_1_inch_69/fonts/Roboto-Condensed-Bold.ttf` | Roboto (classic) v2.138 static build | Apache-2.0 | https://github.com/googlefonts/roboto-2/releases/tag/v2.138 (`roboto-android.zip` → `RobotoCondensed-Bold.ttf`) |
| Logo `Deutschlandfunk.png` | `lib/mpd_service/logos/Deutschlandfunk.png` | Deutschlandfunk (Deutschlandradio) | CC0 1.0 (public domain) | https://commons.wikimedia.org/wiki/File:Deutschlandfunk_Logo_klein.png |
| Logo `Deutschlandfunk_Nova.png` | `lib/mpd_service/logos/Deutschlandfunk_Nova.png` | Deutschlandfunk Nova (Deutschlandradio) | CC0 1.0 (public domain) | https://commons.wikimedia.org/wiki/File:Deutschlandfunk_Nova_Logo_klein.png |
| Logo `Deutschlandfunk_Kultur.png` | `lib/mpd_service/logos/Deutschlandfunk_Kultur.png` | Deutschlandfunk Kultur (Deutschlandradio) | CC0 1.0 (public domain) | https://commons.wikimedia.org/wiki/File:Deutschlandfunk_Kultur_Logo_klein.png |
| Logo `MDR_AKTUELL.png` | `lib/mpd_service/logos/MDR_AKTUELL.png` | MDR Aktuell (Mitteldeutscher Rundfunk) | Trademark (identification only) | MDR brand tile (`mdr-aktuell.44188984.png`) |
| Logo `MDR_JUMP.png` | `lib/mpd_service/logos/MDR_JUMP.png` | MDR Jump (Mitteldeutscher Rundfunk) | PD-textlogo + trademark (identification only) | https://commons.wikimedia.org/wiki/File:MDR_JUMP_Logo.svg |
| Logo `MDR_KULTUR.png` | `lib/mpd_service/logos/MDR_KULTUR.png` | MDR Kultur (Mitteldeutscher Rundfunk) | Trademark (identification only) | MDR brand tile (`mdr-kultur.df884089.jpg`) |

The DLF logos are downscaled to 512×512 from the CC0 1,920×1,920 “Logo klein”
uploads. The MDR Aktuell/Kultur logos are **opaque, solid-background brand
tiles** (like the DLF marks) fit and centred on a 512×512 canvas — chosen over
transparent wordmarks because the display samples the logo's dominant colour for
the backdrop, so a same-colour wordmark washes into the background and reads with
almost no contrast. The MDR JUMP logo (rasterized from its Commons SVG via the
Wikimedia thumbnailer) is fit and centred on a 512×512 transparent canvas. A
preset with a blank/missing `logo=` renders a generated initials tile instead
(see “Generated logo fallback” below).

**Station logos** are broadcaster trademarks included only for identification of
the preset stations (the DLF marks additionally carry a CC0 dedication on
Commons). Replace them with your own stations' assets if you redistribute.

### SHA-256 checksums

Regenerate with `shasum -a 256 <path>` after replacing an artifact.

| Path | SHA-256 |
| --- | --- |
| `lib/display_1_inch_69/fonts/Roboto-Condensed-Regular.ttf` | `68f2c3495f17f27659df0ef3b5ce42642f40e337c5b3adc19cc07f3c5e520f5e` |
| `lib/display_1_inch_69/fonts/Roboto-Condensed-Bold.ttf` | `7f109b2b6d72e7563522d3c3d2c6c8b79ec5a711bfdf483b93e345eab5b5ef94` |
| `lib/mpd_service/logos/Deutschlandfunk.png` | `4e2410e1b96a69691c7771680c0009fae439477e572158a2cf61ed557af05c73` |
| `lib/mpd_service/logos/Deutschlandfunk_Kultur.png` | `8ddf5538d2f08709d71e29497fb7d0a2088f512a9f6521b6dd1903d7d771abc5` |
| `lib/mpd_service/logos/Deutschlandfunk_Nova.png` | `5d96b086d24e4d6bbff15abafaee12be193a205733cfd27715d95281c5b52d71` |
| `lib/mpd_service/logos/MDR_AKTUELL.png` | `80cad4d3afab5fe385efde9cec4cdb6abcd8b4058a4d84e97937770c95ac49de` |
| `lib/mpd_service/logos/MDR_JUMP.png` | `2bf1ecb9b1e051c63f08864c8494bf92d9ca584ad820bca204f60a17601a7204` |
| `lib/mpd_service/logos/MDR_KULTUR.png` | `d59a3b8d3b40cb79e249c84f1bf67212b94330fa2b11640d1aad85e62771d80f` |

## Replacing an artifact

- **Fonts** — the display loads two fixed filenames from
  `lib/display_1_inch_69/fonts/`: `Roboto-Condensed-Regular.ttf` (body) and
  `Roboto-Condensed-Bold.ttf` (title / status strip). To swap the typeface,
  replace those two files in place (keep the names), then refresh the checksums
  above. If the bold file is missing the app falls back to the regular weight.
- **Station logos** — transparent PNGs named to match the `logo=` entries in
  `lib/mpd_service/stations.conf`, resolved under `lib/mpd_service/logos/`. The
  display fits each logo (aspect-preserved) into a centred box and samples its
  dominant colour for the backdrop, so a **square-ish PNG that fits within
  ~512×512** works best; transparent backgrounds look cleanest, but a solid
  brand-colour background (like the DLF marks) also reads well as a tile. After
  replacing a logo, refresh its SHA-256 above. See [`logos.md`](logos.md) for how
  logos are rendered, how to prepare/add your own, and the `[ui]` backdrop knobs.

## Generated logo fallback

A station whose `logo=` is blank or points at a missing file does **not** show
an empty screen: the display renders a **generated initials tile** — a rounded
square in a deterministic, name-derived colour with the station's initials
(e.g. a station named “Jazz Radio” → “JR”) — via
`lib/display_1_inch_69/logo_fallback.py`. So adding a station without a logo is
fine; drop in a PNG later to replace the tile. (All six shipped presets have a
real logo, so the tile is only seen for user-added logo-less stations.)

## `.gitignore` note

These files are intentionally **not** ignored. `spotify.conf` is the one
generated-at-runtime exception and *is* ignored. See the comments at the top of
[`../.gitignore`](../.gitignore).
