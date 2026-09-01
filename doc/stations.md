# Adding & editing radio stations

The internet-radio presets are defined in
[`lib/mpd_service/stations.conf`](../lib/mpd_service/stations.conf) and loaded by
`MPDService` at start-up. Each station becomes one of the six front-panel preset
buttons.

## File format

`stations.conf` is a simple INI file. Each station is one section with three
keys:

```ini
[Deutschlandfunk]
url = https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3
logo = Deutschlandfunk.png
name = Deutschlandfunk
```

| Key | Meaning |
| --- | --- |
| section header `[...]` | Internal identifier for the station (must be unique). |
| `url` | The stream URL MPD plays. Any format MPD/`mpc` can play works (MP3, AAC, HLS, …). |
| `logo` | Filename of the cover image, resolved under `lib/mpd_service/logos/`. Leave blank to use a generated initials tile from `name`. |
| `name` | Human-readable name shown on the display. |

## Buttons map to file order (1–6)

`MPDService.__init__` reads the sections **in file order** into a list. The
station index is what the button ladder / `play_index(n)` selects:

- The **first** section → button **1**
- The **second** section → button **2**
- … up to button **6**.

The hardware button ladder (ADS1115 channel 1) reports buttons `1..6`; see the
ADS1115 channel map in [`hardware.md`](hardware.md). If you define more than six
stations only the first six are reachable from the buttons (the rest are still
loaded and can be selected programmatically). To change which station a button
selects, reorder the sections in `stations.conf`.

## Adding a station

1. Add a new section to `lib/mpd_service/stations.conf` with `url`, `logo` and
   `name`, placing it in the position (1–6) you want the button to select.
2. Drop the matching logo PNG into `lib/mpd_service/logos/` using the filename
   you set in `logo`. A **square-ish transparent PNG that fits within ~512×512**
   renders best (the display fits it into a centred box and samples it for the
   backdrop colour); the shipped files are a good template. **Optional:** leave
   `logo=` blank (or point at a missing file) and the display shows a generated
   initials tile from the station `name` instead — see [`assets.md`](assets.md).
3. Rebuild/reflash the image (or, on a running target, restart the radio with
   `/etc/init.d/S90radio restart`) so `MPDService` re-reads the config.

## Logos & licensing

The bundled station logos are broadcaster trademarks included only to identify
the shipped preset stations (the Deutschlandfunk marks are additionally CC0 on
Wikimedia Commons). If you redistribute your build, replace them with your own
stations' assets — see [`assets.md`](assets.md). A station with a blank/missing
`logo` shows a generated initials tile instead.

## See also

- [`logos.md`](logos.md) — how logos are rendered on the display, preparing/adding your own, the fallback tile, and the `[ui]` backdrop/contrast knobs.
- [`hardware.md`](hardware.md) — ADS1115 channel map & the button ladder.
- [`adding-a-music-source.md`](adding-a-music-source.md) — adding a whole new
  playback backend (AirPlay/Spotify-style) rather than just a station.
