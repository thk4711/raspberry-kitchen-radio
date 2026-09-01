# Building the radio image from scratch (beginner's guide)

This guide takes you from a fresh download of this project to a flashed SD card
that boots straight into the radio. **No prior embedded-Linux experience is
assumed.** You do not need to understand Buildroot — two scripts do the work.

## What you're building

A small, self-contained Linux "appliance" image for the **Raspberry Pi 3A+**
that boots directly into the radio app. It is produced by
[Buildroot](https://buildroot.org/), a tool that downloads and compiles a whole
minimal operating system for you. The output is a single file, `sdcard.img`,
that you write to an SD card.

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
cd raspberry-kitchen-radio
```

Every command below is run from this folder.

## Step 2 — Configure your radio (WiFi, name, sound card)

Run the configuration script, replacing the example values with your own. The
`--dac` option picks which I2S sound-card / amplifier board you fitted; if you
are unsure, leave the default (`iqaudio-dacplus`).

```bash
./buildroot/configure.sh --hostname kuechenradio \
    --ssid "YourWiFiName" --psk "YourWiFiPassword" \
    --root-password radio --dac iqaudio-dacplus
```

- Valid `--dac` values: `iqaudio-dacplus`, `hifiberry-dacplus`, `merus-amp`.
- The WiFi password (`--psk`) must be 8–63 characters.
- Run `./buildroot/configure.sh` with **no options** to be prompted for each
  value interactively instead.
- Check what you have set at any time: `./buildroot/configure.sh --show`.
- Your WiFi password is written to a local, git-ignored file. It is never
  uploaded or committed to git.

## Step 3 — Build the image

```bash
./buildroot/build.sh
```

What happens: the script installs the required build tools with `apt` (it will
ask for your password via `sudo`), downloads a pinned copy of Buildroot
(version `2026.05.2`) into `~/embedded/buildroot`, applies this project's
configuration, and compiles everything.

> ⏱️ **The first build takes a long time** — typically one to a few hours,
> depending on your machine and connection — because it compiles a
> cross-compiler, the Linux kernel, and all the audio backends from source.
> This is normal; let it run. Downloaded sources are cached in `~/embedded/dl`,
> so later rebuilds are much faster.

When it finishes you will see a **"Build complete."** banner printing the image
path, its size, and a SHA-256 checksum, for example:

```
 Image : /home/you/embedded/buildroot/output/images/sdcard.img
```

Useful variations:

- `./buildroot/build.sh --configure` — run Step 2 first, then build.
- `./buildroot/build.sh --clean` — recompile from clean.
- `./buildroot/build.sh --dirclean` — wipe everything and do a full rebuild.
- `./buildroot/build.sh --no-apt` — skip the `apt` step (if the build tools are
  already installed, or you can't use `sudo`).


## Step 4 — Write the image to the SD card

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

## Step 5 — Boot the Pi

Put the card in the Pi 3A+ and power it on. It joins your WiFi and starts the
radio automatically. If SSH is enabled you can reach it at the hostname you set
(e.g. `kuechenradio.local`) with user `root` and the root password from Step 2.

## If something goes wrong (common beginner issues)

- **"appliance not configured" / "WiFi credentials missing"** — you skipped
  Step 2, or left placeholder values. Re-run `configure.sh`.
- **`apt` or `sudo` errors** — you are not on Debian/Ubuntu, or your user can't
  use `sudo`. Use a supported host, or install the build tools manually and
  re-run with `./buildroot/build.sh --no-apt`.
- **Build fails partway through** — simply re-run `./buildroot/build.sh`; it
  resumes using the download cache. For a completely fresh attempt use
  `--dirclean`.
- **Wrong sound or no audio** — the `--dac` value (Step 2) must match the sound
  board you actually fitted.
- **Change WiFi or hostname without rebuilding** — you can edit
  `radio-config.txt` on the SD card's boot partition after flashing. See
  "Configuring a prebuilt image from the SD card" in the main
  [`README.md`](../README.md).

## Where to go deeper

- [`buildroot/README.md`](../buildroot/README.md) — the two scripts in detail.
- [`doc/buildroot.md`](buildroot.md) — full reference (manual build, on-target
  validation, service layout, debugging).
- [`doc/hardware.md`](hardware.md) — wiring, GPIO, and DAC overlays.
