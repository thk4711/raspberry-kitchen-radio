# Bluetooth (A2DP) music source

The radio can act as a **Bluetooth audio receiver**: pair a phone or tablet and
its audio plays through the same I2S DAC as internet radio, AirPlay and Spotify.
Track title/artist appear on the display. This is the fourth `MusicSource`
backend (see [`adding-a-music-source.md`](adding-a-music-source.md)).

## How to use it

1. On your phone, open Bluetooth settings and look for the radio. Its name is
   the appliance **hostname** (set via `buildroot/configure.sh --hostname …`,
   default `kuechenradio`).
2. Tap to connect. **There is no PIN and no confirmation prompt** — the radio
   runs an auto-accept agent that answers the pairing automatically. (Your phone
   may briefly show a pairing/passkey dialog; you do not need to act on it — the
   radio confirms it for you.)
3. Start playing anything (music app, video, etc.). Within a couple of seconds
   the radio switches to the Bluetooth source: **whatever was playing before
   (internet radio, AirPlay, Spotify) is stopped**, the display shows the
   Bluetooth track, and audio comes out of the speaker.
4. When you stop/disconnect, the radio returns to pairing mode (see below) and
   another device can connect. Select a preset button or another source to go
   back to radio.

## Pairing mode (no PIN, connection-gated)

- **Whenever no device is connected**, the adapter is **discoverable and
  pairable with no PIN**, so any phone can connect without intervention. An
  auto-accept agent (`bluetoothctl agent auto`) confirms the pairing
  automatically, so even phones that use SSP *numeric comparison* pair without
  you tapping anything on the radio.
- **Once a device is fully paired and connected**, the radio stops advertising
  (it is not discoverable/pairable). The gating waits for the bond to *complete*
  (`Paired: yes` **and** `Connected: yes`) before turning pairability off, so it
  never aborts an in-flight pairing handshake. It automatically re-opens pairing
  mode as soon as that device disconnects or goes out of range.

> **Security note.** No-PIN, auto-accept + always-open-when-idle means *anyone
> in Bluetooth range can connect and pair* while nothing else is connected. This
> is a deliberate choice for a hands-off kitchen appliance. If you need to
> restrict this, replace the `agent auto` / discoverable toggling in
> `S42bluetooth` with a `NoInputNoOutput` agent (and answer confirmations
> manually) or pair manually with `bluetoothctl`.

## What plays, and what shows on the display

- **Audio:** the A2DP stream is received by `bluealsa` and played to the ALSA
  `default` device (→ the `dmix` → I2S DAC path in `/etc/asound.conf`), exactly
  like every other source. Adjust volume with the radio's volume knob as usual.
- **Metadata:** the display shows the **title and artist** reported by the phone
  over AVRCP (`org.bluez.MediaPlayer1`). Bluetooth A2DP/AVRCP carries **no cover
  art**, so instead of album art the radio shows a generated placeholder tile: a
  **Bluetooth glyph on a muted-blue rounded square** (rendered by
  `logo_fallback.render_bluetooth_tile`), centred like a station logo with the
  title/artist text below it. The glyph is the official Bluetooth mark, drawn
  from the public-domain `Bluetooth.svg` path with Pillow (no SVG rasteriser or
  extra dependency on the image).
- **Auto-switching:** the radio stops the previously playing source when the
  phone reports **playback** over AVRCP (`Status == "playing"`). Virtually all
  mainstream music/video apps do this. If you ever meet an app that streams
  audio but never reports AVRCP status, the radio will not auto-stop the other
  source — connect, then briefly pause/resume in the app, or select the source
  manually.

## How it is built and wired (appliance image)

The whole stack is enabled in the Buildroot image; there is nothing to install
on the phone side beyond pairing.

### Firmware / kernel / packages

- **On-chip radio enabled — Bluetooth is on the PL011 UART (`ttyAMA0`).**
  `board/radio/config.txt` does **not** set `dtoverlay=pi3-miniuart-bt`; leaving
  that overlay off keeps Bluetooth on the full PL011 with hardware flow control.
  The kernel console stays on `tty1` (see `cmdline.txt`), so nothing else needs
  the PL011. The config also sets `enable_uart=1` so the PL011 comes up
  deterministically for the BT attach. (Pinning the VPU core clock via
  `core_freq` was tried and made no difference on the PL011, so it is not set.)
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
| Metadata + play/pause | `radio.py` / `lib/bluetooth_service` | A pure **D-Bus consumer**: reads `org.bluez.MediaPlayer1` and issues `Play`/`Pause`. It does **not** launch any Bluetooth daemon. |

Config files: `/etc/bluetooth/main.conf` (adapter class + timeouts disabled so
`S42bluetooth` is authoritative over discoverable/pairable; the adapter *name*
shown to phones is the runtime `Alias`, set by `S42bluetooth` to the hostname —
the `%h` token in `main.conf`'s `Name` is not expanded by this BlueZ build),
`/etc/dbus-1/system.d/bluetooth-radio.conf` (D-Bus policy for `org.bluealsa`).

The Python side has a `[bluetooth]` section in `radio.conf`
(`dbus_service = org.bluez`).

## On-target validation & troubleshooting

SSH into the radio (`ssh root@<radio-ip>`), then:

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
  `Alias:` set to the hostname (e.g. `kuechenradio`). If `Pairable: no` while
  idle, the persistent auto-pairing session is not up: check that a single
  `bluetoothctl` process is running (`ps | grep bluetoothctl`) and that the
  control FIFO exists (`ls -l /run/bluetooth-radio-btctl.fifo`); restart with
  `S42BLUETOOTH_LOG=/tmp/S42bluetooth.log /etc/init.d/S42bluetooth restart` and
  inspect the log. If `Powered: no`, verify `S40bluetoothd` is running and
  `hci0` exists (`ls /sys/class/bluetooth`).
- **Pairing pops up a code and/or fails (`auth failed 0x05`):** if the phone was
  previously (half-)paired, it holds a stale link key. Do **"Forget This Device"**
  on the phone **and** `bluetoothctl remove <MAC>` on the radio, then pair fresh.
  The radio's auto-accept agent (`agent auto`) confirms SSP numeric comparison
  automatically, so no code needs tapping on the radio side.
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
  audio still plays. This also means the radio may not auto-stop the previous
  source (see the AVRCP note above).

See [`buildroot.md`](buildroot.md) for the full image reference and
[`adding-a-music-source.md`](adding-a-music-source.md) for the `BluetoothService`
implementation notes.

