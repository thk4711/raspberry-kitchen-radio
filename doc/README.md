# Documentation

This directory collects the project documentation.

| Document | Purpose |
| --- | --- |
| [../CHANGELOG.md](../CHANGELOG.md) | Release notes / version history (Keep a Changelog); includes the release checklist. |
| [development.md](development.md) | **Developer / contributor guide.** Local setup (virtualenv), running the pytest suite & coverage, linting (`ruff`), type checks (`mypy`), and the CI pipeline. |
| [build-from-scratch.md](build-from-scratch.md) | Beginner's step-by-step guide from a fresh download of the project to a flashed SD card (no embedded-Linux experience assumed). |
| [buildroot.md](buildroot.md) | **Buildroot appliance image reference.** Build (scripted & manual), flash, on-target validation, external-tree/defconfig/service layout, reproducible-build source pins, design constraints, and background/design decisions. |
| [hardware.md](hardware.md) | Wiring diagram, GPIO pinout, and ADS1115 channel map. |
| [display-test.md](display-test.md) | Standalone 1.69" ST7789 display smoke test & wiring troubleshooting. |
| [stations.md](stations.md) | How to add / edit the internet-radio preset stations. |
| [bluetooth.md](bluetooth.md) | The Bluetooth (A2DP) music source: how to connect a phone (no PIN), connection-gated pairing mode, what plays / shows on the display, how it is built/wired, and on-target validation & troubleshooting. |
| [logos.md](logos.md) | How station logos are rendered on the display, how to prepare/add your own, the generated fallback tile, and the `[ui]` backdrop/contrast knobs. |
| [adding-a-music-source.md](adding-a-music-source.md) | How to add a new `MusicSource` playback backend. |
| [assets.md](assets.md) | Asset management policy for the vendored UI font and station logos: provenance, licensing, checksums, and how to replace them. |

> Every document in this directory is complete. Note that no 3D-printable case
> or speaker files are part of this repository — see [hardware.md](hardware.md).
