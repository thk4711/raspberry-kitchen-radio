# Raspberry Pi Radio

Raspberry Pi Radio turns a Raspberry Pi 3A+ into a **kitchen-style internet
radio and streaming speaker** with real, tactile controls. Power it on and it
boots straight into the radio — no desktop, no login, no app to launch. Turn the
knob to change the volume, press a button to switch stations, and stream to it
from your phone over AirPlay, Spotify Connect, Bluetooth, or USB Audio.

Under the hood it is a small Python application running on a **minimal,
fast-booting [Buildroot](https://buildroot.org/) appliance image**. The image is
purpose-built for the **Raspberry Pi 3A+** and starts the radio automatically.

## What you get

### A standalone radio appliance

- Boots directly into the radio application.
- Uses physical controls: a volume knob, preset buttons, and a power switch.
- Shows now-playing information and station logos on an SPI display.
- Runs from a minimal Buildroot image instead of a general-purpose desktop OS.

### Multiple audio sources

- **Internet radio presets** for everyday listening.
- **AirPlay receiver** for Apple devices.
- **Spotify Connect** for playback from the Spotify app.
- **Bluetooth A2DP** for phones, tablets, and computers.
- **USB Audio Class receiver** so the radio can appear as a stereo USB sound
  card over the Pi 3A+ USB data port. See the USB wiring warning in
  [`doc/hardware.md`](doc/hardware.md#usb-audio-gadget-wiring): USB VBUS must
  not be connected.

### Web administration

- Local password-protected web interface on port 8080.
- Edit radio presets and choose which music sources are enabled.
- Configure WiFi, static IP, hostname, device name, time settings, display
  options, audio output, and maximum volume.
- View status for the player, network, Bluetooth adapter, and system.
- Back up and restore persistent device data.
- Install firmware updates and switch back to retained compatible firmware.

### Sound tuning

- Built-in **ten-band parametric equalizer** in the web interface.
- Per-band frequency, gain, Q, and filter type controls.
- One EQ profile applies to Internet Radio, AirPlay, Spotify Connect,
  Bluetooth, and USB Audio.
- Ordinary EQ adjustments are applied live without restarting the current
  stream; enabling or bypassing the whole EQ briefly restarts the audio path.
- EQ settings survive normal firmware updates, trial boots, and automatic
  rollback.

### Appliance reliability

- Fast-booting appliance image focused on the radio use case.
- Persistent configuration and user data on a shared data partition.
- Safe A/B firmware update flow with health-checked trial boots.
- Automatic rollback when a trial firmware is unhealthy.
- Service restart and watchdog support for unattended use.

## Typical use cases

- A kitchen, workshop, or bedside internet radio with physical controls.
- A compact AirPlay, Spotify Connect, Bluetooth, or USB Audio speaker.
- A local audio appliance that can be administered from a phone or laptop.
- A hackable embedded audio project with extensible music-source backends.

## Hardware at a glance

The appliance image targets the **Raspberry Pi 3A+** specifically. Other Pi
models are not supported by the image.

A typical build uses:

- Raspberry Pi 3A+.
- SPI-connected display for now-playing information and station logos. Two
  panels are supported and selectable via `display.conf`: the 1.69" **ST7789**
  (240×280, rectangular, default) and the 1.28" **GC9A01** (240×240, round).
  Both use the same four-wire SPI wiring.
- ADS1115 ADC on I2C for the volume potentiometer, preset-button ladder, and
  power switch.
- I2S DAC / amplifier board, for example IQaudIO, HiFiBerry, ESS I-SABRE/Katana,
  or MERUS, driving the speaker.
- Speaker, enclosure, wiring, and power supply.

Base-radio wiring and ADS1115 controls are documented in
[`doc/hardware.md`](doc/hardware.md), which also includes a full
[wiring schematic](doc/hardware.md#schematic). Every selectable output has a
complete, color-coded 40-pin map in
[`doc/sound-devices.md`](doc/sound-devices.md). No 3D-printable case or speaker
files ship with this repository.

## Getting started

The usual first setup flow is:

1. **Burn the image to an SD card.**

   Flash a completed `sdcard.img` to a microSD card using a tool such as:

   - [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
   - [balenaEtcher](https://etcher.balena.io/)
   - GNOME Disks
   - `dd` on Linux/macOS
   - Win32 Disk Imager on Windows

   If you still need to build the image, follow the beginner walkthrough in
   [`doc/build-from-scratch.md`](doc/build-from-scratch.md) or the Buildroot
   quick start in [`buildroot/README.md`](buildroot/README.md).

2. **Edit `radio-config.txt` on the boot partition.**

   After flashing, remove and reinsert the SD card on your computer. Open the
   small FAT boot partition and edit the existing `radio-config.txt` with a plain
   text editor. Uncomment and set your WiFi SSID and password. You can also set
   the hostname, a unique root password, timezone, country, display options, and
   source flags. The generic image has no reusable login: root password access
   is locked, and SSH cannot be enabled until a non-placeholder password is
   explicitly provisioned.

   Provisioning is one-shot: after applying active settings, the radio comments
   their lines out in `radio-config.txt`. To apply a boot setting again, edit its
   value on the boot partition, remove the leading `#`, and reboot. The full key
   reference is in
   [`doc/buildroot.md`](doc/buildroot.md#provisioning-a-prebuilt-image-from-the-sd-card-radio-configtxt).

3. **Boot the device.**

   Insert the SD card into the Raspberry Pi 3A+ and power it on. The appliance
   boots directly into the radio application.

4. **Open the web interface.**

   From a phone or computer on the same trusted local network, open one of:

   - `http://<hostname>.local:8080`
   - `http://<device-ip>:8080`

   On first use, the web interface asks you to create an administrator password.
   The interface uses plain HTTP and is intended for trusted LANs only.

5. **Select the sound card.**

   Open the **Audio** page in the web interface and select the DAC / amplifier
   board used by your build. Save the setting and reboot if prompted. The
   built-in headphone output is the safe default. Supported sound cards and
   wiring maps are documented in [`doc/sound-devices.md`](doc/sound-devices.md)
   ([color-coded pinout](https://thk4711.github.io/raspberry-kitchen-radio/sound-devices.html)).

After that, use the web interface for stations, music sources, display settings,
network settings, equalizer tuning, backups, and firmware maintenance. SSH is
disabled by default.

## Firmware updates

Firmware updates are installed from the web interface using versioned
`kitchen-radio-<version>.swu` packages. Updates are written to the inactive
firmware slot, keep persistent configuration and user data, and use a
health-checked trial boot. If a trial firmware is unhealthy, U-Boot automatically
returns to the previous accepted slot.

Firmware packages are **unsigned**, so SHA-256 detects corruption but does not
prove authenticity. Obtain packages through a trusted channel and use the web
interface only on a trusted LAN. The complete upload, activation, rollback, and
recovery procedure is in [`doc/firmware-updates.md`](doc/firmware-updates.md).

## Technical overview

`radio.py` runs a `RadioController` that ties together the playback backends, the
display, and the analog controls. Every backend implements the same small
[`MusicSource`](lib/music_source.py) interface, so the controller can treat
internet radio, AirPlay, Spotify Connect, Bluetooth, and USB Audio as
interchangeable sources.

The media backends — shairport-sync, nqptp, go-librespot, BlueZ/bluez-alsa, and
the ALSA `alsaloop`/libsamplerate USB bridge — are compiled from source into the
Buildroot appliance image. For the service layout, audio routing, and update
architecture, see the documentation below.

## Documentation

Start here depending on what you want to do:

| Goal | Read |
| --- | --- |
| Build your first radio from scratch | [`doc/build-from-scratch.md`](doc/build-from-scratch.md) |
| Build the Buildroot image | [`buildroot/README.md`](buildroot/README.md) |
| Configure a flashed image | [`doc/buildroot.md`](doc/buildroot.md#provisioning-a-prebuilt-image-from-the-sd-card-radio-configtxt) |
| Wire the base hardware | [`doc/hardware.md`](doc/hardware.md) |
| Choose a DAC / amplifier | [`doc/sound-devices.md`](doc/sound-devices.md) · [color-coded pinout](https://thk4711.github.io/raspberry-kitchen-radio/sound-devices.html) |
| Use the web interface | [`doc/web-interface.md`](doc/web-interface.md) |
| Tune the parametric EQ | [`doc/equalizer.md`](doc/equalizer.md) |
| Edit station presets | [`doc/stations.md`](doc/stations.md) |
| Use Bluetooth audio | [`doc/bluetooth.md`](doc/bluetooth.md) |
| Use USB Audio | [`doc/usb-audio.md`](doc/usb-audio.md) |
| Update firmware | [`doc/firmware-updates.md`](doc/firmware-updates.md) |
| Develop or contribute | [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`doc/development.md`](doc/development.md) |
| Browse all documentation | [`doc/README.md`](doc/README.md) |

## License

This project is released under the [MIT License](LICENSE). The Buildroot image
compiles its media backends (shairport-sync, nqptp, go-librespot, and the
Bluetooth stack BlueZ + bluez-alsa) from source under their own upstream
licenses. The repository itself vendors only a UI font (Apache-2.0) and station
logos (broadcaster trademarks) — see [`doc/assets.md`](doc/assets.md).