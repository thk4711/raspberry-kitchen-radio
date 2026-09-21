# Documentation

Project documentation index.

| Document | Purpose |
| --- | --- |
| [../CHANGELOG.md](../CHANGELOG.md) | Release notes / version history (Keep a Changelog); includes the release checklist. |
| [releasing.md](releasing.md) | **Release automation.** Cutting a release with `scripts/release.py`: options, per-step behavior, artifact naming, draft→publish flow, and troubleshooting. |
| [development.md](development.md) | **Developer guide.** Virtualenv setup, the pytest suite and coverage, `ruff`, `mypy`, and CI. |
| [build-from-scratch.md](build-from-scratch.md) | Beginner walkthrough from a fresh download to a flashed A/B SD card and its matching update package. |
| [buildroot.md](buildroot.md) | **Buildroot image reference.** Build, flash, service layout, logging, source pins, and design decisions. |
| [persistent-data.md](persistent-data.md) | **Persistent-data reference.** The full `/data` inventory, image seeding, `p4` mounting, compatibility links, and update isolation. |
| [firmware-updates.md](firmware-updates.md) | **Firmware operator guide.** Upload, installation, trial acceptance, rollback, and recovery. |
| [firmware-update-architecture.md](firmware-update-architecture.md) | **Firmware developer reference.** Image layout, the redundant U-Boot state machine, SWU construction, fallback, and porting checklist. |
| [hardware.md](hardware.md) | Base-radio wiring, full schematic, display, USB gadget, and ADS1115 controls. |
| [analog-audio.md](analog-audio.md) | Switching a development appliance from an I2S DAC to the Pi's built-in headphone output. |
| [sound-devices.md](sound-devices.md) · [color-coded HTML](https://thk4711.github.io/pisonic/sound-devices.html) | Every selectable sound device, with a color-coded 40-pin map, routing, and qualification. The HTML version is published to GitHub Pages by [`../.github/workflows/pages.yml`](../.github/workflows/pages.yml). |
| [alsa-audio-path.md](alsa-audio-path.md) | **ALSA topology & routing reference.** How every source converges on ALSA `default`, the EQ and softvol/dmix stages, the receiver bridges, and verification. |
| [equalizer.md](equalizer.md) | **Parametric-EQ user and developer guide.** Web controls, headroom, persistence, troubleshooting, and the ALSA/LADSPA topology. |
| [display-test.md](display-test.md) | SPI display smoke test and wiring troubleshooting (ST7789 and GC9A01). |
| [stations.md](stations.md) | Adding and editing the internet-radio preset stations. |
| [web-interface.md](web-interface.md) | The web administration interface: login, dashboard and settings pages, firmware management, and recovery. |
| [bluetooth.md](bluetooth.md) | The Bluetooth (A2DP) source: connecting a phone, connection-gated pairing, playback/display behavior, and troubleshooting. |
| [usb-audio.md](usb-audio.md) | The USB Audio Class source: profile, source switching, and troubleshooting. Wiring and the no-VBUS requirement are in `hardware.md`. |
| [logos.md](logos.md) | How station logos are rendered and prepared, the fallback tile, and the `[ui]` backdrop/contrast knobs. |
| [adding-a-music-source.md](adding-a-music-source.md) | Adding a new `MusicSource` playback backend. |
| [assets.md](assets.md) | Asset policy for the vendored UI font and station logos: provenance, licensing, checksums, and replacement. |

> No 3D-printable case or speaker files are part of this repository — see
> [hardware.md](hardware.md).
