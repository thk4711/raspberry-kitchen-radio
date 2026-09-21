# Buildroot appliance image (Raspberry Pi 3A+)

This document is the reference for the **minimal, fast-booting Buildroot
appliance image** for the PiSonic — the supported way to run the
radio. The image boots straight into the radio with no general-purpose OS
underneath.

## Overview

- **Target:** Raspberry Pi 3A+ — BCM2837 / Cortex-A53, 512 MB, WiFi only (no
  Ethernet).
- **Architecture:** 32-bit ARMv7 (`BR2_arm`, `cortex_a53`, NEON-VFPv4), `bcm2709`
  kernel, `zImage`. Built from Buildroot's proven `raspberrypi3_defconfig` base.
- **Init system:** BusyBox init (no systemd), for a faster boot.
- **Media backends built from source:** `shairport-sync` (AirPlay 2), `nqptp`,
  and `go-librespot` are all compiled by Buildroot and referenced on `PATH`. The
  repository ships no prebuilt media binaries.
- **Bluetooth A2DP source:** the on-chip Bluetooth radio is enabled and the
  image builds BlueZ (`bluez5_utils` + audio plugins) and `bluez-alsa`. A no-PIN
  auto-pairing agent + `bluealsa`/`bluealsa-aplay` (init script `S42bluetooth`)
  turn the radio into an A2DP receiver that is discoverable/pairable whenever no
  device is connected; AVRCP track metadata
  is read by `radio.py` over the `org.bluez` D-Bus API.
- **USB Audio source:** DWC2 runs in peripheral mode and a BusyBox service creates
  a playback-only UAC1 ConfigFS gadget. `alsaloop` with libsamplerate bridges the
  host stream to the shared ALSA/I2S output. The USB-A port uses D+/D−/GND only;
  see [`hardware.md`](hardware.md#usb-audio-gadget-wiring) before connecting it.
- **Build host:** the image is built **natively on an x64 (amd64) Debian (or
  Ubuntu) host** using a stock Buildroot checkout plus this repository's
  `buildroot/external/` directory as the `BR2_EXTERNAL` tree. There is no Docker
  involved. Only flashing the SD card is done on your workstation.

## Prerequisites

- An **x64 (amd64) Debian/Ubuntu build host**. The scripted flow installs the
  Buildroot host prerequisites for you; for a manual build install
  build-essential, bison, flex, libncurses-dev, rsync, cpio, unzip, bc, python3,
  git, wget and file.
- Disk and time: the first build compiles a full cross-toolchain, the kernel and
  Go, so it takes a while and needs several GB free. A shared download cache
  (reused across builds via `BR2_DL_DIR`) makes rebuilds much faster.

No cross-compilers need to be installed by hand — Buildroot fetches the Bootlin
external toolchain and builds `host-go` itself.

## Build it (scripted — recommended)

The helper script in [`../buildroot/`](../buildroot/) builds a generic image. Run
it on the amd64 Debian host from the root of a fresh clone of this repository:

```bash
# Build (or rebuild) the image. Installs the host build packages via apt,
#    fetches a pinned stock Buildroot, applies radio_rpi3_defconfig, and compiles.
./buildroot/build.sh
./buildroot/build.sh --clean         # make clean, then rebuild
./buildroot/build.sh --dirclean      # wipe output/, then full rebuild
./buildroot/build.sh --no-apt        # skip the apt host-package step
```

Buildroot `2026.05.2` is pinned to commit
`72d9d4fa636a371ef9eb99c92a735ce9f6d829d5`. The script validates both newly
cloned and existing directories before building: the directory must be a Git
checkout at that exact commit with no staged, modified, or untracked files.
This prevents a reused build directory from silently changing the firmware
source. To deliberately test a patched or different checkout, use
`--allow-unverified-buildroot`; this development-only escape hatch prints
warnings instead of weakening the default. The requested and actual Buildroot
revision and the radio repository commit are printed in `output/radio-build.log`,
and the two actual commits are embedded in `/etc/radio-release.json`.

`build.sh` requires and prints both the installation image
(`output/images/sdcard.img`) and versioned update package
(`output/images/pisonic-<version>.swu`), with each size and SHA-256. It
also prints the exact `dd` command for Linux and macOS.

The repository-contained `scripts/build_image.py` orchestrator can additionally
copy both artifacts to timestamped, checksum-verified output names. It executes
locally by default and therefore does not require SSH. Copy
`scripts/build-image.example.ini` to the git-ignored `scripts/build-image.ini`
to keep recurring paths and options without machine-specific repository
defaults. Remote execution is opt-in (`execution = remote`) and requires an SSH
host and staging root; it uses `rsync`, batch-mode SSH, and `scp`. Command-line
values take precedence over the optional INI file. Run
`python3 scripts/build_image.py --help` or see the
[beginner guide](build-from-scratch.md#optional-collect-named-artifacts-locally-or-build-remotely).

Override the build locations with environment variables if the defaults do not
suit your host:

```bash
BUILDROOT_DIR=~/br/buildroot BR2_DL_DIR=~/br/dl BUILDROOT_VERSION=2026.05.2 \
    ./buildroot/build.sh
```

The build contains generic defaults and no WiFi credentials. Device-specific
settings are supplied after flashing through `radio-config.txt`, so one image
can be reused for multiple radios without rebuilding.

## Build it (manual)

If you prefer to drive Buildroot directly, clone stock Buildroot on the build
host and point it at this repository's external tree. Use environment variables
for the locations so nothing is hard-coded:

```bash
# On the amd64 Debian host. Adjust these to your layout:
export BUILDROOT_DIR=$HOME/embedded/buildroot          # stock Buildroot checkout
export BR2_DL_DIR=$HOME/embedded/dl                    # shared download cache
export REPO_DIR=$HOME/embedded/radio-repo              # this repo on the host

cd "$BUILDROOT_DIR"
make BR2_EXTERNAL="$REPO_DIR/buildroot/external" radio_rpi3_defconfig
make -j"$(nproc)"
```

The resulting release artifacts are `output/images/sdcard.img` and
`output/images/pisonic-<version>.swu`. To change kernel or Buildroot
options interactively:

```bash
cd "$BUILDROOT_DIR"
make BR2_EXTERNAL="$REPO_DIR/buildroot/external" menuconfig
```

Some `BR2_PACKAGE_*` option symbols may differ slightly by Buildroot release; if
a symbol is reported unknown, run `make menuconfig` and save.


## Flash and first boot

Find the target device, then write the image (**double-check the device — `dd`
overwrites it**):

```bash
# Linux
lsblk
sudo dd if=output/images/sdcard.img of=/dev/sdX bs=4M oflag=direct conv=fsync

# macOS
diskutil list
sudo dd if=sdcard.img of=/dev/rdiskN bs=4m
```

Before booting, edit `radio-config.txt` on the flashed card's FAT boot partition
as described below. Log in over SSH once WiFi is up and SSH has been explicitly
enabled. The HDMI `tty1` login remains visible, but the
USB port is dedicated to USB Audio and cannot accept a keyboard:

```text
user:     root
password: <the root_password value from radio-config.txt>
```

### Validate on the target

```sh
# Radio processes
ps | grep -E 'mpd|radio.py|shairport-sync|nqptp|go-librespot|bluetoothd|bluealsa'
#   expect: mpd, python3 /opt/pisonic/radio.py,
#           nqptp, shairport-sync, go-librespot,
#           bluetoothd, bluealsa, bluealsa-aplay

# Service layout
ls -l /etc/init.d/S50mpd /etc/init.d/S90radio
ls -l /etc/init.d/disabled

# Module tools (real kmod, not BusyBox — see "How it's built")
ls -l /sbin/modprobe /sbin/insmod /sbin/depmod /usr/bin/kmod

# WiFi firmware, interface and association
find /lib/firmware -type f | grep -Ei 'brcmfmac|cyfmac|bcm434'
ip addr show wlan0
/etc/init.d/S41wlan restart

# Logs
# This appliance produces no persistent logs by design (see "Logging and
# debugging" below): there is no syslog daemon, so `logread` is empty and
# `/var/log` is a symlink to the RAM-backed /tmp. To see anything, enable
# logging as described in that section. The radio.py console output goes to the
# tty1/getty session that S90radio's supervisor runs under.
dmesg | grep -Ei 'brcmfmac|wlan|i2c|error'   # kernel ring buffer (RAM)

# Hardware
i2cdetect -y 1          # ADS1115 @ 0x48
aplay -l                # DAC present
```

These manual hardware checks are also available as an optional automated smoke
suite. It is skipped by default and never runs in CI (it needs the real board);
run it from a repo checkout on the device with:

```sh
pytest -m hardware      # i2c ADC, ALSA DAC, SPI display, MPD, AirPlay/Spotify, amp GPIO 26
```

To debug the app manually, stop the service and run it in the foreground with
verbose logging (Python app + all media backends) turned on:

```sh
/etc/init.d/S90radio stop
cd /opt/pisonic
RADIO_LOG_LEVEL=DEBUG \
RADIO_PROCESS_LOG_DIR=/tmp/radio-proc-logs \
RADIO_NQPTP_BINARY=/usr/bin/nqptp \
RADIO_AIRPLAY_BINARY=/usr/bin/shairport-sync \
RADIO_SPOTIFY_BINARY=/usr/bin/go-librespot \
RADIO_SPOTIFY_CONF=/tmp/go-librespot/config.yaml \
RADIO_SPOTIFY_CONFIG_ARG=--config_dir \
/usr/bin/python3 radio.py
# Backend stdout/stderr then appear under /tmp/radio-proc-logs/<name>.log
# (size-capped, truncated on each restart). Without RADIO_PROCESS_LOG_DIR the
# backends' output is discarded to /dev/null (the appliance default).
```

See [Logging and debugging](#logging-and-debugging) for the full policy, every
knob, and how to make logging persistent across reboots.

## Logging and debugging

**By design this appliance produces (almost) no logs, and nothing is written to
the SD card.** It runs 24/7 from a small, wear-sensitive SD card with a
RAM-backed `/tmp`, so the default is to discard output rather than accumulate
it. This section explains that policy and exactly how to turn logging back on
when you need to debug.

### What the default (silent) image does

- **No syslog daemon.** `syslogd`/`klogd`/`rsyslog`/`sysklogd` are not enabled
  in `radio_rpi3_defconfig`, so `/var/log/messages` is never created and
  `logread` returns nothing.
- **`/var/log` → `/tmp` (tmpfs).** `board/radio/post-build.sh` makes `/var/log`
  a symlink to the RAM-backed `/tmp`, so even a component that ignores the
  policy and writes to `/var/log` lands in RAM and vanishes on reboot — it can
  never fill the SD card.
- **Media backends → `/dev/null`.** `radio.py` launches `nqptp`,
  `shairport-sync` and `go-librespot`; by default their stdout/stderr are
  discarded (`lib/utilities.py`).
- **Backends quieted at the source too:** MPD `log_file "/dev/null"` +
  `log_level "warning"` (`/etc/mpd.conf`); go-librespot `log_level: error`
  (`lib/spotify_service/`); shairport-sync `log_verbosity = 0`
  (`lib/airplay_service/airplay.conf`).
- **Python app log level is `ERROR`.** `S90radio` exports
  `RADIO_LOG_LEVEL=ERROR`; the app's own log lines go to the tty the supervisor
  runs under, not to a file.
- **WiFi (`S41wlan`) → `/dev/null`** by default.
- **Bluetooth (`S42bluetooth`) → `/dev/null`** by default (override with
  `S42BLUETOOTH_LOG=/tmp/S42bluetooth.log` to watch pairing-mode transitions).
  `bluetoothd` (`S40bluetoothd`) and `bluealsa` write nothing persistent.
- The only routine on-disk writes are tiny, bounded, and on tmpfs: chrony
  (`logdir /tmp`) and `provision-from-boot` (`/tmp/provision-from-boot.log`).
- Kernel messages still go to the in-RAM ring buffer (`dmesg`); the console is
  quieted via `cmdline.txt` (`quiet loglevel=3`).

### Bounded recovery summary

The exception to the no-persistent-log policy is
`/data/operations/summary.json`: a root-written, world-readable, fixed-schema
recovery summary capped at 16 KiB and the latest 32 events. It contains only the
kernel boot ID, firmware slot, clean/unclean reboot classification, bounded
`radio.py` process-exit and heartbeat-timeout restart counters, and the last
firmware-health result. It never accepts arbitrary messages, credentials, URLs,
station/source metadata, network identifiers, or hostnames.

`S15operational-summary` opens a boot record and marks a normal SysV shutdown as
clean. If the previous boot was not marked clean, the next boot records
`unclean`. This intentionally conservative label covers power loss, watchdog or
hard reset, and crashes: the Pi/Buildroot combination does not provide a
dependable reset-cause value that distinguishes those cases. `S90radio` records
process exits and heartbeat-triggered recoveries, while firmware health records
accepted trials, failed trials, and automatic rollback. Recorder failures are
best-effort and can never block boot, restart, health acceptance, or rollback.

Inspect the summary on target with:

```sh
cat /data/operations/summary.json
stat -c '%a %s %n' /data/operations/summary.json
```

The Maintenance diagnostics download includes this safe summary as
`snapshots/operational-summary.json`. Routine logs remain volatile and are not
copied into it.

### Environment variables (the logging knobs)

These are read by the Python app / `lib/utilities.py`:

| Variable | Default | Effect |
| --- | --- | --- |
| `RADIO_LOG_LEVEL` | `ERROR` | Python app log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`). Also settable via a `[logging] level` entry in `radio.conf`; the env var wins. |
| `RADIO_PROCESS_LOG_DIR` | *(unset → `/dev/null`)* | When set to a directory, each media backend logs to `<dir>/<name>.log`. `none` (or unset) discards to `/dev/null`. |
| `RADIO_PROCESS_LOG_MAX_BYTES` | `262144` (256 KiB) | Per-backend log size cap. The file is **truncated on each (re)start** and re-truncated when it exceeds this size, so it can never exhaust tmpfs/RAM. |
| `S41WLAN_LOG` | `/dev/null` | Point WiFi bring-up logging at a file (e.g. `/tmp/S41wlan.log`) to debug association/DHCP. |
| `S42BLUETOOTH_LOG` | `/dev/null` | Point Bluetooth bring-up logging at a file (e.g. `/tmp/S42bluetooth.log`) to watch pairing-mode transitions and daemon startup. |

### Enable logging temporarily (run the app in the foreground)

The quickest way to see everything while reproducing a problem — stop the
service and run the app yourself with verbose logging and backend logs enabled:

```sh
/etc/init.d/S90radio stop
cd /opt/pisonic
RADIO_LOG_LEVEL=DEBUG \
RADIO_PROCESS_LOG_DIR=/tmp/radio-proc-logs \
RADIO_PROCESS_LOG_MAX_BYTES=1048576 \
RADIO_NQPTP_BINARY=/usr/bin/nqptp \
RADIO_AIRPLAY_BINARY=/usr/bin/shairport-sync \
RADIO_SPOTIFY_BINARY=/usr/bin/go-librespot \
RADIO_SPOTIFY_CONF=/tmp/go-librespot/config.yaml \
RADIO_SPOTIFY_CONFIG_ARG=--config_dir \
/usr/bin/python3 radio.py

# In another SSH session, tail the backend logs:
tail -F /tmp/radio-proc-logs/*.log
```

The `RADIO_*_BINARY` / `RADIO_SPOTIFY_*` vars mirror what `S90radio` normally
exports, so the foreground run behaves like the service.

To also raise a specific backend's own verbosity, edit its config while
debugging: MPD `log_level "verbose"` in `/etc/mpd.conf`; go-librespot
`log_level: debug` (generated at `/tmp/go-librespot/config.yaml`);
shairport-sync `log_verbosity = 1..3` in `lib/airplay_service/airplay.conf`.

### Enable logging under the service (survives until you revert)

To keep logging on while the app runs as the normal service, add the env vars
to the supervisor block in `/etc/init.d/S90radio` (the `cat > "$WRAP"` heredoc),
next to the existing `export RADIO_LOG_LEVEL=...` line, then
`/etc/init.d/S90radio restart`:

```sh
export RADIO_LOG_LEVEL="${RADIO_LOG_LEVEL:-DEBUG}"
export RADIO_PROCESS_LOG_DIR="${RADIO_PROCESS_LOG_DIR:-/tmp/radio-proc-logs}"
```

Because `/tmp` is tmpfs, these logs are **still lost on reboot** and remain
size-capped, so this is safe to leave on temporarily without risking the SD
card. If persistent diagnostic logging is temporarily unavoidable, use a
dedicated bounded directory under `/data` rather than a firmware slot, for
example `/data/diagnostics/radio-proc-logs`, and keep
`RADIO_PROCESS_LOG_MAX_BYTES` conservative. Such logs are not part of the
supported persistence inventory and are not cleaned automatically; remove them
and revert the setting after diagnosis. See [Persistent data](persistent-data.md).

> If you truly need centralized syslog, enable a BusyBox `syslogd`/`klogd`
> (`BR2_PACKAGE_BUSYBOX` config) and add an `S01logging` init script — but this
> reverses the "no persistent logs" design constraint below, so prefer the
> tmpfs-based knobs above unless you have a specific reason.

## How it's built

### Reproducible builds / pinned sources

Every input that affects the produced image is pinned so a given commit rebuilds
byte-for-byte (network mirrors permitting):

- **Buildroot revision** — `buildroot/build.sh` fetches a *pinned* stock
  Buildroot release (not `master`); see the version/hash pin near the top of
  `build.sh`. Never float this to a moving branch.
- **Kernel + RPi firmware** — pinned in
  `buildroot/external/configs/radio_rpi3_defconfig` (the 6.12.x kernel pin and
  the proven 32-bit base). The RULE at the top of that file forbids swapping the
  arch/toolchain/kernel-source out from under the proven base.
- **Media backends compiled from source, with download hashes:**
  - `go-librespot` — the package pins an immutable upstream tag. Buildroot's Go
    download post-processor runs `go mod vendor`, repacks the source and modules
    as `go-librespot-v0.9.0-go2.tar.gz`, and verifies that final archive against
    `buildroot/external/package/go-librespot/go-librespot.hash`. The `-go2`
    suffix is Buildroot's archive-format version, not the host Go version. The
    hash file also verifies `LICENSE` when Buildroot collects legal information.
  - `shairport-sync` / `nqptp` — pinned by the upstream Buildroot packages
    selected in the defconfig (moving them means bumping the pinned Buildroot
    revision above).
- **App version** — `lib/_version.py` (`__version__`) is the single source of
  truth, surfaced on the boot splash subtitle and via `radio.py --version`, so a
  running unit reports exactly which build it is.

When cutting a release (see `CHANGELOG.md`): bump `lib/_version.py` +
`pyproject.toml`, record the pinned Buildroot revision and the backend source
hashes above in the changelog entry, then create an **annotated** `vX.Y.Z` tag.
Run `python3 scripts/check-release-consistency.py` after tagging and before
publishing. The checker verifies the versions, changelog date, artifact naming,
generated rootfs/SWUpdate metadata, documentation examples, and tag type/date.
The already-published lightweight `v0.2.0` tag is the sole historical exception
and remains unchanged rather than rewriting public history. That tuple
(Buildroot revision + defconfig + backend hashes + app version) fully
identifies a build.

### External-tree layout

```
buildroot/
└─ external/                # BR2_EXTERNAL tree
   ├─ external.desc / external.mk / Config.in
   ├─ configs/radio_rpi3_defconfig   # the working 32-bit Pi 3 base
   ├─ package/             # go-librespot (pinned) and radio-app
   │                       #   (nqptp, python-smbus2, python-gpiozero,
   │                       #    python-colorzero come from upstream Buildroot)
   └─ board/radio/
      ├─ config.txt        # built-in headphones, optional I2S DACs, DWC2 peripheral,
      │                    #   Bluetooth on PL011 (NOT pi3-miniuart-bt) + enable_uart,
      │                    #   boot_delay=0 / initial_turbo
      ├─ cmdline.txt       # kernel command line (quiet loglevel=3, tty1 only)
      ├─ linux-i2c.fragment       # positive fragment (APPLIED): expose the ADS1115 I2C bus
      ├─ linux-watchdog.fragment  # positive fragment (APPLIED): BCM2835 watchdog + zram
      ├─ linux-bluetooth.fragment # positive fragment (APPLIED): CONFIG_BT + BT UART HCI
      ├─ linux-usb-audio.fragment # positive fragment (APPLIED): DWC2 + ConfigFS UAC1
       ├─ device_table.txt   # fakeroot ownership for radio-web writable dirs
      ├─ post-build.sh     # mpd + radio-web users, tmpfs fstab, hostname, service layout
      └─ rootfs-overlay/   # init.d scripts (incl. S41wlan async WiFi and
                           #   S42bluetooth A2DP receiver), asound.conf, mpd.conf,
                           #   bluetooth/main.conf, default/bluetoothd,
                           #   dbus-1/system.d/bluetooth-radio.conf,
                           #   wpa_supplicant.conf, network/interfaces
```

### Defconfig summary

The working defconfig is `buildroot/external/configs/radio_rpi3_defconfig`. Its
key decisions:

```text
BR2_arm=y
BR2_cortex_a53=y
BR2_ARM_FPU_NEON_VFPV4=y
BR2_TOOLCHAIN_EXTERNAL=y
BR2_TOOLCHAIN_EXTERNAL_BOOTLIN=y
BR2_TOOLCHAIN_EXTERNAL_BOOTLIN_ARMV7_EABIHF_GLIBC_BLEEDING_EDGE=y

BR2_LINUX_KERNEL=y
BR2_LINUX_KERNEL_DEFCONFIG="bcm2709"
BR2_LINUX_KERNEL_INTREE_DTS_NAME="broadcom/bcm2710-rpi-3-b broadcom/bcm2710-rpi-3-b-plus broadcom/bcm2710-rpi-cm3"

BR2_INIT_BUSYBOX=y
BR2_ROOTFS_DEVICE_CREATION_DYNAMIC_MDEV=y

BR2_PACKAGE_BUSYBOX_SHOW_OTHERS=y
BR2_PACKAGE_KMOD=y
BR2_PACKAGE_KMOD_TOOLS=y
BR2_PACKAGE_XZ=y
BR2_PACKAGE_HOST_KMOD_XZ=y

BR2_PACKAGE_RPI_FIRMWARE=y
BR2_PACKAGE_RPI_FIRMWARE_CONFIG_FILE="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/config.txt"
BR2_PACKAGE_RPI_FIRMWARE_CMDLINE_FILE="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/cmdline.txt"

BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI=y
BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI_WIFI=y
BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI_BT=y     # Bluetooth firmware (A2DP source)
BR2_PACKAGE_WPA_SUPPLICANT=y

# Bluetooth A2DP audio source (BlueZ stack + bluez-alsa receiver).
BR2_PACKAGE_BLUEZ5_UTILS=y
BR2_PACKAGE_BLUEZ5_UTILS_PLUGINS_AUDIO=y
BR2_PACKAGE_BLUEZ5_UTILS_CLIENT=y
BR2_PACKAGE_BLUEZ_ALSA=y

# USB Audio adaptive ALSA bridge.
BR2_PACKAGE_ALSA_UTILS_ALSALOOP=y
BR2_PACKAGE_LIBSAMPLERATE=y
BR2_PACKAGE_RADIO_EQUALIZER=y

BR2_ROOTFS_OVERLAY="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/rootfs-overlay"
BR2_ROOTFS_POST_BUILD_SCRIPT="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/post-build.sh"
BR2_ROOTFS_POST_IMAGE_SCRIPT="board/raspberrypi3/post-image.sh"
```

`BR2_PACKAGE_RADIO_EQUALIZER` builds the project-owned, MIT-licensed LADSPA
plugin from `buildroot/external/package/radio-equalizer/` and installs
`/usr/lib/ladspa/radio_equalizer.so`. ALSA's built-in LADSPA PCM hosts it; the
web helper generates the selected filters in `/etc/asound.conf`. See
[Parametric equalizer](equalizer.md#developer-architecture) for the signal path,
control ABI and target qualification procedure.

The firmware `config.txt` enables the hardware interfaces and built-in headphone
output and applies the boot-speed options; `cmdline.txt` keeps the HDMI/keyboard console
while suppressing kernel chatter:

```text
# config.txt (key lines)
kernel=zImage
gpu_mem_512=100
# Bluetooth on the PL011 UART: NOT pi3-miniuart-bt (that breaks A2DP)
enable_uart=1
dtoverlay=dwc2,dr_mode=peripheral
disable_splash=1
boot_delay=0
initial_turbo=30
dtparam=i2c_arm=on
#dtparam=i2s=on              # enabled when an external I2S DAC is selected
dtparam=spi=on
dtparam=audio=on
#dtoverlay=iqaudio-dacplus    # selected by the web Audio page when needed

# cmdline.txt
root=/dev/mmcblk0p2 rootwait console=tty1 quiet loglevel=3 logo.nologo
```

Peripheral mode dedicates the Pi 3A+ USB-A connector to USB Audio, so a USB
keyboard is no longer available as a recovery path. Keep WiFi/SSH access working
or configure a separate serial console. The wiring must omit VBUS/+5 V.

### Boot order (BusyBox init) and service layout

```text
BusyBox init -> /etc/inittab
  sysinit:  mount /proc; remount,rw /; mkdir /dev/{pts,shm}; mount -a (/data + tmpfs /tmp)
  sysinit:  /usr/sbin/radio-boot-splash     # paint the branded SPI splash ASAP,
                                             #   launched DETACHED so it renders
                                             #   in parallel and never blocks boot
  sysinit:  /usr/sbin/radio-persistent-boot # validate writable ext4 p4; then link,
                                             #   migrate, provision radio-config.txt,
                                             #   and render firmware-owned files
  sysinit:  hostname -F /etc/hostname        # reads provisioned hostname
  sysinit:  /etc/init.d/rcS
  S10mdev / mounts, tmpfs on /tmp
  S11modules
  S12data-resize        # safely grow only final persistent-data partition p4
  S13zram              # compressed RAM swap (OOM protection on the 512 MB board)
  S14watchdog          # arm the BCM2835 hardware watchdog (/dev/watchdog)
  S30dbus-daemon       # system bus (required for shairport-sync AirPlay control + BlueZ)
  S39usb-audio         # ConfigFS UAC1 gadget + supervised alsaloop bridge to I2S
  S40network           # brings up lo (WiFi is NOT here — non-blocking)
  S40bluetoothd        # bluetoothd (bluez5_utils); --experimental via /etc/default/bluetoothd
  S41wlan              # wpa_supplicant + udhcpc (sends hostname) for wlan0, IN THE BACKGROUND
                       #   (log discarded to /dev/null by default;
                       #    set S41WLAN_LOG=/tmp/S41wlan.log to debug)
  S42bluetooth         # no-PIN auto-pair agent + connection-gated pairing mode +
                       #   bluealsa (A2DP receiver) + bluealsa-aplay (-> ALSA default)
  S49chronyd           # sets wall clock for HTTPS/TLS clients on the RTC-less Pi
  S50avahi-daemon      # mDNS for AirPlay / Spotify discovery
  S50dropbear          # SSH server (disabled unless explicitly enabled)
  S50mpd               # Music Player Daemon (/etc/mpd.conf)
  S79radio-helper      # root-owned privileged helper for the web admin UI
                       #   Listens on a local unix
                       #   socket (/run/radio-helper.sock, chowned root:radio-web
                       #   0660) and performs only a fixed whitelist of actions
                       #   (restart radio/MPD, reboot, shutdown, set hostname,
                       #   set time). Starts BEFORE S80radio-web.
  S80radio-web         # web admin interface (python3 -m radio_web). Runs as the
                       #   unprivileged 'radio-web' user (Phase 7 privilege
                       #   split) and delegates privileged ops to S79radio-helper.
                       #   Runs independently of S90radio and survives its
                       #   restarts/freeze recovery.
  S90radio             # supervises radio.py (crash restart w/ backoff +
                       #   freeze-watcher), which launches nqptp,
                       #   shairport-sync and go-librespot itself
                       #   (Bluetooth and the USB bridge are init-owned; radio.py
                       #    observes their state and performs source arbitration)
  S99firmware-health   # background local trial-health confirmation and rollback
```

Each root slot contains immutable `/etc/radio-release.json` metadata generated
from `lib/_version.py` and the fixed Pi 3A+ hardware revision. The privileged
helper derives the non-running partition from the kernel slot/root pair and mounts
it only at `/mnt/firmware-other-ro` with `ro,noload,nodev,nosuid,noexec` while
validating that metadata. Neither HTTP nor the helper protocol accepts a device,
slot, path, or mountpoint. A password-confirmed manual switch transactionally sets
the current slot as `previous_slot`, selects the validated other slot, resets the
boot counter, enables a trial, verifies the environment, and only then reboots.

The same metadata declares the firmware's persistent-schema writer and reader
capabilities. `/data/radio/schema-version` is the format last written, while
`schema-compatibility.json` records the oldest reader that may safely consume it.
Configuration changes should be additive: INI readers ignore unknown keys, and a
renamed key must remain readable under both names for at least one rollback
generation. Early boot runs ordered, locked migrations before services start,
publishes schema metadata only after success, and leaves a root-only pre-migration
backup under `/data/update/config-backups`. The backup is accepted only after the
trial firmware is marked good and the latest accepted generation remains protected.
An update or manual switch is refused when the retained firmware cannot read the
shared data. Irreversible migrations are not part of the normal update policy and
are rejected before installation because they would also defeat automatic fallback.

#### Early boot splash (non-blocking)

The SPI panel is a bare ST7789 with no kernel framebuffer, so there is no
`fbi`-style console image: the display can only be driven through the app's
Python driver. To avoid a dark panel for the several seconds it takes the full
app (`S90radio`, dead last) to come up, an **early boot splash** paints one
branded frame from the very first useful `sysinit` step:

- `/usr/sbin/radio-boot-splash` runs from `inittab` sysinit, right after the
  rootfs is writable + `/tmp` (tmpfs) exists + `/dev` (incl. `/dev/spidev*`) is
  populated, and *before* provisioning/hostname/rcS.
- It **launches the renderer detached** (`python3 … &`) and returns in a few
  ms, so BusyBox init does **not** block on the ~1 s Python startup — the
  splash (`lib/display/boot_splash.py`) renders in parallel with
  provisioning/rcS and adds **no measurable serial boot latency**.
- The renderer draws the same dark-gradient "RADIO" splash the app itself shows
  (shared fonts + `[ui]` colours), sets the backlight on, then releases SPI and
  the GPIO lines **without blanking the backlight**. The image stays lit until
  `radio.py`'s `DisplayController` grabs the same pins at the end of boot and
  repaints — a seamless handoff, no SPI/GPIO contention.
- **Best-effort:** any failure (missing interpreter/module, wiring fault) is
  non-fatal; the launcher always exits 0 and the renderer logs to
  `/tmp/radio-boot-splash.log` (tmpfs — no persistent logs), so it can never
  block boot.

### Self-recovery and reliability

The radio is an appliance: it must never stay unusable. Recovery is layered,
fastest first, so a cheap targeted restart is tried before a full reboot:

1. **App crash** — `radio.py` exits (uncaught exception, OOM kill, `SIGSEGV`).
   `S90radio`'s supervisor loop restarts it with **exponential backoff**
   (3 → 6 → 12 → … → 60 s cap, reset to 3 s after any run that stays up ≥ 60 s),
   so a deterministic crash cannot become a tight, CPU-burning restart loop.
   Before each (re)launch the supervisor also **reaps orphaned media backends**
   (`nqptp`, `shairport-sync`, `go-librespot`) the previous instance may have
   left behind, so duplicates never accumulate.
2. **App freeze (hung but alive)** — a wedged D-Bus/ALSA/SPI/HTTP call inside
   the metadata loop keeps the process alive but stops it doing anything. The
   plain crash loop never sees this. `radio.py` refreshes a heartbeat file
   (`/tmp/radio.alive`, `[watchdog]` in `radio.conf`; env override
   `RADIO_HEARTBEAT_FILE`) on **every** metadata-loop iteration; the
   **freeze-watcher** companion in `S90radio` kills the frozen process when the
   heartbeat is stale for `RADIO_HEARTBEAT_TIMEOUT` seconds (default 30, ≈10× the
   3 s loop), which then triggers case 1. Both the freeze-watcher and the
   `stop`/`restart` path kill **only the supervised player's process group**
   (the supervisor wrapper recorded in `/run/radio.pid` plus its `radio.py`
   child), never `killall python3`, so the separate `S80radio-web` service is
   left untouched. The media backends (`shairport-sync`, `nqptp`,
   `go-librespot`) are still reaped by name because they have no other owner.
3. **Whole-system hang** — a kernel lockup, stuck SD/I-O path or wedged WiFi
   driver that also takes the userspace supervisors down. `S14watchdog` arms the
   **BCM2835 hardware watchdog** via the BusyBox `watchdog` applet
   (`watchdog -T 60 -t 15 /dev/watchdog`). If the applet stops feeding
   `/dev/watchdog`, the SoC **hard-resets** the board after ~60 s. A clean
   `stop`/shutdown magic-closes the device so a normal reboot never triggers a
   reset.

**Memory pressure.** `S13zram` sets up a compressed RAM swap device (default
192 MB, `zstd`) so the kernel can page out cold pages instead of OOM-killing a
media backend or `radio.py`. It lives entirely in RAM, so it adds no SD-card
wear and vanishes on reboot — consistent with the "no persistent writes"
policy. Kernel support for both the watchdog and zram comes from the
positive-only `board/radio/linux-watchdog.fragment`; `/dev/watchdog` also needs
`dtparam=watchdog=on` in `config.txt`.

Quick on-target checks:

```sh
ls -l /dev/watchdog          # present -> hardware watchdog available
cat /proc/swaps              # shows /dev/zram0 as swap
cat /tmp/radio.alive         # heartbeat file (mtime advances every ~3 s)
# Simulate a crash: kill radio.py; the supervisor restarts it within the
# backoff window. (Target radio.py specifically — do not "killall python3",
# which would also take down the S80radio-web service.)
kill "$(pgrep -f radio.py)"
# Simulate a freeze: pause the app; the freeze-watcher kills+restarts it
# after RADIO_HEARTBEAT_TIMEOUT.
kill -STOP "$(pgrep -f radio.py)"
```


### Provisioning a prebuilt image from the SD card (`radio-config.txt`)

Every build produces the same generic image. Device-specific settings belong in
the FAT ("boot") partition's plain-text `radio-config.txt` — the same idea as
Raspberry Pi OS's `/boot` provisioning.

The image ships a `radio-config.txt` template on the FAT partition (added by
`board/radio/post-image.sh`). Credential placeholders are commented and the
generic root account is locked. Edit it directly before the first boot:

```ini
# wifi_ssid=<your-wifi-name>
# wifi_psk=<your-private-passphrase>
# hostname=pisonic     # also the AirPlay / Spotify device name
# root_password=<your-unique-device-password>
enable_ssh=0                 # 1 enables the SSH server
timezone=UTC                 # zoneinfo name
# ntp_server=pool.ntp.org
```

Each active line is automatically commented on the FAT partition **only after
its individual setting has been successfully persisted**. This lets later
web-interface changes persist while ensuring a failed write or invalid value
remains active and is retried on the next boot. To apply a stored boot setting
again, edit its value, remove the leading `#`, and reboot.

- **Applied first, when active settings are present.**
  `/usr/sbin/provision-from-boot` runs from an `inittab` **sysinit** line
  *before* `hostname -F` and *before* `rcS` (WiFi/chrony/dropbear), so every
  consumer reads newly provisioned values. It is the earliest step after the
  rootfs is remounted read-write and `/tmp` exists.
- **Overrides the generic image defaults**; values persist on the rootfs.
- **One-shot, whitelist parser with retry safety.** The FAT partition is mounted
  read-write for this early step. A recognised assignment is commented only
  after its durable apply succeeds; failed or invalid assignments remain active
  for a later boot retry. Unknown keys are ignored and the file is never
  sourced/eval'd. A missing or invalid file never blocks boot — built-in or
  previously persisted values are used and the outcome is logged to
  `/tmp/provision-from-boot.log`.
- **WiFi** is rewritten as the strict `network={ … key_mgmt=WPA-PSK }` block the
  stripped `wpa_supplicant` requires (PSK 8..63 chars). `wifi_country` (an
  ISO-3166 alpha-2 code, e.g. `DE`) is **validated and persisted to
  `/etc/radio/wifi-country`**, then applied at bring-up by the `S41wlan` init script
  via `iw reg set`. The country is set with `iw` (not `wpa_supplicant`) because
  this `wpa_supplicant` is built without `CONFIG_CTRL_IFACE` and rejects a
  `country=` line inside `wpa_supplicant.conf`. An unset or invalid value keeps
  the safe worldwide default.
- **`enable_ssh=0`** persists the disabled setting and writes
  `/run/provision-disable-ssh`; the overlay
  `S50dropbear` checks it and skips starting SSH (race-free, since sysinit runs
  before `S50`).
- **Root login is locked in the generic image.** `root_password` must be an
  explicit, non-placeholder value. Its hash is persisted only after `chpasswd`
  succeeds. `enable_ssh=1`, the web UI, and `S50dropbear` all fail closed until
  that persisted hash exists. Known examples such as `changeme`, `MyNetwork`,
  and `my-wifi-password` are rejected. Missing or rejected required settings are
  shown as a setup warning on the display and web dashboard without exposing
  submitted credential values.
- **`timezone`** (a zoneinfo name such as `Europe/Berlin`) symlinks
  `/etc/localtime` to the matching entry and writes `/etc/timezone`. It requires
  the tz database (`BR2_TARGET_TZ_INFO`, enabled in the defconfig); otherwise the
  appliance — and the display's top status-bar clock — stays on UTC. Because it
  runs before `S90radio`, the radio app starts already in the local zone.
- The **sound card** is selected after the first network boot from the web
  interface's Audio page. This updates both the FAT `config.txt` overlay and the
  matching ALSA/MPD/radio settings before requesting a reboot.

#### Image assembly and partition layout

`board/radio/post-image.sh` is a self-contained replacement for Buildroot's
stock `board/raspberrypi/post-image.sh`. It builds `sdcard.img` with `genimage`
from `board/radio/genimage.cfg.in`, and:

- Reserves the first **8 MiB** for the MBR and two redundant 64 KiB U-Boot
  environments at fixed 1 MiB and 2 MiB offsets, and seeds both copies.
- Creates a fixed **128 MiB** FAT loader partition, equal **768 MiB** ext4 A/B
  firmware slots, and a **128 MiB** seed ext4 data partition as final p4.
- Gives A, B and data distinct deterministic filesystem labels and UUIDs, then
  copies the completed Buildroot rootfs into both initial firmware slots.
- Seeds `/data`, mounts it before provisioning, and links `/etc/radio`, WiFi,
  Dropbear identity and BlueZ state to their persistent locations.
- Mounts the fixed data partition as `/dev/mmcblk0p4`, avoiding dependency on
  optional BusyBox filesystem-label resolution. Early persistent processing is
  skipped unless that mount is writable ext4 p4, preventing accidental writes
  into an unmounted `/data` directory in a firmware slot.
- Places Raspberry Pi firmware/DTBs, `u-boot.bin`, the fixed A/B `boot.scr`, and
  `radio-config.txt` on the FAT loader. U-Boot loads `/boot/zImage` from the
  selected ext4 slot and passes a matching `root=` and `radio.slot=` pair.
- Runs `scripts/verify-audio-catalog.py` before image assembly. The build fails
  if a selectable I2S overlay is absent from the completed image or one of its
  required drivers is disabled in the final kernel `.config`. Driver selections
  are explicit in the positive-only `linux-i2s-audio.fragment`.

The fixed on-card map is:

| Region | Size | Purpose |
| --- | ---: | --- |
| Raw reservation | 8 MiB | MBR plus redundant U-Boot environments at 1 MiB and 2 MiB. |
| `p1` FAT | 128 MiB | Stable Raspberry Pi firmware, DTBs, U-Boot, `boot.scr`, and provisioning file. |
| `p2` ext4 | 768 MiB | Firmware slot A, including its own `/boot/zImage`. |
| `p3` ext4 | 768 MiB | Firmware slot B, including its own `/boot/zImage`. |
| `p4` ext4 | 128 MiB seed | Shared persistent data; expanded to the card's end on early boot. |

`S12data-resize` validates that `p4` is the final partition before growing the
partition and filesystem. It never resizes either firmware slot. Consequently a
normal `.swu` writes only inactive `p2` or `p3`; it contains no partition table,
loader, provisioning file, or data filesystem.

#### U-Boot A/B variables and trial policy

The complete boot state machine, redundant-environment requirements, SWUpdate
transaction, and porting guidance are in the
[firmware-update architecture reference](firmware-update-architecture.md).

The two redundant environments are initialized from `uboot-env.txt`. These
variables form the boot transaction:

| Variable | Meaning |
| --- | --- |
| `active_slot` | Slot A or B selected for the next boot. |
| `previous_slot` | Accepted starting slot to use if the trial fails. |
| `upgrade_available` | `1` while `active_slot` is an unaccepted trial. |
| `bootcount` | Trial attempts consumed by U-Boot. |
| `bootlimit` | Attempts allowed before fallback; shipped as `3`. |
| `rollback_from` | Failed slot recorded when U-Boot falls back, otherwise `none`. |

On every trial boot, `boot.scr` increments and saves `bootcount`. When it exceeds
`bootlimit`, U-Boot selects `previous_slot`, clears the trial state, and records
the failed slot. Slot A maps only to `p2`; slot B maps only to `p3`. U-Boot loads
that slot's `/boot/zImage` and supplies a matching `root=/dev/mmcblk0pN` and
`radio.slot=A|B`. A healthy trial is accepted by `S99firmware-health`, which
clears the trial state only after local checks pass.

For read-only diagnosis, use `fw_printenv` with the six variables above and
inspect `/proc/cmdline`. Do not use `fw_setenv` as a routine operator workflow:
the installer and manual-switch helper commit and verify the complete variable
set transactionally.

#### Persistent paths and compatibility

`/data` is the ext4 filesystem on fixed partition `p4`; it is mounted before
provisioning and services. The early `radio-persistent-boot` coordinator validates
that exact read-write mount, establishes compatibility links, migrates shared
state, applies `radio-config.txt`, and regenerates slot-owned outputs. The
canonical inventory, ownership, image-seeding process and write guarantees are in
[Persistent data](persistent-data.md).

| Persistent location | Compatibility path or purpose |
| --- | --- |
| `/data/radio` | `/etc/radio`; administrator secret, managed INI files, logos, ADC calibration, and schema state. |
| `/data/network/wpa_supplicant.conf` | `/etc/wpa_supplicant.conf`. |
| `/data/identity/dropbear` | `/etc/dropbear`; stable SSH host identity. |
| `/data/bluetooth` | `/var/lib/bluetooth`; retained pairing state. |
| `/data/update/upload` | Bounded unprivileged upload staging and reboot-reconnect status. |
| `/data/update/queue` | Root-only fixed package consumed by the installer. |
| `/data/update/history.json` | Bounded root-owned update history. |
| `/data/update/config-backups` | Root-only migration backups. |
| `/data/update/data-resize` | Guarded p4-resize marker and MBR backup. |

`/etc/radio` is a directory symlink, so same-directory atomic writes beneath it
land in `/data/radio`. `/etc/wpa_supplicant.conf` is a file symlink; its writers
must resolve the target before `mv`/`os.replace` to avoid replacing the symlink.
Both provisioning and the privileged network helper enforce this distinction.

Firmware-owned `/etc/mpd.conf`, `/etc/asound.conf`, and live ALSA state are not
copied between slots. Each firmware regenerates them from persistent profile,
volume, and calibration inputs. Release metadata and persistent schema markers
allow installation and manual switching only when both the target and retained
firmware can safely read the shared state.

#### `.swu` generation and release-artifact validation

The archive's exact format, build validation, runtime selection, and Linux
tooling are detailed in the
[firmware-update architecture reference](firmware-update-architecture.md).

The image build runs `board/radio/build-swu.sh` after the root filesystem is
complete. `scripts/build_firmware_swu.py` creates a deterministic unsigned CRC
CPIO named `pisonic-<version>.swu`. Its first member is `sw-description`;
its second is one deterministic gzip-compressed root filesystem shared by fixed
`slot-a` and `slot-b` selections. The manifest records semantic version,
Pi 3A+ hardware compatibility, persistent-schema contracts, compressed size,
and SHA-256. The builder validates member order and payload integrity and asks
the native `swupdate` checker to parse both selections before atomic publication.

`build.sh` starts a build marker and then runs `validate-artifacts.sh`. Reporting
fails unless `sdcard.img` and exactly one matching, non-empty, newer versioned
`.swu` exist. It prints each path, size, and SHA-256. These hashes detect
accidental corruption; because packages are not signed, they are not an
authenticity guarantee.

#### Firmware recovery paths

The supported recovery order is automatic U-Boot fallback, a password-confirmed
manual switch from Maintenance, and boot-partition `radio-config.txt` recovery
for WiFi/hostname/root-password/SSH access. HDMI exposes `tty1`, but USB Audio
peripheral mode prevents use of a USB keyboard; prepare serial access or enable
SSH before low-level work. Reflashing `sdcard.img` is the final recovery path and
destroys existing `/data`. See the complete operator procedure in
[`firmware-updates.md`](firmware-updates.md).

`post-build.sh` enforces the service layout. The enabled radio scripts include
`S12data-resize`, `S13zram`, `S14watchdog`, `S50mpd`, `S79radio-helper`,
`S80radio-web`, `S90radio` and `S99firmware-health` (all shipped in the board
overlay and `chmod 0755`'d by `post-build.sh`); the competing upstream media scripts are moved to
`/etc/init.d/disabled/`:

```text
/etc/init.d/disabled/S90nqptp
/etc/init.d/disabled/S95mpd
/etc/init.d/disabled/S99shairport-sync
```

`S90radio` points the app at the Buildroot-built backends via environment
variables, so the vendored repo binaries are unused on the image:

```sh
RADIO_NQPTP_BINARY=/usr/bin/nqptp
RADIO_AIRPLAY_BINARY=/usr/bin/shairport-sync
RADIO_SPOTIFY_BINARY=/usr/bin/go-librespot
RADIO_SPOTIFY_CONF=/tmp/go-librespot/config.yaml
RADIO_SPOTIFY_CONFIG_ARG=--config_dir
```

`radio.py` runs from `/opt/pisonic` (installed by the `radio-app`
package). `S90radio` sets these `RADIO_*` overrides to the Buildroot-built
binaries; if unset, the app looks each backend up by name on `PATH`.


## Design constraints

Keep these to avoid regressing the known-good image:

- **Stay 32-bit ARMv7 / `bcm2709` / `zImage`.** Do not switch to aarch64, do not
  use `kernel=Image`, and do not add `arm_64bit=1`. Keep the CPU settings
  (`BR2_arm` / `cortex_a53` / NEON-VFPv4), the RPi firmware, the pinned kernel
  and the upstream Pi 3 DTB set (`bcm2710-rpi-3-b`, `-3-b-plus`, `-cm3`).
- **Keep real `kmod` tools.** The Raspberry Pi kernel installs compressed
  modules (`*.ko.xz`). BusyBox `modprobe` hands compressed files straight to the
  kernel, which expects uncompressed ELF (`Invalid ELF header magic`). Installing
  `kmod` (`BR2_PACKAGE_KMOD`, `KMOD_TOOLS`, `XZ`, `HOST_KMOD_XZ`) fixes this;
  `/sbin/{modprobe,insmod,depmod}` become symlinks to `usr/bin/kmod`. `kmod` is
  not an init system — BusyBox init stays.
- **Media backends are app-owned.** `radio.py` starts `nqptp`, `shairport-sync`
  and `go-librespot`, so the upstream `S90nqptp`, `S99shairport-sync` and the
  duplicate `S95mpd` must stay under `/etc/init.d/disabled/`. Do not leave a
  disabled script under `/etc/init.d/S??*` even with a `.disabled` suffix —
  BusyBox `rcS` still runs every matching regular file. Do not mix app-owned and
  init-script-owned models.
- **Keep the HDMI `tty1` console for diagnostics**, but remember that it has no
  USB keyboard while the connector is in peripheral mode. WiFi/SSH is the normal
  recovery path; configure a serial getty when low-level interactive debugging
  is required.
- **Keep the validated boot-speed options** (`quiet loglevel=3 logo.nologo`,
  `disable_splash=1`, `boot_delay=0`, `initial_turbo=30`, async `S41wlan`) unless
  a change is shown to regress on hardware.
- **Only small positive kernel fragments are applied** for I2C, watchdog,
  Bluetooth, and USB Audio. No broad kernel trim is used: the image deliberately keeps
  the framebuffer/DRM console and other drivers so failed boots stay debuggable.
- **No persistent logs — nothing writes to the SD card.** This is an always-on
  appliance, so logging is minimized and kept off the (small, wear-sensitive)
  SD card. No syslogd/klogd/rsyslog is enabled in the defconfig, so
  `/var/log/messages` is never produced. The app's media backends (`nqptp`,
  `shairport-sync`, `go-librespot`) default their stdout/stderr to `/dev/null`
  in `lib/utilities.py`; set `RADIO_PROCESS_LOG_DIR` (and optionally
  `RADIO_PROCESS_LOG_MAX_BYTES`, default 256 KiB) to opt into size-capped,
  truncate-on-restart file logging for debugging. The backends are also quieted
  at the source: MPD → `log_file "/dev/null"`, go-librespot → `log_level:
  error`, shairport-sync → `log_verbosity = 0`. `S41wlan` defaults its log to
  `/dev/null` (override with `S41WLAN_LOG=/tmp/S41wlan.log`). chrony and
  `provision-from-boot` write only to `/tmp` (tmpfs). As a safety net,
  `post-build.sh` makes **`/var/log` a symlink to `/tmp`** (a tmpfs), so any
  future/third-party writer that ignores this policy lands in RAM and vanishes
  on reboot rather than filling the SD card. Do not enable a syslog daemon or
  add persistent log paths without revisiting this constraint.
- **Declared download hashes are enforced.** The global
  `BR2_DOWNLOAD_FORCE_CHECK_HASHES` option remains unset; this does not bypass
  hash entries that exist. Packages from pinned Buildroot 2026.05.2 use its
  bundled hash files. The external `go-librespot` package ships its own hash for
  the final `-go2` archive generated after `go mod vendor`, and Buildroot checks
  that post-processed archive before extraction. Its transient original GitHub
  archive is not checked as a second, independent artifact. The external
  `radio-*` packages use local source trees and therefore have no downloads to
  hash. A Buildroot or go-librespot bump must regenerate and review the vendored
  archive, then update its filename and digest together rather than removing
  verification. Enabling forced checking globally is a separate image-wide
  policy change that requires a clean-build audit of every selected package and
  custom source.

## Background and design decisions

Why the setup looks the way it does:

- **Native, not Docker/aarch64.** An earlier flow built a 64-bit image inside a
  `linux/amd64` Docker container. It never produced a reliably bootable SD image
  (emulated amd64, an untested aarch64 target, and stale hand-rolled packages all
  stacked up). A plain native build on an x86 Debian box from Buildroot's proven
  `raspberrypi3_defconfig` boots, so we keep the 32-bit base and layer the radio
  on top via `BR2_EXTERNAL`. The Docker/aarch64 path has been removed.
- **Most custom packages are now redundant.** On a modern Buildroot, upstream
  already ships almost everything: `shairport-sync` (AirPlay 2 auto-`select`s the
  now-upstream `nqptp`), `mpd`, `mpd-mpc` (the `mpc` CLI — note plain `mpc` is now
  the GNU MPC math library), `avahi`, `dropbear`, `alsa-*`, `wpa_supplicant`,
  `dbus-python`, and the Python deps including `python-rpi-gpio`,
  `python-colorzero` and `python-smbus2`. The external tree therefore holds only
  **`go-librespot`** (pinned) and **`radio-app`**; the old `nqptp`,
  `python-smbus2` and `python-gpiozero-radio` custom packages were dropped.
- **Bleeding-edge Bootlin toolchain (required by MPD).** MPD 0.24 depends on
  kernel headers ≥ 5.6 (for `openat2.h`). The STABLE Bootlin ARMv7 glibc
  toolchain ships only 5.4 headers, which silently hid MPD from the config. We
  switched to the same-vendor **`...GLIBC_BLEEDING_EDGE`** toolchain (gcc 14→15,
  headers 5.4→5.15). This is an in-family compiler bump only — the CPU, RPi
  firmware and kernel pin are untouched; the running kernel already has
  `openat2`, the gate was purely about compile-time headers.
- **go-librespot from source.** Pinned to upstream **v0.9.0**, which uses
  `github.com/coder/websocket`, so Buildroot's Go infrastructure vendors its
  dependencies normally at download time (no pre-vendored-tarball hack). No
  `.hash` file is shipped for it (see the download-hash design constraint
  above): the vendored-archive digest is host-go/Buildroot-version dependent and
  was brittle to pin. If a future bump ever needs a newer Go than Buildroot's
  `host-go`, pin back a release rather than reviving the vendoring hack.

## See also

- [`../buildroot/README.md`](../buildroot/README.md) — the external-tree scripts
  and layout in more detail.
- [`firmware-updates.md`](firmware-updates.md) — operator update, activation,
  rollback, and unreachable-interface recovery procedures.
- [`hardware.md`](hardware.md) — base-radio GPIO, display and ADS1115 wiring.
- [`sound-devices.md`](sound-devices.md) — selectable audio overlays and one
  complete pinout per output device.
