# Buildroot appliance image (Raspberry Pi 3A+)

This document is the reference for the **minimal, fast-booting Buildroot
appliance image** for the Raspberry Kitchen Radio — the supported way to run the
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

Two helper scripts in [`../buildroot/`](../buildroot/) do everything. Run them on
the amd64 Debian host from the root of a fresh clone of this repository:

```bash
# 1) Configure the appliance (hostname, WiFi, root password, DAC overlay).
#    Prompts interactively (current value = default), or pass flags to script it.
#    WiFi credentials are written to a git-ignored file, so they are never
#    committed.
./buildroot/configure.sh --hostname kuechenradio \
    --ssid MyNetwork --psk 'my-wifi-password' \
    --root-password radio --dac iqaudio-dacplus
./buildroot/configure.sh --show      # print current values (PSK hidden)

# 2) Build (or rebuild) the image. Installs the host build packages via apt,
#    fetches a pinned stock Buildroot, applies radio_rpi3_defconfig, and compiles.
./buildroot/build.sh
./buildroot/build.sh --configure     # run configure.sh first, then build
./buildroot/build.sh --clean         # make clean, then rebuild
./buildroot/build.sh --dirclean      # wipe output/, then full rebuild
./buildroot/build.sh --no-apt        # skip the apt host-package step
```

`build.sh` prints the finished image path (`output/images/sdcard.img`), its size
and SHA-256, plus the exact `dd` command for Linux and macOS.

Override the build locations with environment variables if the defaults do not
suit your host:

```bash
BUILDROOT_DIR=~/br/buildroot BR2_DL_DIR=~/br/dl BUILDROOT_VERSION=2026.05.2 \
    ./buildroot/build.sh
```

`configure.sh` writes device settings to git-ignored files only — no tracked
source is edited, so two devices configure from one clean checkout and no secret
lands in a tracked diff:

- `buildroot/local-device.conf` — `RADIO_HOSTNAME`, `RADIO_ROOT_PASSWORD`,
  `RADIO_DAC` (git-ignored). `build.sh` merges hostname + root password onto the
  tracked defconfig via a generated kconfig fragment and applies the DAC overlay
  to the boot `config.txt` build artifact.
- `buildroot/external/board/radio/rootfs-overlay/etc/wpa_supplicant.conf` —
  regenerated from the template as a constraint-safe `network={...}` block
  (git-ignored).

## Build it (manual)

If you prefer to drive Buildroot directly, clone stock Buildroot on the build
host and point it at this repository's external tree. Use environment variables
for the locations so nothing is hard-coded:

```bash
# On the amd64 Debian host. Adjust these to your layout:
export BUILDROOT_DIR=$HOME/embedded/buildroot          # stock Buildroot checkout
export BR2_DL_DIR=$HOME/embedded/dl                    # shared download cache
export REPO_DIR=$HOME/embedded/radio-repo              # this repo on the host

# Set WiFi credentials before building (copy the template, then edit):
#   $REPO_DIR/buildroot/external/board/radio/rootfs-overlay/etc/wpa_supplicant.conf
#   (start from wpa_supplicant.conf.example)

cd "$BUILDROOT_DIR"
make BR2_EXTERNAL="$REPO_DIR/buildroot/external" radio_rpi3_defconfig
make -j"$(nproc)"
```

The resulting image is `output/images/sdcard.img`. To change kernel or Buildroot
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

Log in on the HDMI/USB-keyboard console (or over SSH once WiFi is up):

```text
user:     root
password: radio          # or whatever you passed to configure.sh
```

### Validate on the target

```sh
# Radio processes
ps | grep -E 'mpd|radio.py|shairport-sync|nqptp|go-librespot|bluetoothd|bluealsa'
#   expect: mpd, python3 /opt/raspberry-kitchen-radio/radio.py,
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

To debug the app manually, stop the service and run it in the foreground with
verbose logging (Python app + all media backends) turned on:

```sh
/etc/init.d/S90radio stop
cd /opt/raspberry-kitchen-radio
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
cd /opt/raspberry-kitchen-radio
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
card. **Persistent** logging (surviving reboots) requires writing to the ext4
rootfs — point `RADIO_PROCESS_LOG_DIR` at a real path such as
`/opt/raspberry-kitchen-radio/logs`, keep `RADIO_PROCESS_LOG_MAX_BYTES` sane,
and remember the rootfs is normally the appliance's only writable persistent
store. Revert these changes once debugging is done to restore the silent
default.

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
- **Media backends compiled from source, each with a source hash:**
  - `go-librespot` — pinned version + `sha256` in
    `buildroot/external/package/go-librespot/go-librespot.hash`.
  - `shairport-sync` / `nqptp` — pinned by the upstream Buildroot packages
    selected in the defconfig (moving them means bumping the pinned Buildroot
    revision above).
- **App version** — `lib/_version.py` (`__version__`) is the single source of
  truth, surfaced on the boot splash subtitle and via `radio.py --version`, so a
  running unit reports exactly which build it is.

When cutting a release (see `CHANGELOG.md`): bump `lib/_version.py` +
`pyproject.toml`, record the pinned Buildroot revision and the backend source
hashes above in the changelog entry, then tag `vX.Y.Z`. That tuple
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
      ├─ config.txt        # i2c/i2s/spi on, audio off, DAC overlay,
      │                    #   Bluetooth on PL011 (NOT pi3-miniuart-bt) + enable_uart,
      │                    #   boot_delay=0 / initial_turbo
      ├─ cmdline.txt       # kernel command line (quiet loglevel=3, tty1 only)
      ├─ linux-i2c.fragment       # positive fragment (APPLIED): expose the ADS1115 I2C bus
      ├─ linux-watchdog.fragment  # positive fragment (APPLIED): BCM2835 watchdog + zram
      ├─ linux-bluetooth.fragment # positive fragment (APPLIED): CONFIG_BT + BT UART HCI
      ├─ post-build.sh     # mpd user, tmpfs fstab, hostname, service layout
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

BR2_ROOTFS_OVERLAY="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/rootfs-overlay"
BR2_ROOTFS_POST_BUILD_SCRIPT="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/post-build.sh"
BR2_ROOTFS_POST_IMAGE_SCRIPT="board/raspberrypi3/post-image.sh"
```

The firmware `config.txt` enables the hardware interfaces and the DAC overlay
and applies the boot-speed options; `cmdline.txt` keeps the HDMI/keyboard console
while suppressing kernel chatter:

```text
# config.txt (key lines)
kernel=zImage
gpu_mem_512=100
# Bluetooth on the PL011 UART: NOT pi3-miniuart-bt (that breaks A2DP)
enable_uart=1
disable_splash=1
boot_delay=0
initial_turbo=30
dtparam=i2c_arm=on
dtparam=i2s=on
dtparam=spi=on
dtparam=audio=off
dtoverlay=iqaudio-dacplus     # set by configure.sh --dac

# cmdline.txt
root=/dev/mmcblk0p2 rootwait console=tty1 quiet loglevel=3 logo.nologo
```

### Boot order (BusyBox init) and service layout

```text
BusyBox init -> /etc/inittab
  sysinit:  mount /proc; remount,rw /; mkdir /dev/{pts,shm}; mount -a (tmpfs /tmp)
  sysinit:  /usr/sbin/radio-boot-splash     # paint the branded SPI splash ASAP,
                                             #   launched DETACHED so it renders
                                             #   in parallel and never blocks boot
  sysinit:  /usr/sbin/provision-from-boot   # apply SD-card radio-config.txt FIRST,
                                             #   before hostname/WiFi/chrony/SSH
  sysinit:  hostname -F /etc/hostname        # reads provisioned hostname
  sysinit:  /etc/init.d/rcS
  S10mdev / mounts, tmpfs on /tmp
  S11modules
  S13zram              # compressed RAM swap (OOM protection on the 512 MB board)
  S14watchdog          # arm the BCM2835 hardware watchdog (/dev/watchdog)
  S30dbus-daemon       # system bus (required for shairport-sync AirPlay control + BlueZ)
  S40network           # brings up lo (WiFi is NOT here — non-blocking)
  S40bluetoothd        # bluetoothd (bluez5_utils); --experimental via /etc/default/bluetoothd
  S41wlan              # wpa_supplicant + udhcpc for wlan0, IN THE BACKGROUND
                       #   (log discarded to /dev/null by default;
                       #    set S41WLAN_LOG=/tmp/S41wlan.log to debug)
  S42bluetooth         # no-PIN auto-pair agent + connection-gated pairing mode +
                       #   bluealsa (A2DP receiver) + bluealsa-aplay (-> ALSA default)
  S49chronyd           # sets wall clock for HTTPS/TLS clients on the RTC-less Pi
  S50avahi-daemon      # mDNS for AirPlay / Spotify discovery
  S50dropbear          # SSH server (root login enabled; skipped if enable_ssh=0)
  S50mpd               # Music Player Daemon (/etc/mpd.conf)
  S90radio             # supervises radio.py (crash restart w/ backoff +
                       #   freeze-watcher), which launches nqptp,
                       #   shairport-sync and go-librespot itself
                       #   (Bluetooth is NOT launched here — radio.py only reads
                       #    org.bluez metadata over D-Bus)
```

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
  splash (`lib/display_1_inch_69/boot_splash.py`) renders in parallel with
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
   3 s loop), which then triggers case 1.
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
# Simulate a crash: the supervisor restarts radio.py within the backoff window.
killall python3
# Simulate a freeze: pause the app; the freeze-watcher kills+restarts it
# after RADIO_HEARTBEAT_TIMEOUT.
kill -STOP "$(pgrep -f radio.py)"
```


### Provisioning a prebuilt image from the SD card (`radio-config.txt`)

`buildroot/configure.sh` bakes hostname / WiFi / root password / DAC into the
image at build time. For someone who only wants to flash a **prebuilt image**
and adjust it without rebuilding, the FAT ("boot") partition of the SD card can
carry a plain-text `radio-config.txt` — the same idea as Raspberry Pi OS's
`/boot` provisioning.

The image ships `radio-config.txt.example` on the FAT partition (added by
`board/radio/post-image.sh`). Copy it to `radio-config.txt`, edit it, and boot:

```ini
wifi_ssid=MyNetwork
wifi_psk=my-wifi-password
# hostname=kuechenradio      # also the AirPlay / Spotify device name
# root_password=radio
# enable_ssh=1               # 0 disables the SSH server
timezone=Europe/Berlin       # zoneinfo name; without it the display clock shows UTC
# ntp_server=pool.ntp.org
```

- **Applied first, on every boot.** `/usr/sbin/provision-from-boot` runs from an
  `inittab` **sysinit** line *before* `hostname -F` and *before* `rcS`
  (WiFi/chrony/dropbear), so every consumer reads the provisioned values. It is
  the earliest step after the rootfs is remounted read-write and `/tmp` exists.
- **Overrides the build-time defaults**; values persist on the rootfs.
- **Read-only, whitelist parser.** The FAT partition is mounted read-only and
  the file is never modified. Unknown keys are ignored and the file is never
  sourced/eval'd. A missing or invalid file never blocks boot — the built-in
  defaults are used and the outcome is logged to `/tmp/provision-from-boot.log`.
- **WiFi** is rewritten as the strict `network={ … key_mgmt=WPA-PSK }` block the
  stripped `wpa_supplicant` requires (PSK 8..63 chars). `wifi_country` (an
  ISO-3166 alpha-2 code, e.g. `DE`) is **validated and persisted to
  `/run/wifi-country`**, then applied at bring-up by the `S41wlan` init script
  via `iw reg set`. The country is set with `iw` (not `wpa_supplicant`) because
  this `wpa_supplicant` is built without `CONFIG_CTRL_IFACE` and rejects a
  `country=` line inside `wpa_supplicant.conf`. An unset or invalid value keeps
  the safe worldwide default.
- **`enable_ssh=0`** writes `/run/provision-disable-ssh`; the overlay
  `S50dropbear` checks it and skips starting SSH (race-free, since sysinit runs
  before `S50`).
- **`timezone`** (a zoneinfo name such as `Europe/Berlin`) symlinks
  `/etc/localtime` to the matching entry and writes `/etc/timezone`. It requires
  the tz database (`BR2_TARGET_TZ_INFO`, enabled in the defconfig); otherwise the
  appliance — and the display's top status-bar clock — stays on UTC. Because it
  runs before `S90radio`, the radio app starts already in the local zone.
- The **DAC overlay** is not a key here — edit the `dtoverlay=` line in
  `config.txt` on the same FAT partition directly.

#### Image assembly and the FAT boot partition size

`board/radio/post-image.sh` is a self-contained replacement for Buildroot's
stock `board/raspberrypi/post-image.sh`. It builds `sdcard.img` with `genimage`
from `board/radio/genimage.cfg.in`, and:

- Enlarges the FAT boot partition to **64M** (Buildroot's stock template uses
  32M) so the firmware, kernel, DTBs, `config.txt`/`cmdline.txt` and the shipped
  `radio-config.txt.example` fit with headroom. Override with the
  `RADIO_BOOT_VFAT_SIZE` environment variable at build time, e.g.
  `RADIO_BOOT_VFAT_SIZE=128M ./buildroot/build.sh`.
- Builds the boot-file list exactly like the stock script (every `*.dtb`, every
  file under `rpi-firmware/`, plus the kernel named by the `kernel=` line in
  `config.txt`) and adds `radio-config.txt.example`.

`post-build.sh` enforces the service layout. The enabled radio scripts are
`S13zram`, `S14watchdog`, `S50mpd` and `S90radio` (all shipped in the board
overlay and `chmod 0755`'d by `post-build.sh`); the competing upstream media
scripts are moved to `/etc/init.d/disabled/`:

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

`radio.py` runs from `/opt/raspberry-kitchen-radio` (installed by the `radio-app`
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
- **Keep the HDMI/USB-keyboard console** (`console=tty1` + a `tty1` getty) as the
  recovery path. Re-enable a serial getty only for low-level boot debugging.
- **Keep the validated boot-speed options** (`quiet loglevel=3 logo.nologo`,
  `disable_splash=1`, `boot_delay=0`, `initial_turbo=30`, async `S41wlan`) unless
  a change is shown to regress on hardware.
- **Only the small positive `linux-i2c.fragment` is applied** (it exposes the
  ADS1115 I2C bus). No broad kernel trim is used: the image deliberately keeps
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
- **Download hash checking is not forced.** `radio_rpi3_defconfig` leaves
  `BR2_DOWNLOAD_FORCE_CHECK_HASHES` unset and ships **no `.hash` file** for
  `go-librespot`. Its digest is the *vendored* archive produced by `go mod
  vendor`, whose contents and `-go<N>` filename suffix depend on the exact
  host-go that Buildroot ships, so a pinned hash was brittle and broke fresh
  builds. Only downloads without a hash entry (in practice just the go-librespot
  vendored archive) go unverified; every upstream Buildroot package still
  enforces its own bundled hash. Do not re-enable `FORCE_CHECK_HASHES` without
  re-pinning a go-librespot hash against the exact pinned Buildroot version.

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
- [`hardware.md`](hardware.md) — DAC/overlays, GPIO, ADS1115 wiring.
