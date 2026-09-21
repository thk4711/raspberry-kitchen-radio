# Buildroot appliance image — PiSonic (Pi 3A+)

This directory holds the `BR2_EXTERNAL` tree that builds a **minimal,
fast-booting Buildroot image** running the radio app, replacing the full
Raspberry Pi OS install. It targets the **Raspberry Pi 3A+ (32-bit ARMv7,
512 MB)** and builds **all media backends from source** (shairport-sync, nqptp,
go-librespot). The repo ships no prebuilt media binaries.
The init system is **BusyBox init** (no systemd) for a faster boot. The Pi 3A+
USB-A data port is configured as a UAC1 USB Audio peripheral; see
[`../doc/hardware.md`](../doc/hardware.md#usb-audio-gadget-wiring) before wiring
it because VBUS/+5 V must not be connected.

The image is built **natively on an x64 (amd64) Debian host** using a stock
Buildroot checkout plus this directory as the `BR2_EXTERNAL` tree. There is no
Docker involved. Only flashing the SD card is done on your workstation.

> **This is a quick-start.** The authoritative, step-by-step reference — build
> (scripted & manual), flash, on-target validation, defconfig/service layout,
> design constraints and background — is [`../doc/buildroot.md`](../doc/buildroot.md).

## Prerequisites

- An **x64 (amd64) Debian** build host with the Buildroot host prerequisites
  installed (build-essential, bison, flex, libncurses-dev, rsync, cpio, unzip,
  bc, python3, git, wget, file, …) and the native `swupdate` package used to
  check the completed update archive without installing it. The `swupdate`
  package pulls its `libubootenv0.1` runtime dependency, so `libubootenv.so.0`
  resolves on the default loader path. `build.sh`'s apt step installs both (it
  lists `swupdate` and `libubootenv-tool`); when you run with `--no-apt`,
  install them yourself (`apt-get install swupdate`). Before the long build,
  `build.sh` runs a preflight that verifies a native `swupdate` checker
  actually **runs** (a binary whose libraries cannot be resolved exits 127 and
  is rejected), failing fast with an actionable message otherwise. Point
  `SWUPDATE_CHECKER` at a runnable binary to override the search. The host-side
  check runs `swupdate -c -H radio:<revision>` so the manifest's
  `hardware-compatibility` list is satisfied without an `/etc/hwrevision` on the
  build host (the device still validates against its own `/etc/hwrevision` at
  install time).
- A **stock Buildroot checkout** on that host (the working setup uses
  `~/embedded/buildroot`).
- A shared download cache reused across builds via `BR2_DL_DIR`
  (`~/embedded/dl`).

No cross-compilers need to be installed by hand — Buildroot fetches the Bootlin
external toolchain and builds `host-go` itself.

## Quick start (scripted — recommended)

One helper script builds a generic image. Run it on the **x64/amd64 Debian
host** from the root of a fresh clone of this repository:

```bash
# Build (or rebuild) the generic image. Installs host packages via apt,
#    clones a pinned stock Buildroot into ~/embedded/buildroot (shared download
#    cache ~/embedded/dl), applies radio_rpi3_defconfig, and compiles.
./buildroot/build.sh
./buildroot/build.sh --clean         # make clean, then rebuild
./buildroot/build.sh --dirclean      # wipe output/, then full rebuild
./buildroot/build.sh --no-apt        # skip the apt host-package step
```

The script pins Buildroot `2026.05.2` to commit
`72d9d4fa636a371ef9eb99c92a735ce9f6d829d5`. Every new or existing checkout
must be a clean Git worktree at that exact commit; a different revision,
untracked file, or local modification stops the build. To repair a reusable
checkout, save any wanted changes elsewhere and run `git reset --hard
72d9d4fa636a371ef9eb99c92a735ce9f6d829d5` plus `git clean -fd` in it. For an
intentional, non-release development build only, pass
`--allow-unverified-buildroot`; the script emits warnings and records the actual
commit in the build log and firmware release metadata. When building through
`scripts/build_image.py`, the same override is available with
`--allow-unverified-buildroot` (or INI key `allow_unverified_buildroot = true`).

`build.sh` requires and prints both the installation image
(`output/images/sdcard.img`) and versioned firmware update
(`output/images/pisonic-<version>.swu`), including each size and SHA-256.
It also prints the exact `dd` command for flashing the installation image.

Override the build locations with environment variables if the defaults do not
suit your host:

```bash
BUILDROOT_DIR=~/br/buildroot BR2_DL_DIR=~/br/dl BUILDROOT_VERSION=2026.05.2 \
    ./buildroot/build.sh
```

No device identity or credentials are embedded during the build. Root password
login is locked in the generic image. After flashing, edit `radio-config.txt` on
the FAT boot partition to set WiFi, hostname, a unique root password and other
first-boot values. SSH remains disabled unless that credential was successfully
persisted. The same image can provision many devices.

### Artifact helper and remote builds

[`../scripts/build_image.py`](../scripts/build_image.py) wraps this build and
publishes timestamped, checksum-verified copies of both artifacts. It builds on
the current host by default, so SSH is optional:

```bash
python3 scripts/build_image.py --dry-run
python3 scripts/build_image.py
```

For reusable local or remote settings, copy
`scripts/build-image.example.ini` to the git-ignored `scripts/build-image.ini`.
Set `execution = remote` and the dummy SSH/build paths for remote operation;
that mode stages the checkout with `rsync`, builds through batch-mode SSH, and
retrieves it with `scp`. CLI options override configuration values. See
[`../doc/build-from-scratch.md`](../doc/build-from-scratch.md#optional-collect-named-artifacts-locally-or-build-remotely)
for examples and run `python3 scripts/build_image.py --help` for every option.

## Quick start (manual, on the Debian host)

```bash
# 1) Get this repo onto the build host (rsync from your workstation), so the
#    BR2_EXTERNAL tree + radio-app source live there. Adjust user@host:path:
rsync -az --delete --exclude '.git' --exclude '__pycache__' \
    ./ user@build-host:~/embedded/radio-repo/

# 2) On the build host, apply the radio defconfig and build:
ssh user@build-host
cd ~/embedded/buildroot
export BR2_DL_DIR=$HOME/embedded/dl
make BR2_EXTERNAL=$HOME/embedded/radio-repo/buildroot/external radio_rpi3_defconfig
make -j"$(nproc)"
# 3) The artifacts are in output/images — copy both back to your workstation:
#    (run this from your workstation)
scp user@build-host:~/embedded/buildroot/output/images/pisonic-<version>.swu .
scp user@build-host:~/embedded/buildroot/output/images/sdcard.img .

# 4) Flash it (find the device first), then edit radio-config.txt on its boot
#    partition before starting the Pi:
#    macOS:    diskutil list        -> sudo dd if=sdcard.img of=/dev/rdiskN bs=4m
#    Linux:    lsblk                 -> sudo dd if=sdcard.img of=/dev/sdX  bs=4M oflag=direct conv=fsync
```

`sdcard.img` is used for initial installation or complete recovery. The matching
`.swu` is used for an A/B firmware update through the radio's Maintenance web
interface and does not replace the partition table or shared configuration.

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
      ├─ config.txt        # built-in headphones by default; optional I2S DACs,
      │                    #   DWC2 USB peripheral mode,
      │                    #   Bluetooth on PL011 (NOT pi3-miniuart-bt) + enable_uart,
      │                    #   disable_splash, boot_delay=0, initial_turbo=30,
      │                    #   gpu_mem_512=100
      ├─ cmdline.txt        # kernel command line (quiet loglevel=3, tty1 only)
      ├─ linux-i2c.fragment       # positive fragment (APPLIED): expose the ADS1115 I2C bus
      ├─ linux-watchdog.fragment  # positive fragment (APPLIED): BCM2835 watchdog + zram
      ├─ linux-bluetooth.fragment # positive fragment (APPLIED): CONFIG_BT + BT UART HCI
      ├─ linux-usb-audio.fragment # positive fragment (APPLIED): DWC2 + ConfigFS UAC1
       ├─ device_table.txt   # fakeroot ownership for radio-web writable dirs
       ├─ image-layout.conf # fixed A/B partition sizes, labels, UUIDs, env offsets
       ├─ boot.cmd          # U-Boot A/B trial counter and automatic fallback
       ├─ uboot.fragment    # redundant raw environment and ext4 boot support
       ├─ genimage.cfg.in   # p1 loader, p2/p3 firmware slots, p4 persistent data
       ├─ post-build.sh     # users, /data + tmpfs fstab, hostname, service layout
       └─ rootfs-overlay/   # init.d scripts (S12data-resize, S13zram, S14watchdog,
                           #   S41wlan async WiFi, S42bluetooth A2DP + no-PIN pairing
                           #   while unconnected, S90radio supervisor), asound.conf,
                           #   mpd.conf, bluetooth/main.conf, default/bluetoothd,
                           #   usr/sbin/radio-boot-splash (early, non-blocking
                           #   SPI boot splash launched detached from inittab),
                           #   wpa_supplicant.conf, network/interfaces
```

## What runs on the target (boot order, BusyBox init)

1. mdev / mounts, fixed `/dev/mmcblk0p4` on `/data`, and tmpfs on `/tmp`; the
   fail-closed `radio-persistent-boot` coordinator then validates writable ext4,
   establishes compatibility links, migrates state, provisions, and renders
   runtime configuration
2. `S12data-resize` — safely expands only final data partition p4
3. `S13zram` — compressed RAM swap (OOM protection on the 512 MB board)
4. `S14watchdog` — arm the BCM2835 hardware watchdog (`/dev/watchdog`)
5. `S39usb-audio` — creates the UAC1 ConfigFS gadget and supervises the
   USB-to-I2S `alsaloop` bridge
6. `S40network` brings up `lo` (WiFi is *not* here — see step 8, non-blocking)
7. `S40bluetoothd` (bluez5_utils) — `bluetoothd`, augmented with `--experimental`
   via `/etc/default/bluetoothd` so AVRCP metadata is exposed on D-Bus
8. `S41wlan` — starts `wpa_supplicant` + DHCP for `wlan0` **in the background**,
   sending the live hostname as DHCP option 12
   directly (no `ifup`/`allow-hotplug` indirection, so errors are logged to
   `/tmp/S41wlan.log`)
9. `S42bluetooth` — registers a no-PIN auto-pairing agent, keeps the adapter
   discoverable/pairable while **no device is connected** (stops advertising once
   one connects; re-opens when it disconnects), and runs `bluealsa` (A2DP
   receiver) + `bluealsa-aplay` (→ ALSA `default` → I2S DAC)
10. `dbus` (system bus — required for shairport-sync AirPlay control and BlueZ)
11. `S49chronyd` — sets wall-clock time for HTTPS/TLS clients on the RTC-less Pi
12. `avahi-daemon` (mDNS for AirPlay / Spotify discovery)
13. `S50dropbear` — Dropbear SSH server (disabled until explicitly enabled)
14. `S50mpd` — Music Player Daemon (`/etc/mpd.conf`)
15. `S90radio` — supervises `radio.py` (crash restart with exponential backoff +
    a freeze-watcher that restarts a hung-but-alive app via the `/tmp/radio.alive`
    heartbeat), which itself launches `nqptp`, `shairport-sync` and `go-librespot`
    (paths injected via `RADIO_*_BINARY` env vars). The Bluetooth source is *not*
    launched here — `radio.py` only reads `org.bluez` metadata over D-Bus.
16. `S99firmware-health` — starts a non-blocking 120-second local health evaluator
    for an active firmware trial. Healthy trials are accepted; unhealthy trials
    reboot until U-Boot reaches its limit and returns to the previous slot.

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
> removed). Only small positive kernel fragments are applied for I2C, watchdog,
> Bluetooth, and USB Audio (no broad kernel trim is used), and `gpu_mem` is kept
> generous (100 MB) rather than
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
  `/tmp/pisonic/processes/` so crashes from `nqptp`,
  `shairport-sync`, or `go-librespot` can be diagnosed on the target.

All backend paths are set by `S90radio` to the Buildroot-built binaries on
`PATH`; when the env overrides are unset the app looks the binaries up by name
on `PATH`.

## Notes / things to verify on first hardware boot

- After flashing, set WiFi and first-boot values in the boot partition's
  `radio-config.txt` before inserting the card into the Pi.
- The built-in headphone output is the safe generic default. After the first
  network boot, select an external sound card from the web interface and reboot.
- Validate: `i2cdetect -y 1` (ADS1115 @0x48), `aplay -l` (DAC), SPI display,
  MPD playback, AirPlay + Spotify discovery, amp GPIO 26. These checks are also
  codified as an optional automated smoke suite: from a checkout on the device
  run `pytest -m hardware` (skipped by default and in CI; see
  `tests/test_hardware_smoke.py`).
- Some `BR2_PACKAGE_*` option symbols may differ slightly by Buildroot release;
  run `make menuconfig` in the Buildroot tree if a symbol is reported unknown,
  then save.
