# AirPlay source

The appliance is an AirPlay receiver based on **shairport-sync** (with **nqptp**
for AirPlay 2 timing). It advertises itself on the network, plays audio streamed
from Apple devices, and exposes now-playing metadata and cover art on the
display. Like every backend it implements the shared
[`MusicSource`](../lib/music_source.py) interface, so it participates in the
single-active-source arbitration (`doc/adding-a-music-source.md`).

## shairport-sync version (development branch)

The Buildroot image tracks the upstream shairport-sync **`development`** branch
instead of the stable release pinned by mainline Buildroot. The pinned commit is
set in [`buildroot/build.sh`](../buildroot/build.sh):

```sh
SHAIRPORT_SYNC_DEV_REPO="https://github.com/mikebrady/shairport-sync.git"
SHAIRPORT_SYNC_DEV_COMMIT="<development commit sha>"
```

`build.sh` clones that commit into a persistent cache directory (under
`BR2_DL_DIR`) and hands it to Buildroot as `SHAIRPORT_SYNC_OVERRIDE_SRCDIR`.
Buildroot then rsyncs that source into `output/build/shairport-sync-custom/` and
**skips its own download, extract and hash steps** for the package.

**Why not repoint the package's `_VERSION`/`_SITE`?** The mainline
`package/shairport-sync/shairport-sync.mk` is included *after* the external tree,
assigns `SHAIRPORT_SYNC_VERSION = 4.3.7` with a plain `=`, and finalises the
package with `$(eval $(autotools-package))` at its own end. Because Buildroot
snapshots some derived paths with immediate `:=` expansion and others with
recursive `=`, an `override` in `external.mk` yields an *inconsistent* package —
the source extracts into `shairport-sync-<commit>` while the build/stamp dir
stays `shairport-sync-4.3.7`, and the build fails (`tar: … No such file`).
`OVERRIDE_SRCDIR` sidesteps all of that: no tarball, no `.hash`, and the build
dir is the fixed `shairport-sync-custom`.

**Why the development branch:** shairport-sync's remote-control commands (Play /
Pause / volume) over the D-Bus, MPRIS and MQTT interfaces are, on the stable
branch, available only for **Classic AirPlay (AirPlay 1)** senders — they are
backed by the sender's DACP remote-control channel, which AirPlay 2 senders do
not advertise. The development branch adds **experimental** remote-control
support for **AirPlay 2** clients, which is what lets the appliance pause a modern
iPhone/Mac stream when another music source takes over — and is the foundation
for future playback controls in the web now-playing view.

**Bumping the pin:** change `SHAIRPORT_SYNC_DEV_COMMIT` in
[`buildroot/build.sh`](../buildroot/build.sh) to a newer `development` commit
deliberately, then rebuild and validate on-target (below). No other edit is
required — the checkout is refreshed to the new commit automatically, and the
`scripts/build_image.py` `--fast` path forwards the same override so a fast
rebuild never falls back to the mainline 4.3.7 package.

This is a **major** jump from stable 4.3.7 to the 5.6-dev line: config-file keys,
dependencies (e.g. JACK support was removed) and behaviour can change, so
re-check `lib/airplay_service/airplay.conf` parsing and the D-Bus interface after
every bump. Because it is an experimental branch on an A/B firmware appliance,
prefer a health-checked trial boot before accepting a new pin.

## Metadata delivery (why `get_plist_metadata = "no"`)

PiSonic reads now-playing metadata and cover art from shairport-sync's classic
**metadata FIFO** (`pipe_name = "/tmp/shairport-sync-metadata"`), parsed by
[`AirplayMetadataProcessor`](../lib/airplay_service/airplay_metadata_processor.py).

The 5.x development branch changed the default to **AirPlay-2 plist metadata**
(`diagnostics.get_plist_metadata = "yes"`). In that mode a modern sender delivers
title/artist/album and artwork as a binary plist that shairport-sync routes
**only to its D-Bus/MPRIS interface — it is never written to the FIFO**. The pipe
reader therefore receives nothing and the display/web now-playing view goes
blank, even though audio still plays. This is controlled by the advertised
AirPlay feature bits: `get_plist_metadata = "yes"` sets **bit 50** (plist
metadata); `"no"` sets **bits 15/16/17** (classic artwork/progress/text) which
route metadata through the FIFO exactly as on stable 4.3.7.

`airplay.conf` therefore pins `diagnostics.get_plist_metadata = "no"`. Upstream
marks this option *Deprecated*, so a future pin bump could remove it — at which
point the reader must be migrated to consume metadata from the D-Bus/MPRIS
interface the service already connects to. **Re-verify this after every pin bump.**

## Source switching (stopping AirPlay)

AirPlay cannot always be stopped by a single remote command, so the app uses a
**layered stop** in `AirplayService.set_play_state(False)`:

1. **Best-effort remote control.** If shairport-sync reports the RemoteControl
   interface as `Available` (a DACP channel exists — always for Classic AirPlay,
   and for AirPlay 2 with the development branch), the app calls the
   `Pause` method on `org.gnome.ShairportSync.RemoteControl`, pausing the sender.
2. **Guaranteed local inhibit fallback.** The app always creates a local marker
   (`/run/airplay-inhibited`, configurable via `[airplay] inhibit_file` or
   `RADIO_AIRPLAY_INHIBIT_FILE`). While the marker exists, `get_play_state()`
   reports `False`, so source arbitration stops treating AirPlay as the active
   source even if the sender ignored the remote command. The marker is cleared
   automatically once the AirPlay session itself ends (shairport's `PlayerState`
   leaves `Playing`), so a later AirPlay stream can take over normally.

This mirrors the USB Audio source's inhibit-marker approach
([usb-audio.md](usb-audio.md#source-switching)): a remote pause is preferred, but
a source switch is always guaranteed. Pressing a radio preset therefore reliably
switches away from AirPlay (`RadioController.handle_button_press` stops the other
sources immediately rather than waiting for the next arbitration tick).

## On-target validation

```sh
# The RemoteControl interface and its Available/PlayerState properties
busctl --system introspect org.gnome.ShairportSync /org/gnome/ShairportSync

# While an Apple device is streaming, confirm remote control is offered
busctl --system get-property org.gnome.ShairportSync /org/gnome/ShairportSync \
    org.gnome.ShairportSync.RemoteControl Available
busctl --system get-property org.gnome.ShairportSync /org/gnome/ShairportSync \
    org.gnome.ShairportSync.RemoteControl PlayerState

# The local inhibit fallback marker (present only while another source has won)
cat /run/airplay-inhibited 2>/dev/null
```

For an AirPlay 2 sender, verify `Available` becomes `true` on the development
branch and that pressing a preset (or starting another source) actually pauses
the phone. If `Available` is `false` for a given sender, the inhibit marker still
guarantees the source switch — the appliance goes silent for AirPlay even though
the sender keeps its session open.

## Troubleshooting

- **`Available` is `false` for an AirPlay 2 sender:** remote pause is not offered
  by that sender/build; the inhibit fallback still stops local AirPlay audio.
  Confirm the image was built from the pinned `development` commit.
- **AirPlay resumes immediately after switching away:** ensure the inhibit marker
  is writable (its directory exists) and that `get_play_state()` sees the marker;
  a read-only `/run` would defeat the fallback.
- **No metadata / cover art:** unrelated to source switching. On the 5.x
  development branch this is almost always the plist-vs-classic metadata default:
  ensure `diagnostics.get_plist_metadata = "no"` in
  `lib/airplay_service/airplay.conf` (see
  [Metadata delivery](#metadata-delivery-why-get_plist_metadata--no) above) so
  metadata is written to the FIFO the app reads, then confirm the FIFO
  configuration (`enabled`, `include_cover_art`, `pipe_name`).
