# Building the PiSonic image from scratch (beginner's guide)

This guide takes you from a fresh download of this project to a flashed SD card
that boots straight into PiSonic. **No prior embedded-Linux experience is
assumed.** You do not need to understand Buildroot — one script does the build.

## What you're building

A small, self-contained Linux "appliance" image for the **Raspberry Pi 3A+**
that boots directly into the PiSonic app. It is produced by
[Buildroot](https://buildroot.org/), a tool that downloads and compiles a whole
minimal operating system for you. It produces an installation image,
`sdcard.img`, to write to an SD card and a matching versioned
`pisonic-<version>.swu` for later firmware updates.

The installation image has four partitions: a stable FAT loader, equal firmware
slots A and B, and a shared data partition. Both slots initially contain the
same firmware. Updates replace only the inactive slot; configuration and device
identity remain on the shared data partition. Older two-partition cards are not
converted in place and must be freshly flashed with `sdcard.img`.

## What you need before you start

1. **A Raspberry Pi 3A+.** This image targets that board specifically (32-bit
   ARMv7). Other Pi models are not supported by this image.
2. **A separate build computer running 64-bit Debian or Ubuntu Linux**
   (x64/amd64). *This is required.* You **cannot** build the image on macOS,
   Windows, or on the Pi itself. If you don't have such a machine, your options
   are: a spare PC/laptop with Ubuntu installed, a virtual machine (VirtualBox,
   VMware, UTM, …) running Ubuntu, or a cloud Linux server.
   - Allow ~30 GB of free disk space and use a good internet connection — the
     first build downloads a lot.
3. **An SD card** (8 GB or larger) and a way to plug it into your workstation.
4. **Your WiFi network name (SSID) and password.** The Pi 3A+ has no Ethernet
   port, so WiFi is the only way it gets online.

## Step 1 — Get the project onto the build computer

On the Debian/Ubuntu build computer, download and unpack the project — either
`git clone` the GitHub repository, or download the ZIP from GitHub and unzip it.
Then open a terminal **in the project folder** (the one containing `README.md`
and the `buildroot/` folder):

```bash
cd pisonic
```

Every command below is run from this folder.

## Step 2 — Build the generic image

```bash
./buildroot/build.sh
```

What happens: the script installs the required build tools with `apt` (it will
ask for your password via `sudo`), downloads a pinned copy of Buildroot
(version `2026.05.2`) into `~/embedded/buildroot`, applies this project's
configuration, and compiles everything.

If that Buildroot directory already exists, the script verifies that it is a
clean Git checkout at the project's pinned commit. It stops rather than silently
building a different or locally modified revision. Advanced developers who
intentionally patch Buildroot may pass `--allow-unverified-buildroot`; do not use
that override for release artifacts.

> ⏱️ **The first build takes a long time** — typically one to a few hours,
> depending on your machine and connection — because it compiles a
> cross-compiler, the Linux kernel, and all the audio backends from source.
> This is normal; let it run. Downloaded sources are cached in `~/embedded/dl`,
> so later rebuilds are much faster.

When it finishes you will see a **"Build complete."** banner printing both
artifact paths, sizes, and SHA-256 checksums, for example:

```
 Install : /home/you/embedded/buildroot/output/images/sdcard.img
 Size    : 1.8G
 SHA256  : <installation-image digest>
 Update  : /home/you/embedded/buildroot/output/images/pisonic-<version>.swu
 Size    : <compressed update size>
 SHA256  : <firmware-package digest>
```

Keep the versioned `.swu` and its checksum for updating an already-flashed
radio. The build validates the update archive for both A-to-B and B-to-A
selection before publishing it. Firmware packages are unsigned, so the checksum
detects corruption but does not prove authorship — see the security notes in
[`firmware-updates.md`](firmware-updates.md).

The installation image and update package are a matched pair from the same build.
Keep them together and retain both displayed SHA-256 values.

Useful variations:

- `./buildroot/build.sh --clean` — recompile from clean.
- `./buildroot/build.sh --dirclean` — wipe everything and do a full rebuild.
- `./buildroot/build.sh --no-apt` — skip the `apt` step (if the build tools are
  already installed, or you can't use `sudo`). This includes the native
  `plistutil` program from Debian/Ubuntu's `libplist-utils` package, required to
  configure the pinned shairport-sync development build for AirPlay 2.

## Optional: collect named artifacts locally or build remotely

The repository includes [`scripts/build_image.py`](../scripts/build_image.py).
By default it runs the supported build script **on the current Linux host** and
copies both results to `artifacts/`, using timestamped names and SHA-256 checks:

```bash
python3 scripts/build_image.py
```

No SSH tools are needed in local mode. For settings you use repeatedly, copy the
dummy example and edit the untracked local file:

```bash
cp scripts/build-image.example.ini scripts/build-image.ini
python3 scripts/build_image.py
```

Command-line options override the configuration file. To build on a separate
x64 Debian/Ubuntu machine, set `execution = remote`, `host`, `remote_root`, and
the remote Buildroot/cache paths in that file. Remote mode uses passwordless
batch-mode SSH plus `rsync` and `scp`:

```bash
python3 scripts/build_image.py --execution remote \
    --host build-user@build-host.example --remote-root /home/build-user/embedded
```

The raw image is ZIP-compressed by default; use `--no-zip` to retain a raw
`.img`. Use the image for installation/recovery and the matching `.swu` through
the Maintenance web interface. Parallel `make` jobs auto-detect the build host's
core count by default, so a remote build scales to the remote machine; set
`--jobs N` (or `jobs = N` in the INI) only to cap parallelism. Run
`python3 scripts/build_image.py --help` for all settings or add `--dry-run` to
inspect resolved paths without building.


## Step 3 — Write the image to the SD card

Insert the SD card into your workstation. **Double-check the device name — `dd`
writing to the wrong disk destroys its data.**

- **On Linux:** find the card with `lsblk` (e.g. `/dev/sdb`), then:
  ```bash
  sudo dd if=~/embedded/buildroot/output/images/sdcard.img \
      of=/dev/sdX bs=4M oflag=direct conv=fsync
  ```
- **On macOS** (if you copied `sdcard.img` to your Mac): find it with
  `diskutil list` (e.g. `/dev/disk4`), unmount it
  (`diskutil unmountDisk /dev/diskN`), then:
  ```bash
  sudo dd if=sdcard.img of=/dev/rdiskN bs=4m
  ```
- Prefer a graphical tool? **Raspberry Pi Imager** or **balenaEtcher** can flash
  the `sdcard.img` file too (choose "use custom image").

## Step 4 — Configure the SD card

After flashing, reinsert or remount the SD card and open its small FAT boot
partition. Edit the existing `pisonic-config.txt` with a plain-text editor:

- uncomment the WiFi lines and replace their placeholders with your details;
- optionally choose a hostname and set a unique root password if console or SSH
  login is needed;
- optionally set the WiFi country, timezone, static IP, and SSH enablement.

The WiFi password must be 8–63 characters. Save the file and safely eject the
card. Known placeholder values are rejected. Device-specific credentials are
added only now; the built image remains generic and reusable, with root login
locked until a password is explicitly provisioned.

## Step 5 — Boot the Pi

Put the card in the Pi 3A+ and power it on. It joins your WiFi and starts the
radio automatically. If SSH is enabled you can reach it at the hostname you set
(e.g. `pisonic.local`) with user `root` and the root password from Step 4.
Open `http://<hostname>.local:8080` to finish setup. The generic image starts on
the built-in headphone output; select an external sound card on the web Audio
page and reboot if needed.

On early boot, the guarded data-resize service expands only the final data
partition (`p4`) to use the SD card's remaining capacity. The loader and two
768 MiB firmware slots stay fixed-size so A/B updates always have equal targets.

## If something goes wrong (common beginner issues)

- **Radio does not join WiFi** — check that Step 4's active `wifi_ssid` and
  `wifi_psk` values are correct. Inspect `/tmp/provision-from-boot.log` from a
  console if available.
- **`apt` or `sudo` errors** — you are not on Debian/Ubuntu, or your user can't
  use `sudo`. Use a supported host, or install the build tools manually and
  re-run with `./buildroot/build.sh --no-apt`.
- **Build fails partway through** — simply re-run `./buildroot/build.sh`; it
  resumes using the download cache. For a completely fresh attempt use
  `--dirclean`.
- **Wrong sound or no audio** — choose the fitted sound-card profile from the
  web interface's Audio page and reboot.
- **Change WiFi or hostname without rebuilding** — use the web interface, or
  edit and reactivate the relevant `pisonic-config.txt` lines as a recovery route.

## Where to go deeper

- [`buildroot/README.md`](../buildroot/README.md) — build-script quick start.
- [`doc/buildroot.md`](buildroot.md) — full reference (manual build, on-target
  validation, service layout, debugging).
- [`doc/firmware-updates.md`](firmware-updates.md) — upload the generated `.swu`,
  follow trial activation, roll back, and recover an unreachable radio.
- [`doc/hardware.md`](hardware.md) — PiSonic base wiring, GPIO, display and controls.
- [`doc/sound-devices.md`](sound-devices.md) — choose and wire a sound device
  using its dedicated pinout.
