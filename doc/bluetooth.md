# Bluetooth (A2DP) music source

PiSonic can act as a **Bluetooth audio receiver**: pair a phone or tablet and
its audio plays through the same I2S DAC as internet radio, AirPlay and Spotify.
Track title/artist appear on the display. This is the fourth `MusicSource`
backend (see [`adding-a-music-source.md`](adding-a-music-source.md)).

## How to use it

1. On your phone, open Bluetooth settings and look for PiSonic. Its name is
   the appliance **hostname** (set through `pisonic-config.txt` or Device settings;
   default `pisonic`).
2. Tap to connect. **There is no PIN and no confirmation prompt** — an
   auto-accept agent answers the pairing for you. (Your phone may briefly show a
   pairing dialog; you do not need to act on it.)
3. Start playing anything (music app, video, etc.). Within a couple of seconds
   PiSonic switches to the Bluetooth source: **whatever was playing before
   (internet radio, AirPlay, Spotify) is stopped**, the display shows the
   Bluetooth track, and audio comes out of the speaker.
4. When you stop/disconnect, PiSonic returns to pairing mode (see below) and
   another device can connect. Select a preset button or another source to go
   back to Internet Radio.

## Pairing mode (no PIN, connection-gated)

- **Whenever no device is connected**, the adapter is **discoverable and
  pairable with no PIN**, so any phone can connect without intervention. An
  auto-accept agent (`bluetoothctl agent auto`) confirms the pairing
  automatically, so even phones that use SSP *numeric comparison* pair without
  you tapping anything on PiSonic.
- **Once a device is fully paired and connected**, PiSonic stops advertising
  (it is not discoverable/pairable). The gating waits for the bond to *complete*
  (`Paired: yes` **and** `Connected: yes`) before turning pairability off, so it
  never aborts an in-flight pairing handshake. It automatically re-opens pairing
  mode as soon as that device disconnects or goes out of range.

> **Security note.** No-PIN, auto-accept + always-open-when-idle means *anyone
> in Bluetooth range can connect and pair* while nothing else is connected. This
> is a deliberate choice for a hands-off home audio appliance. If you need to
> restrict this, replace the `agent auto` / discoverable toggling in
> `S42bluetooth` with a `NoInputNoOutput` agent (and answer confirmations
> manually) or pair manually with `bluetoothctl`.

## What plays, and what shows on the display

- **Audio:** the A2DP stream is received by `bluealsa` and played to the ALSA
  `default` device (→ the `dmix` → I2S DAC path in `/etc/asound.conf`), exactly
  like every other source. Adjust volume with PiSonic's volume knob as usual.
- **Metadata:** the display shows the **title and artist** reported by the phone
  over AVRCP (`org.bluez.MediaPlayer1`). Bluetooth A2DP/AVRCP carries **no cover
  art**. PiSonic therefore immediately shows a generated placeholder tile: a
  **Bluetooth glyph on a muted-blue rounded square** (rendered by
  `logo_fallback.render_bluetooth_tile`), centred like a station logo with the
  title/artist text below it. If the optional online lookup described below is
  enabled, a confidently matched cover replaces that glyph asynchronously on
  both the display and web dashboard. The glyph is the Bluetooth mark from
  `radio_web/static/bluetooth-symbol.svg`, rasterised at build time into a PNG
  under `lib/display/glyphs/` by `scripts/render-source-glyphs.py` and tinted at
  runtime with Pillow (the appliance image ships no SVG rasteriser).
- **Auto-switching:** PiSonic stops the previously playing source when BlueZ's
  A2DP transport enters `pending` or `active`. AVRCP status is deliberately not
  used as the playback signal: phones can report their global media session as
  playing over AVRCP while sending that audio to AirPlay or another output.
  Track metadata and the Play/Pause/Previous/Next commands still use AVRCP.

## Optional online cover art

The **Display** page can enable **Fetch missing Bluetooth cover art from
MusicBrainz / Cover Art Archive**. It is disabled by default and takes effect
after **Apply and restart radio**. A usable artist and title must be supplied by
the phone/app over AVRCP, and the radio needs working internet access.

When enabled, PiSonic sends the artist, title, and possibly album to MusicBrainz,
selects only a high-confidence recording match, and downloads a bounded image
from Cover Art Archive or its expected Internet Archive host. No account or API
key is needed. Requests identify PiSonic with its version and project URL and
are paced to no more than one MusicBrainz request per second. Ambiguous matches,
missing artwork, provider errors, and offline operation leave the Bluetooth
glyph in place and never interrupt audio.

This is a privacy opt-in: the provider can observe the submitted track metadata,
request time, and radio's public IP address. Disable the setting to prevent these
artwork requests. Routine cache filenames are SHA-256 keys rather than track
names, and PiSonic's default artwork error messages do not log artist, title, or
album.

Downloaded images are normalized JPEGs stored only in RAM-backed `/tmp`:

- per-track cache: `/tmp/pisonic/artwork-cache/<sha256>.jpg`;
- currently published image: `/tmp/bluetooth_cover.jpg`;
- at most 32 positive files and 8 MiB total;
- at most 128 in-memory negative entries; a definite no-match/no-cover is retried
  after six hours, while transient failures use shorter backoff;
- all files and entries disappear on reboot and are excluded from backup.

Cover Art Archive availability does not imply that an image is freely licensed.
Album artwork may remain copyrighted and subject to third-party terms. Anyone
redistributing PiSonic commercially must review the current MusicBrainz, Cover
Art Archive, and Internet Archive usage, identification, rate-limit, caching,
and artwork-display terms rather than relying on this project's defaults.

## How it is built and wired (appliance image)

The whole stack is enabled in the Buildroot image; there is nothing to install
on the phone side beyond pairing.

### Firmware / kernel / packages

- **On-chip radio enabled — Bluetooth is on the PL011 UART (`ttyAMA0`).**
  `board/radio/config.txt` does **not** set `dtoverlay=pi3-miniuart-bt`; leaving
  that overlay off keeps Bluetooth on the full PL011 with hardware flow control.
  The kernel console stays on `tty1` (see `cmdline.txt`), so nothing else needs
  the PL011. The config also sets `enable_uart=1` so the PL011 comes up
  deterministically for the BT attach.
  > **Do not enable `pi3-miniuart-bt`.** It moves Bluetooth onto the weaker
  > *mini-UART* (`ttyS0`), which cannot reliably sustain the A2DP data rate —
  > streaming then produces continuous `ACL packet for unknown connection
  > handle` / `Unexpected continuation frame` kernel errors, loses ~half the
  > audio, and plays choppy/silent.
- **BT firmware.** `BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI_BT=y` installs the
  `brcm/*.hcd` patchram firmware (incl. `BCM43430A1.hcd`).
- **Kernel.** `board/radio/linux-bluetooth.fragment` adds `CONFIG_BT` + the BT
  UART HCI symbols (positive-only fragment, like the I2C/watchdog ones).
- **Userspace.** `bluez5_utils` (with the A2DP/AVRCP audio plugins + the
  `bluetoothctl` CLI client) and `bluez-alsa` (the `bluealsa` daemon +
  `bluealsa-aplay`).

### Services (who owns what)

| Component | Owner | Purpose |
| --- | --- | --- |
| `bluetoothd` | `/etc/init.d/S40bluetoothd` (bluez5_utils) | BlueZ stack + `org.bluez` D-Bus API. Started with `--experimental` via `/etc/default/bluetoothd` so AVRCP `MediaPlayer1` metadata is exposed. |
| auto-pairing agent, discoverable/pairable gating, `bluealsa`, `bluealsa-aplay` | `/etc/init.d/S42bluetooth` | Holds **one long-lived `bluetoothctl` session** (fed via the `/run/bluetooth-radio-btctl.fifo` control FIFO) that registers the auto-accept `agent auto` (no PIN, auto-confirms SSP numeric comparison) and sets the adapter alias to the hostname, toggles pairing mode by connection state (only closing once a device is fully **paired + connected**, so it never aborts an in-flight handshake), and receives A2DP + routes it to ALSA `default`. The session must stay alive: in BlueZ the agent and the `Pairable` state are scoped to the D-Bus client that set them, so a short-lived client would drop them the instant it exits. |
| Metadata + playback state/control | `radio.py` / `lib/bluetooth_service` | A pure **D-Bus consumer**: reads A2DP state from `org.bluez.MediaTransport1`, reads metadata from `org.bluez.MediaPlayer1`, and issues AVRCP controls. It does **not** launch any Bluetooth daemon. |

Config files: `/etc/bluetooth/main.conf` (adapter class + timeouts disabled so
`S42bluetooth` is authoritative over discoverable/pairable; the adapter *name*
shown to phones is the runtime `Alias`, set by `S42bluetooth` to the hostname —
the `%h` token in `main.conf`'s `Name` is not expanded by this BlueZ build),
`/etc/dbus-1/system.d/bluetooth-radio.conf` (D-Bus policy for `org.bluealsa`).

The Python side has a `[bluetooth]` section in `radio.conf`
(`dbus_service = org.bluez`).

## On-target validation & troubleshooting

SSH into PiSonic (`ssh root@<radio-ip>`), then:

```sh
# Daemons up?
ps | grep -E 'bluetoothd|bluealsa'
#   expect: bluetoothd, bluealsa, bluealsa-aplay

# Adapter powered, and (while idle) discoverable + pairable?
bluetoothctl show | grep -E 'Powered|Discoverable|Pairable'

# Who is connected?
bluetoothctl devices
bluetoothctl info <MAC> | grep -E 'Connected|Name'
```

Enable the (normally silent) init-script log to watch pairing-mode transitions:

```sh
/etc/init.d/S42bluetooth stop
S42BLUETOOTH_LOG=/tmp/S42bluetooth.log /etc/init.d/S42bluetooth start
cat /tmp/S42bluetooth.log
```

Common checks:

- **Radio not visible on the phone:** confirm a device is not already connected
  (pairing mode is off while connected — that is by design). `bluetoothctl show`
  should report `Discoverable: yes` **and** `Pairable: yes` when idle, with
  `Alias:` set to the hostname (e.g. `pisonic`). If `Pairable: no` while
  idle, the persistent auto-pairing session is not up: check that a single
  `bluetoothctl` process is running (`ps | grep bluetoothctl`) and that the
  control FIFO exists (`ls -l /run/bluetooth-radio-btctl.fifo`); restart with
  `S42BLUETOOTH_LOG=/tmp/S42bluetooth.log /etc/init.d/S42bluetooth restart` and
  inspect the log. If `Powered: no`, verify `S40bluetoothd` is running and
  `hci0` exists (`ls /sys/class/bluetooth`).
- **Pairing pops up a code and/or fails (`auth failed 0x05`):** if the phone was
  previously (half-)paired, it holds a stale link key. Do **"Forget This Device"**
  on the phone **and** `bluetoothctl remove <MAC>` on PiSonic, then pair fresh.
  PiSonic's auto-accept agent (`agent auto`) confirms SSP numeric comparison
  automatically, so no code needs tapping on PiSonic side.
- **Connects but no sound:** check `bluealsa-aplay` is running and the volume
  knob is up; confirm audio works from another source (same ALSA path).
- **Audio is choppy or drops to silence after ~1 s:** almost certainly the HCI
  **UART transport**. Confirm Bluetooth is on the **PL011**, not the mini-UART:
  `readlink -f /sys/class/bluetooth/hci0/device` should point at
  `…/3f201000.serial/…` (PL011). If it points at `…/3f215040.serial/…`
  (mini-UART), `pi3-miniuart-bt` is enabled — remove it from `config.txt` (see
  the firmware note above). Watch `dmesg | grep -icE 'continuation frame|unknown
  connection handle'` while streaming: it should stay ~flat.
- **No title/artist on the display:** the phone/app may not send AVRCP metadata;
  audio still plays and A2DP transport state still triggers source switching.
- **Bluetooth glyph never changes to a cover:** first confirm online artwork is
  enabled on the Display page and the radio was restarted. Check that artist and
  title are present, DNS works (`nslookup musicbrainz.org`), and the clock is
  correct (`date`); a badly wrong clock causes HTTPS certificate validation to
  fail. A low-confidence/ambiguous MusicBrainz result or a release with no Cover
  Art Archive image intentionally keeps the glyph. Provider failures are retried
  with backoff, so repeatedly restarting or toggling the setting is unnecessary.
- **Inspect artwork errors without exposing listening history:** stop the service
  and run `radio.py` in the foreground with `RADIO_LOG_LEVEL=ERROR` as described
  in [`buildroot.md`](buildroot.md#logging-and-debugging). Startup and unexpected
  artwork errors omit track text; controlled DNS/TLS/no-match/no-cover failures
  may produce no line because the glyph and retry are normal fallback behavior.
  Do not publish the now-playing status, screenshots, provider URLs, or any debug
  output that a future provider/library version might add until you have reviewed
  and redacted it.

USB Audio is separate: USB Audio Class transports PCM audio and has no native
artist/title metadata, so Bluetooth online cover lookup cannot operate for that
source.

See [`buildroot.md`](buildroot.md) for the full image reference and
[`adding-a-music-source.md`](adding-a-music-source.md) for the `BluetoothService`
implementation notes.
