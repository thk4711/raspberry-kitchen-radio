# Web administration interface

The radio serves a small, local **web administration interface** on your home
WiFi. Use it to see what the radio is doing, edit presets and settings, install
firmware to the inactive A/B slot, and restart or reboot the radio — all from a
phone or laptop browser, without SSH.

It is served by a separate, lightweight service (`radio_web`) built entirely
from the Python standard library — no extra web server, database or framework.

> **Scope.** This covers station editing, source flags, device settings,
> maintenance, **display &amp; audio settings**, **Bluetooth pairing
> management**, **WiFi / static-IP configuration**, and **firmware update and
> rollback**. WiFi can also still be
> set on the SD card's `radio-config.txt` (see [`buildroot.md`](buildroot.md)),
> which remains the recovery route.

## Reaching the interface

The interface listens on **port 8080**, bound to the radio's WiFi (`wlan0`)
address and to loopback (`127.0.0.1`). Open one of:

- `http://<hostname>.local:8080` — using the device's hostname (set during
  SD-card provisioning or later under Device settings; see
  [`buildroot.md`](buildroot.md)).
- `http://<device-ip>:8080` — using the radio's IP address.

Not sure of the address? The **Dashboard** (below) shows the WiFi IP once you
are connected, or check your router's client list. The Pi 3A+ is WiFi-only (no
Ethernet), so the interface is only reachable from your local network; it is
never exposed to the internet.

> **Security — plain HTTP on a trusted LAN.** The interface serves plain,
> unencrypted HTTP. Your admin password and session cookie travel in the clear
> on the local network, so only use it on a WiFi network you trust. The admin
> password is **separate** from the SSH/root password of the device.

## First-use setup and login

1. **Set an admin password.** The first time you open the interface it asks you
   to create an admin password (at least 8 characters). It is stored as a salted
   PBKDF2 hash in `/etc/radio/admin.secret` (never in plain text).
2. **Log in.** After setup you log in with that password. A session cookie
   (HttpOnly, SameSite=Strict) keeps you signed in; repeated failed logins are
   rate-limited. Use **Log out** to end the session. Sessions are held in memory
   and are cleared on reboot or when the radio restarts.

The **Dashboard is public** (read-only status); every editing/maintenance page
requires login.

### Forgot the admin password?

SSH into the device and remove the secret file, then reload the page — the
interface will prompt you to set a new password:

```sh
rm -f /etc/radio/admin.secret
```

## The pages

The Dashboard links to each admin page (Edit stations · Music sources · Display
· Audio · WiFi &amp; network · Device settings · Maintenance). Firmware management
is linked from Maintenance.

### Dashboard (`/`)

Read-only, no login required. Shows:

- **Now playing** and the **active source**, from the player's status snapshot.
  The card also indicates whether that source is currently playing and refreshes
  itself in the background every five seconds without reloading the whole page.
  It shows the configured station logo for Internet Radio or locally cached
  album art for Spotify/AirPlay, with a placeholder when artwork is unavailable.
- **Per-source state** (Internet Radio, AirPlay, Spotify, Bluetooth, USB Audio):
  whether each is enabled, running and currently active.

### ADC debug & calibration (`/debug/adc`)

Available from the separate **Physical controls** card on **Device settings**
after login. The page shows live ADS1115 raw
millivolt values for volume (AIN0), buttons (AIN1), power (AIN2), and the unused
AIN3 diagnostic channel. Alongside each value it shows the player's current
interpretation: mapped/applied volume, detected preset button, and ON/OFF state.

Use the capture buttons to record volume endpoints, button 1/button 6 ladder
endpoints, and both power-switch positions. Saving writes `/etc/radio/adc.ini`;
choose **Save and restart radio** to apply it immediately. Live data uses an
authenticated, same-origin WebSocket and automatically reconnects after a player
or WiFi interruption.
- **System status:** hostname, app version, uptime, CPU temperature, memory and
  root-filesystem usage, WiFi IP/SSID/signal, and the player heartbeat age.
- **Bluetooth status (read-only):** the adapter's visible name (it follows the
  device name, set under **Device settings**), whether it is powered, and whether
  it is currently discoverable. There are no Bluetooth controls: when nothing is
  connected the radio is discoverable and pairs with no PIN automatically, so no
  manual pairing action is needed (see [`bluetooth.md`](bluetooth.md)).

If the player is down or a metric is unavailable, that field simply shows a
placeholder — the dashboard always renders.

### Edit stations (`/stations`)

Edit the six front-panel preset stations. Each slot has a **name**, **stream
URL** (http/https) and an optional **logo filename**:

- **Test** probes a stream URL before you save it.
- **Move up / Move down** reorders the presets (slot order = button order 1–6;
  see [`stations.md`](stations.md)).
- **Save** validates and stores your edits.
- **Apply and restart radio** restarts the player so the new presets take
  effect.
- **Restore built-in** discards your edits and reverts to the shipped presets.

Your edits are written to a managed copy at `/etc/radio/stations.ini`; the
shipped [`lib/mpd_service/stations.conf`](../lib/mpd_service/stations.conf) is
never modified. **Logo uploads are not supported** — enter the filename of a
logo already shipped under `lib/mpd_service/logos/`, or leave it blank to use a
generated initials tile (see [`logos.md`](logos.md)).

### Music sources (`/sources`)

Turn the user-facing music sources on or off:

- **Internet Radio, AirPlay, Spotify, Bluetooth and USB Audio.**
- **Apply** restarts the radio so disabled sources are neither started nor
  shown as running.
- **Restore built-in** re-enables every source.

Choices are written to `/etc/radio/sources.ini`. A missing file or key means
"enabled", so a fresh image behaves exactly as before.

### Device settings (`/device`)

In addition to the device name, timezone and NTP server, this page controls
**SSH remote access**, which is disabled by default. SSH is a device service, not a music source. Changes are
applied immediately; disabling it closes new SSH access after the current
session ends. An SD-card `radio-config.txt` setting of `enable_ssh=0` still
overrides the web setting.

A separate **Physical controls** card links to live ADC debugging and
calibration for the volume knob, preset buttons and power switch.

### Display (`/settings`)

A small, safe subset of the display theme and behaviour:

- **Theme preset** (Default / High contrast / Dim night / No animations),
  **animations** on/off, **idle timeout** (clock screensaver), **crossfade**
  duration, **clock size**, and the volume-OSD / preset-toast durations.

Display options are written to `/etc/radio/display.ini` (a `[ui]` section layered
over the shipped `display.conf`). They take effect after **Apply and restart
radio**.

The display's low-level SPI bus and hardware chip-select are not web settings.
They are configured in the shipped `[display]` section. The default
`spi_device = 0` uses SPI0 CE0/BCM 8; a MERUS Amp reserves BCM 8, so move the
display CS wire to SPI0 CE1/BCM 7 (physical pin 26) and set `spi_device = 1`.
See [`display-test.md`](display-test.md#alternative-chip-select-for-a-merus-amp).

### Audio (`/audio-hardware`)

Choose the active sound card from a drop-down list of the built-in, IQaudIO,
generic I2S, HiFiBerry, Allo, Audiophonics I-SABRE, ESS Katana, JustBoom and
MERUS profiles, select the USB Audio mode, and set the **maximum volume** (0–100),
a cap the physical volume knob cannot exceed. Each drop-down entry shows whether
it is project tested, kernel-supported but awaiting project hardware validation,
or experimental; a note below the drop-down shows the selected card's family,
kind and compatibility detail. See
[`sound-devices.md`](sound-devices.md) for the complete matrix and the dedicated
pinout for each choice.

Above the output settings and their Apply/Restore controls, a separate form
provides a ten-band **parametric equalizer**. Enable or bypass the
whole EQ, choose bell, low/high shelf, or low/high-pass for each band, and edit
frequency, gain and Q. The logarithmic response graph updates immediately and
bell/shelf points can be dragged. A preamp control supplies headroom for boosted
bands. **Save EQ** stores changes without interrupting playback; **Apply EQ**
updates the sound live — ordinary band and preamp changes are pushed to the
running LADSPA plugin without interrupting playback. Only enabling or disabling
the whole equalizer regenerates the ALSA route and briefly restarts the radio
application, MPD, AirPlay/Spotify, Bluetooth and the USB Audio bridge so every
source reopens it.
**Reset flat** disables the EQ and restores the preamp, all bands, and all graph
dots to their flat defaults. Settings are stored in `/etc/radio/equalizer.ini`.
See the dedicated [Parametric equalizer guide](equalizer.md) for filter choices,
headroom, troubleshooting, persistence and developer architecture.

The output-settings form has one **Apply changes** button for its three settings
(sound card, USB mode and maximum volume). Its **Restore defaults** action does
not alter the separately managed equalizer. A maximum-volume
only change is written to `/etc/radio/audio.ini` and applied by restarting the
player. The ALSA mixer control the volume knob drives is set automatically by the
sound-card selection (below), so it is not edited by hand.

Because the sound card is selected by a **device-tree overlay** that the firmware
resolves at boot, changing it is a **reboot-level** operation — it is not live.
Applying a changed card saves the choice to `/etc/radio/audio_hardware.ini`, and
the privileged helper then, for the chosen profile:

- edits **only** the `dtparam=audio=` / `dtoverlay=` lines in the FAT boot
  partition's `config.txt` (backing it up to `config.txt.bak` first, and
  unmounting again immediately),
- writes `/etc/asound.conf` using a stable `CARD=…` id for every profile; devices
  without hardware volume receive a shared ALSA `Radio Volume` softvol layer,
- rewrites the MPD `audio_output` device and hardware/software mixer routing,
- writes any needed `/etc/modules-load.d` entry, and
- derives the ALSA mixer control **and the amplifier-enable GPIO** from
  `/etc/radio/audio_hardware.ini` and the firmware profile catalog so the volume
  knob, MPD and the player's amp control all agree. GPIO 26 remains enabled only
  for the existing IQaudIO plus external-amp
  circuit; HAT amplifiers managed by their kernel drivers use `amp = none`.

Changing USB Audio mode also requires a reboot. If either reboot-level setting
changes, the page shows a **Reboot required** choice after saving everything;
reboot (the password is asked, as for any reboot) or defer it. **Restore defaults**
returns the sound card, USB mode and maximum volume to their shipped defaults;
use the EQ form's **Reset flat** button to reset the equalizer.
The SD-card `radio-config.txt` remains the recovery route.

### WiFi &amp; network (`/network`)

Change the WiFi network or set a static IP. Because a bad WiFi change could drop
the very connection you are using, the new settings are **tried first and revert
automatically** if the radio cannot reconnect within about a minute — so you
cannot lock yourself out. If the page reloads after a change, click **Keep these
settings** to confirm (or **Revert now** to go back immediately). The SD card
`radio-config.txt` remains the recovery route.

- **SSID** and **password** (WPA2-personal, 8–63 characters) and an optional
  two-letter **country code**.
- **Automatic (DHCP)** or **Static IP** — with IP address, CIDR prefix,
  gateway, and optional DNS servers.

### Device settings (`/device`)

- **Device name** (hostname): saved immediately and applied on the **next
  reboot**. If the SD card's `radio-config.txt` sets a hostname, that wins and
  the field is shown locked (see [priority](#radio-configtxt-takes-priority)).
- **Timezone** and **NTP server**: applied like the boot-time provisioning. The
  current values are prefilled, and timezone is selected from the zones installed
  in the image rather than entered as an unfamiliar zoneinfo name.
- The old root-filesystem expansion control has been removed for the A/B image.
  Firmware slots have fixed equal sizes; the final persistent data partition is
  expanded automatically by the guarded early-boot service.

On a fresh image the form reads the current hostname, timezone and Chrony NTP
source from the system. Saved overrides are written to `/etc/radio/device.ini`.

### Maintenance (`/maintenance`)

- **Restart player** — restarts the main radio application and its audio
  integrations while the operating system and web interface keep running.
  **Restart MPD** restarts only the internet-radio playback service. Both take
  effect immediately.
- **Create backup** opens a password prompt and downloads a backup file containing radio
  configuration and logos, WiFi settings, administrator/root credentials, SSH
  identity, and Bluetooth pairings. The archive contains secrets: store and
  transfer it securely. Firmware packages, update history, migration backups,
  and partition-resize state under `/data/update` are never included.
- **Restore backup** opens a window that asks for one backup file and the current
  administrator password. It validates the file's
  format, path allowlist, persistent-schema compatibility, expanded size, and
  every file checksum before displaying a second confirmation. Applying it
  replaces the selected persistent data transactionally and stops playback,
  MPD, and Bluetooth services. Reboot afterward to apply all restored settings;
  WiFi connectivity, credentials, host identity, and the next web login may
  change. Both backup and restore require the current administrator password.
- **Reboot device** restarts the complete Raspberry Pi and temporarily
  disconnects the web interface. **Shut down device** safely stops the operating
  system and requires a physical power cycle to start it again. Both open a
  confirmation page and require you to re-enter your admin password.
- **Download diagnostics bundle** — a small `tar.gz` of the volatile in-memory
  `/tmp` logs plus read-only status snapshots, capped at ~1 MiB. It is cleared
  on reboot and **excludes secrets and WiFi credentials**.
- **Firmware update** — the Firmware box shows the running and retained firmware.
  **Update firmware** opens a guided overlay that asks for the admin password,
  update file, review and final confirmation. It reports upload and installation
  progress, reboots automatically, reconnects when the web service returns, and
  waits for trial acceptance. The detached update continues safely if the browser
  is closed. Reboot, shutdown, another upload and sound-card boot changes are
  rejected while installation is active. After reboot, the radio allows up to 120 seconds
  for local boot-health checks. A healthy slot is accepted automatically; an
  unhealthy slot is retried and then rolled back to the previous firmware by
  U-Boot. The overlay reports acceptance, a pending failed trial, or an
  automatic rollback together with a bounded local failure reason.
- **Manual firmware switch** — Maintenance inspects the other A/B slot through a
  fixed read-only mount and offers **Switch to previous firmware** when it is
  compatible. The displayed details identify its version and slot. The operation requires CSRF validation,
  explicit confirmation, and the current admin password. It starts another trial
  boot, so failed local health checks automatically return to the firmware from
  which the switch was requested. Shared configuration remains in `/data`; no
  firmware-owned files such as `/etc/mpd.conf` are copied between slots.

Both installation and manual switching also compare the target firmware's declared
persistent-configuration reader capability with the shared data. The action is
blocked with an explicit compatibility message when rollback would be unsafe.
Additive settings and unknown INI keys remain compatible; irreversible configuration
migrations are rejected by the normal updater.

Firmware packages are unsigned. Their SHA-256 checks detect accidental corruption
but do not prove authenticity, so obtain packages from a trusted source and use
the administration interface only on a trusted LAN.

For the complete operator sequence—including what each progress phase means,
successful trial acceptance, interrupted operations, automatic and manual
rollback, and recovery when this interface is unreachable—see
[`firmware-updates.md`](firmware-updates.md).

## Which changes need a restart or reboot?

| Change | How it takes effect |
| --- | --- |
| Station presets (`/stations`) | **Restart radio** — offered inline as "Apply and restart radio". |
| Music sources (`/sources`) | **Restart radio** — offered inline as "Apply". |
| SSH remote access (`/device`) | Applied immediately. |
| Timezone / NTP server (`/device`) | Applied on save; fully in effect after the next time sync / reboot. |
| Device name (`/device`) | **Reboot** the device. |
| Display (`/settings`) | **Restart radio** — offered inline as "Apply and restart radio". |
| Audio (`/audio-hardware`) | One **Apply changes** action: a maximum-volume-only change restarts the player; a sound-card or USB Audio mode change offers a device reboot after all settings are saved. |
| WiFi / static IP (`/network`) | Applied immediately on a **try-then-auto-revert** basis; confirm to keep. |
| Admin password / login | Immediate. |

## Managed configuration files

The web UI never edits the running firmware slot for ordinary settings. Instead
it writes managed overrides through `/etc/radio/`, which is backed by shared
`/data/radio` and read **on top of** built-in defaults. This state therefore
survives firmware updates and rollback:

| File | Contents | Mode |
| --- | --- | --- |
| `/etc/radio/admin.secret` | Salted PBKDF2 hash of the admin password. | `0600` |
| `/etc/radio/stations.ini` | Your edited presets (overrides `stations.conf`). | `0644` |
| `/etc/radio/logos/` | Uploaded station logos, normalized to PNG (overrides shipped logos). | `0644` |
| `/etc/radio/sources.ini` | Music-source on/off flags. | `0644` |
| `/etc/radio/device.ini` | Device name, timezone, NTP server. | `0644` |
| `/etc/radio/display.ini` | Display `[ui]` options (overrides `display.conf`). | `0644` |
| `/etc/radio/audio.ini` | User-selected maximum-volume cap. | `0644` |
| `/etc/radio/audio_hardware.ini` | Selected sound-card profile id (overrides the built-in `headphones` default). | `0644` |
| `/etc/radio/equalizer.ini` | Parametric-EQ enable, preamp, filter types, frequencies, gains and Q values. | `0644` |
| `/etc/radio/adc.ini` | ADS1115 volume, button and power calibration. | `0644` |
| `/etc/radio/usb_audio.ini` | Selected UAC1/UAC2 USB Audio gadget mode. | `0644` |
| `/etc/radio/usb_audio_output.ini` | Output handoff generated from the selected sound-card profile. | `0644` |

WiFi changes are written to `/etc/wpa_supplicant.conf` and persistent helper
files under `/etc/radio/` (for a static address and country code) by the
privileged helper.

These are compatibility paths backed by partition p4, not files owned by the
active firmware slot. The complete canonical path inventory, build-time seeding,
early-boot symlinks, atomic-write behavior and intentionally non-persistent state
are documented in [Persistent data](persistent-data.md).

The "Restore built-in" buttons remove the relevant override so the shipped
defaults apply again. A `.bak` copy of the previous override is kept for
stations/sources/device/display/audio edits.

## Active `radio-config.txt` settings take priority once

Active assignments in `radio-config.txt` win during that boot and are then
commented automatically. When the card sets the hostname, the web UI locks the
**Device name** field for that boot to avoid the impression that a change will
stick. The SD card is also the **recovery route** if the web UI ever becomes
unreachable — see
[`buildroot.md`](buildroot.md#provisioning-a-prebuilt-image-from-the-sd-card-radio-configtxt).

## How it runs (services)

Two BusyBox init services provide the interface, following a strict privilege
split:

- **`S80radio-web`** — the web server itself, running as a dedicated
  **unprivileged** `radio-web` user.
- **`S79radio-helper`** — a small **root-owned** helper (started first) that
  performs the few privileged operations (restart services, set hostname,
  reboot/shutdown) over a local unix socket, accepting only a fixed set of
  actions. The web process talks to it and can never run an arbitrary command.

See the service layout in [`buildroot.md`](buildroot.md). The interface needs no
extra Buildroot packages beyond what the image already ships.

## Not yet available

These are intentionally out of scope for the current version:
- A full display-theme editor — only the small set of display options on the
  Display page is exposed.
- **Root-password changes** and arbitrary service control.

## See also

- [`stations.md`](stations.md) — the preset station file format and how buttons
  map to presets.
- [`logos.md`](logos.md) — how logos render and how to add your own.
- [`buildroot.md`](buildroot.md) — the appliance image, `radio-config.txt`
  provisioning, and the init-service layout.
- [`firmware-updates.md`](firmware-updates.md) — firmware installation,
  activation, rollback, and unreachable-interface recovery.
