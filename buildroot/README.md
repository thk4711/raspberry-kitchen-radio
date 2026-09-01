# Buildroot appliance image — Raspberry Kitchen Radio (Pi 3A+)

This directory holds the `BR2_EXTERNAL` tree that builds a **minimal,
fast-booting Buildroot image** running the radio app, replacing the full
Raspberry Pi OS install. It targets the **Raspberry Pi 3A+ (32-bit ARMv7,
512 MB)** and builds **all media backends from source** (shairport-sync, nqptp,
go-librespot). The repo ships no prebuilt media binaries.
The init system is **BusyBox init** (no systemd) for a faster boot.

The image is built **natively on an x64 (amd64) Debian host** using a stock
Buildroot checkout plus this directory as the `BR2_EXTERNAL` tree. There is no
Docker involved. Only flashing the SD card is done on your workstation.

> **This is a quick-start.** The authoritative, step-by-step reference — build
> (scripted & manual), flash, on-target validation, defconfig/service layout,
> design constraints and background — is [`../doc/buildroot.md`](../doc/buildroot.md).

## Prerequisites

- An **x64 (amd64) Debian** build host with the Buildroot host prerequisites
  installed (build-essential, bison, flex, libncurses-dev, rsync, cpio, unzip,
  bc, python3, git, wget, file, …).
- A **stock Buildroot checkout** on that host (the working setup uses
  `~/embedded/buildroot`).
- A shared download cache reused across builds via `BR2_DL_DIR`
  (`~/embedded/dl`).

No cross-compilers need to be installed by hand — Buildroot fetches the Bootlin
external toolchain and builds `host-go` itself.

## Quick start (scripted — recommended)

Two helper scripts in this directory do everything. Run them on the **x64/amd64
Debian host** from the root of a fresh clone of this repository:

```bash
# 1) Configure the appliance. Prompts for each value (current value = default),
#    or pass flags to script it. WiFi credentials are written to a git-ignored
#    file, so they are never committed.
./buildroot/configure.sh --hostname kuechenradio \
    --ssid MyNetwork --psk 'my-wifi-password' \
    --root-password radio --dac iqaudio-dacplus
./buildroot/configure.sh --show      # print current values (PSK hidden)

# 2) Build (or rebuild) the image. Installs the host build packages via apt,
#    clones a pinned stock Buildroot into ~/embedded/buildroot (shared download
#    cache ~/embedded/dl), applies radio_rpi3_defconfig, and compiles.
./buildroot/build.sh
./buildroot/build.sh --configure     # run configure.sh first, then build
./buildroot/build.sh --clean         # make clean, then rebuild
./buildroot/build.sh --dirclean      # wipe output/, then full rebuild
./buildroot/build.sh --no-apt        # skip the apt host-package step
```

`build.sh` prints the finished image path (`output/images/sdcard.img`), its
size and SHA-256, plus the exact `dd` command for Linux/macOS.

Override the build locations with environment variables if the defaults do not
suit your host:

```bash
BUILDROOT_DIR=~/br/buildroot BR2_DL_DIR=~/br/dl BUILDROOT_VERSION=2026.05.2 \
    ./buildroot/build.sh
```

What `configure.sh` writes (only git-ignored files — no tracked source is
edited, so two devices configure from one clean checkout and no secret lands in
a tracked diff):

- `buildroot/local-device.conf` — `RADIO_HOSTNAME`, `RADIO_ROOT_PASSWORD`,
  `RADIO_DAC`. `build.sh` reads these back: hostname + root password are merged
  onto the tracked defconfig via a generated kconfig fragment, and the DAC
  overlay is applied to the boot `config.txt` build artifact.
- `external/board/radio/rootfs-overlay/etc/wpa_supplicant.conf` — regenerated
  from the template as a constraint-safe `network={...}` block
  (`key_mgmt=WPA-PSK`; no `ctrl_interface`/`update_config`/`country`, which the
  target `wpa_supplicant` rejects).

## Quick start (manual, on the Debian host)

```bash
# 1) Get this repo onto the build host (rsync from your workstation), so the
#    BR2_EXTERNAL tree + radio-app source live there. Adjust user@host:path:
rsync -az --delete --exclude '.git' --exclude '__pycache__' \
    ./ user@build-host:~/embedded/radio-repo/

# 2) Edit WiFi credentials before building (copy the template, then edit):
#    buildroot/external/board/radio/rootfs-overlay/etc/wpa_supplicant.conf
#    (start from wpa_supplicant.conf.example)

# 3) On the build host, apply the radio defconfig and build:
ssh user@build-host
cd ~/embedded/buildroot
export BR2_DL_DIR=$HOME/embedded/dl
make BR2_EXTERNAL=$HOME/embedded/radio-repo/buildroot/external radio_rpi3_defconfig
make -j"$(nproc)"

# 4) The image is at output/images/sdcard.img — copy it back to flash it:
#    (run this from your workstation)
scp user@build-host:~/embedded/buildroot/output/images/sdcard.img .

# 5) Flash it (find the device first):
#    macOS:    diskutil list        -> sudo dd if=sdcard.img of=/dev/rdiskN bs=4m
#    Linux:    lsblk                 -> sudo dd if=sdcard.img of=/dev/sdX  bs=4M oflag=direct conv=fsync
```

First build takes a while (full toolchain + kernel + Go). The `dl/` cache
persists (via `BR2_DL_DIR`), so rebuilds are much faster.

To change kernel/Buildroot options interactively:

```bash
cd ~/embedded/buildroot
make BR2_EXTERNAL=$HOME/embedded/radio-repo/buildroot/external menuconfig
```

## Layout

```
buildroot/
└─ external/               # BR2_EXTERNAL tree
   ├─ external.desc / external.mk / Config.in
   ├─ configs/radio_rpi3_defconfig   # the working 32-bit Pi 3 base
   ├─ package/             # go-librespot (pinned) and radio-app
   │                       #   (nqptp, python-smbus2, python-rpi-gpio,
   │                       #    python-gpiozero, python-colorzero come from
   │                       #    upstream Buildroot)
   └─ board/radio/
      ├─ config.txt        # i2c/i2s/spi on, audio off, iqaudio-dacplus,
      │                    #   Bluetooth on PL011 (NOT pi3-miniuart-bt) + enable_uart,
      │                    #   disable_splash, boot_delay=0, initial_turbo=30,
      │                    #   gpu_mem_512=100
      ├─ cmdline.txt        # kernel command line (quiet loglevel=3, tty1 only)
      ├─ linux-i2c.fragment       # positive fragment (APPLIED): expose the ADS1115 I2C bus
      ├─ linux-watchdog.fragment  # positive fragment (APPLIED): BCM2835 watchdog + zram
      ├─ linux-bluetooth.fragment # positive fragment (APPLIED): CONFIG_BT + BT UART HCI
      ├─ post-build.sh     # mpd user, tmpfs fstab, hostname, service layout
      └─ rootfs-overlay/   # init.d scripts (S13zram, S14watchdog, S41wlan async
                           #   WiFi, S42bluetooth A2DP receiver + no-PIN pairing
                           #   while unconnected, S90radio supervisor), asound.conf,
                           #   mpd.conf, bluetooth/main.conf, default/bluetoothd,
                           #   usr/sbin/radio-boot-splash (early, non-blocking
                           #   SPI boot splash launched detached from inittab),
                           #   wpa_supplicant.conf, network/interfaces
```

## What runs on the target (boot order, BusyBox init)

1. mdev / mounts, tmpfs on `/tmp`
2. `S13zram` — compressed RAM swap (OOM protection on the 512 MB board)
3. `S14watchdog` — arm the BCM2835 hardware watchdog (`/dev/watchdog`)
4. `S40network` brings up `lo` (WiFi is *not* here — see step 5, non-blocking)
5. `S40bluetoothd` (bluez5_utils) — `bluetoothd`, augmented with `--experimental`
   via `/etc/default/bluetoothd` so AVRCP metadata is exposed on D-Bus
6. `S41wlan` — starts `wpa_supplicant` + DHCP for `wlan0` **in the background**
   directly (no `ifup`/`allow-hotplug` indirection, so errors are logged to
   `/tmp/S41wlan.log`)
7. `S42bluetooth` — registers a no-PIN auto-pairing agent, keeps the adapter
   discoverable/pairable while **no device is connected** (stops advertising once
   one connects; re-opens when it disconnects), and runs `bluealsa` (A2DP
   receiver) + `bluealsa-aplay` (→ ALSA `default` → I2S DAC)
8. `dbus` (system bus — required for shairport-sync AirPlay control and BlueZ)
9. `S49chronyd` — sets wall-clock time for HTTPS/TLS clients on the RTC-less Pi
10. `avahi-daemon` (mDNS for AirPlay / Spotify discovery)
11. `S50dropbear` — Dropbear SSH server (root login enabled; `/etc/default/dropbear`)
12. `S50mpd` — Music Player Daemon (`/etc/mpd.conf`)
13. `S90radio` — supervises `radio.py` (crash restart with exponential backoff +
    a freeze-watcher that restarts a hung-but-alive app via the `/tmp/radio.alive`
    heartbeat), which itself launches `nqptp`, `shairport-sync` and `go-librespot`
    (paths injected via `RADIO_*_BINARY` env vars). The Bluetooth source is *not*
    launched here — `radio.py` only reads `org.bluez` metadata over D-Bus.

> **Self-recovery.** Because the radio is an appliance it must never stay
> unusable. Three layers recover automatically, cheapest first: `S90radio`
> restarts a **crashed** `radio.py` (with backoff) and restarts a **frozen** one
> (heartbeat goes stale → freeze-watcher kills it); and if the whole system
> hangs, the **hardware watchdog** (`S14watchdog`) hard-resets the board. See
> `doc/buildroot.md` → "Self-recovery and reliability".

> **Boot-time optimizations.** BusyBox init (no systemd); `quiet loglevel=3` +
> `console=tty1` in `cmdline.txt`; `boot_delay=0` + `initial_turbo=30`,
> splash off, `gpu_mem_512=100` in `config.txt`; and **async
> WiFi** (`S41wlan`) so the display and MPD come up without waiting for DHCP. A
> single DHCP client ships (BusyBox `udhcpc`; the unused `dhcpcd` package was
> removed). Only the small positive `linux-i2c.fragment` is applied (no broad
> kernel trim is used), and `gpu_mem` is kept generous (100 MB) rather than
> dropped to 16 MB while the
> HDMI/framebuffer path stays debuggable — see the design constraints in
> [`../doc/buildroot.md`](../doc/buildroot.md). (Bluetooth uses the PL011 UART —
> `config.txt` deliberately does **not** set `pi3-miniuart-bt`, which would put
> BT on the mini-UART and break A2DP — see
> [`../doc/bluetooth.md`](../doc/bluetooth.md).)

## App changes made for this image

- `lib/utilities.py::restart_systemd_service` is now init-agnostic (systemd
  D-Bus → SysV `/etc/init.d` → `service` fallback), so the MPD-recovery path
  works under BusyBox init.
- `lib/spotify_service/spotify_service.py` writes its generated config to a
  writable path (`RADIO_SPOTIFY_CONF` / `/tmp`) for a read-only rootfs, and the
  binary path / config argument are overridable (`RADIO_SPOTIFY_BINARY`,
  `RADIO_SPOTIFY_CONFIG_ARG`).
- `lib/airplay_service/airplay_service.py` binary paths are overridable
  (`RADIO_NQPTP_BINARY`, `RADIO_AIRPLAY_BINARY`).
- External media backend stdout/stderr is kept under
  `/tmp/raspberry-kitchen-radio/processes/` so crashes from `nqptp`,
  `shairport-sync`, or `go-librespot` can be diagnosed on the target.

All backend paths are set by `S90radio` to the Buildroot-built binaries on
`PATH`; when the env overrides are unset the app looks the binaries up by name
on `PATH`.

## Notes / things to verify on first hardware boot

- Set your WiFi SSID/PSK with `./buildroot/configure.sh` before flashing (it
  writes the git-ignored `wpa_supplicant.conf`).
- Confirm the DAC overlay matches your board (`configure.sh --dac ...`;
  `iqaudio-dacplus` default; `hifiberry-dacplus` / `merus-amp` also supported).
- Validate: `i2cdetect -y 1` (ADS1115 @0x48), `aplay -l` (DAC), SPI display,
  MPD playback, AirPlay + Spotify discovery, amp GPIO 26.
- Some `BR2_PACKAGE_*` option symbols may differ slightly by Buildroot release;
  run `make menuconfig` in the Buildroot tree if a symbol is reported unknown,
  then save.
