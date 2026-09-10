# Documentation

This directory collects the project documentation.

| Document | Purpose |
| --- | --- |
| [../CHANGELOG.md](../CHANGELOG.md) | Release notes / version history (Keep a Changelog); includes the release checklist. |
| [development.md](development.md) | **Developer / contributor guide.** Local setup (virtualenv), running the pytest suite & coverage, linting (`ruff`), type checks (`mypy`), and the CI pipeline. |
| [build-from-scratch.md](build-from-scratch.md) | Beginner's step-by-step guide from a fresh download to a flashed A/B SD card and its matching update package (no embedded-Linux experience assumed). |
| [buildroot.md](buildroot.md) | **Buildroot appliance image reference.** Build, flash, A/B partition and U-Boot policy, persistent paths, `.swu` generation, recovery, service layout, source pins, and design decisions. |
| [persistent-data.md](persistent-data.md) | **Persistent-data reference.** Complete `/data` inventory, why each item survives, image seeding, p4 mounting, compatibility links, safe runtime writes, update isolation, and deliberately volatile state. |
| [firmware-updates.md](firmware-updates.md) | **Firmware operator guide.** Preflight, upload, installation, trial acceptance, automatic/manual rollback, and recovery. |
| [firmware-update-architecture.md](firmware-update-architecture.md) | **Firmware developer reference.** Buildroot, image layout, redundant U-Boot state machine, SWU construction, Linux tooling, fallback, and porting checklist. |
| [hardware.md](hardware.md) | Base-radio wiring, display, USB gadget, and ADS1115 controls. |
| [analog-audio.md](analog-audio.md) | Switching a development appliance from an I2S DAC to the Raspberry Pi's built-in headphone output. |
| [sound-devices.md](sound-devices.md) | Every selectable sound device, with a complete color-coded 40-pin map, compatibility actions, routing, and qualification. |
| [alsa-audio-path.md](alsa-audio-path.md) | **ALSA topology & routing reference.** The single end-to-end picture: how every source converges on ALSA `default`, the layered parametric-EQ and `Radio Volume` softvol/dmix stages, the `bluealsa-aplay` and `alsaloop` bridges, stable-card routing, worked `asound.conf` examples, and verification. |
| [equalizer.md](equalizer.md) | **Parametric-EQ user and developer guide.** Web controls, headroom, Save-and-Apply/Reset behavior, persistence, troubleshooting, ALSA/LADSPA topology, implementation and tests. |
| [display-test.md](display-test.md) | Standalone 1.69" ST7789 display smoke test & wiring troubleshooting. |
| [stations.md](stations.md) | How to add / edit the internet-radio preset stations. |
| [web-interface.md](web-interface.md) | The local web administration interface: login, dashboard and settings pages, firmware management, restart/reboot behavior, managed state, and recovery. |
| [bluetooth.md](bluetooth.md) | The Bluetooth (A2DP) music source: how to connect a phone (no PIN), connection-gated pairing mode, what plays / shows on the display, how it is built/wired, and on-target validation & troubleshooting. |
| [usb-audio.md](usb-audio.md) | The USB Audio Class source: profile, source switching, validation, and troubleshooting. Wiring and the no-VBUS requirement are in `hardware.md`. |
| [logos.md](logos.md) | How station logos are rendered on the display, how to prepare/add your own, the generated fallback tile, and the `[ui]` backdrop/contrast knobs. |
| [adding-a-music-source.md](adding-a-music-source.md) | How to add a new `MusicSource` playback backend. |
| [assets.md](assets.md) | Asset management policy for the vendored UI font and station logos: provenance, licensing, checksums, and how to replace them. |

> Every document in this directory is complete. Note that no 3D-printable case
> or speaker files are part of this repository — see [hardware.md](hardware.md).
