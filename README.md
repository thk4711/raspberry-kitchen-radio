<p align="center">
  <img src="radio_web/static/PiSonic-Logo.svg" alt="PiSonic Logo" width="400"/>
</p>

# PiSonic

PiSonic turns a Raspberry Pi 3A+ into a **standalone internet audio
appliance or streaming speaker**.It feels like a device and not like a project. It Boots straight into playback
no general-purpose desktop OS.

- Bluetooth audio
- Airplay 2
- Spotify Connect
- Internet radio
- USB sound card
- Parametric 10 band EQ
- WEB interface
- Reversible firmware updates with persisten configuration
- Support for many Raspberry Pi audio hats
- Tactile buttons and volume potentiometer support
- Rectangular or round SPI display support
- very small memory and storage footprint

### Five audio sources

Internet radio presets, an AirPlay receiver, Spotify Connect, Bluetooth A2DP,
and a USB Audio Class receiver that makes the appliance appear as a stereo USB sound
card. All five share one output path, so the volume knob and equalizer apply
uniformly.

> **USB wiring:** VBUS must not be connected — see
> [`doc/hardware.md`](doc/hardware.md#usb-audio-gadget-wiring).

### Web administration

A password-protected web interface on port 8080 lets you edit presets and
enabled sources; configure WiFi, static IP, hostname, device name, time,
display, audio output, and maximum volume; view player, network, Bluetooth, and
system status; back up and restore device data; and install or roll back
firmware.

### Sound tuning

A built-in **ten-band parametric equalizer** in the web interface, with per-band
frequency, gain, Q, and filter type. Ordinary adjustments apply live; enabling
or bypassing the whole EQ briefly restarts the audio path. Settings survive
firmware updates, trial boots, and automatic rollback.

### Appliance reliability

- Persistent configuration and user data on a shared data partition.
- Safe A/B firmware updates with health-checked trial boots and automatic
  rollback when a trial firmware is unhealthy.
- Service restart and watchdog support for unattended use.

## Typical use cases

- A kitchen, workshop, or bedside internet radio with physical controls.
  (PiSonic works anywhere a compact networked speaker fits.)
- A compact AirPlay, Spotify Connect, Bluetooth, or USB Audio speaker.
- A local audio appliance that can be administered from a phone or laptop.
- A hackable embedded audio project with extensible music-source backends.

## Hardware

The appliance image targets the **Raspberry Pi 3A+** This device is still affordable and more than adequat for this purpose.
Other Pi models are not supported. 

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

Base wiring and ADS1115 controls are documented in
[`doc/hardware.md`](doc/hardware.md), which also includes a full
[wiring schematic](doc/hardware.md#schematic). Every selectable output has a
complete, color-coded 40-pin map in
[`doc/sound-devices.md`](doc/sound-devices.md). No 3D-printable case or speaker
files ship with this repository.

## Getting started

The usual first setup flow is:

1. **Burn the image to an SD card.**

   Flash a completed `sdcard.img` to a microSD card using a tool such as [Raspberry Pi Imager](https://www.raspberrypi.com/software/) or [balenaEtcher](https://etcher.balena.io/)

   You can also build the image, follow the beginner walkthrough in [`doc/build-from-scratch.md`](doc/build-from-scratch.md) or the Buildroot
   quick start in [`buildroot/README.md`](buildroot/README.md).
   
2. **Edit `pisonic-config.txt` on the boot partition.**

   After flashing, remove and reinsert the SD card on your computer. Open the
   small FAT boot partition and edit the existing `pisonic-config.txt` with a plain
   text editor. Uncomment and set your WiFi SSID and password. You can also set
   the hostname, a unique root password, timezone, country, display options, and
   source flags. The generic image has no reusable login: root password access
   is locked, and SSH cannot be enabled until a non-placeholder password is
   explicitly provisioned.

   Provisioning is one-shot: after applying active settings, the appliance comments
   their lines out in `pisonic-config.txt`. To apply a boot setting again, edit its
   value on the boot partition, remove the leading `#`, and reboot. The full key
   reference is in
   [`doc/buildroot.md`](doc/buildroot.md#provisioning-a-prebuilt-image-from-the-sd-card-pisonic-configtxt).

3. **Boot the device.**

   Insert the SD card into the Raspberry Pi 3A+ and power it on. The appliance
   boots directly into the PiSonic application.

4. **Open the web interface.**

   From a phone or computer on the same trusted local network, open one of:

   - `http://<hostname>.local:8080`
   - `http://<device-ip>:8080`

   On first use, the web interface asks you to create an administrator password.
   The interface uses plain HTTP, so use it only on a trusted LAN (see
   [`doc/web-interface.md`](doc/web-interface.md#reaching-the-interface)).

5. **Select the sound card.**

   Open the **Audio** page in the web interface and select the DAC / amplifier
   board used by your build. Save the setting and reboot if prompted. The
   built-in headphone output is the safe default. Supported sound cards and
   wiring maps are documented in [`doc/sound-devices.md`](doc/sound-devices.md)
   ([color-coded pinout](https://thk4711.github.io/pisonic/sound-devices.html)).

After that, use the web interface for stations, music sources, display settings,
network settings, equalizer tuning, backups, and firmware maintenance. SSH is
disabled by default.

## Firmware updates

Firmware updates are installed from the web interface using versioned
`pisonic-<version>.swu` packages. Updates are written to the inactive
firmware slot, keep persistent configuration and user data, and use a
health-checked trial boot with automatic rollback to the previous accepted slot.

Packages are **unsigned**: obtain them through a trusted channel and update only
on a trusted LAN. The full upload, activation, rollback, and recovery procedure —
and the complete security notes — are in
[`doc/firmware-updates.md`](doc/firmware-updates.md).

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
| Build your first PiSonic from scratch | [`doc/build-from-scratch.md`](doc/build-from-scratch.md) |
| Build the Buildroot image | [`buildroot/README.md`](buildroot/README.md) |
| Configure a flashed image | [`doc/buildroot.md`](doc/buildroot.md#provisioning-a-prebuilt-image-from-the-sd-card-pisonic-configtxt) |
| Wire the base hardware | [`doc/hardware.md`](doc/hardware.md) |
| Choose a DAC / amplifier | [`doc/sound-devices.md`](doc/sound-devices.md) · [color-coded pinout](https://thk4711.github.io/pisonic/sound-devices.html) |
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