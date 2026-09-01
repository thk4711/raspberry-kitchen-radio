# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Bluetooth (A2DP) music source.** A fourth `MusicSource` backend
  (`lib/bluetooth_service`) streams audio from any phone/tablet over Bluetooth.
  The appliance's on-chip Bluetooth radio is enabled and attached over the
  **PL011 UART** (`ttyAMA0`); `config.txt` deliberately does **not** use
  `dtoverlay=pi3-miniuart-bt` (the mini-UART cannot sustain the A2DP data rate —
  it caused continuous HCI `continuation frame` errors and choppy/silent audio),
  and sets `enable_uart=1` so the PL011 comes up deterministically. The image now
  builds BlueZ (`bluez5_utils` with the A2DP/AVRCP audio plugins + the CLI
  client) and `bluez-alsa`. A new init script `S42bluetooth` holds one
  long-lived `bluetoothctl` session (driven through a `/run` control FIFO) that
  registers a no-PIN auto-accept agent (`agent auto`, which auto-confirms SSP
  numeric-comparison pairing so no code/tap is needed) and sets the adapter
  alias to the hostname (the name phones display), keeps the adapter
  discoverable / pairable **whenever no device is bonded** (only stopping once a
  device is fully paired + connected, so it never aborts an in-flight pairing
  handshake — and re-opening automatically when the phone disconnects), and runs
  `bluealsa` (A2DP receiver) +
  `bluealsa-aplay` (routing the received PCM to the ALSA default device / I2S
  DAC). `bluetoothd` itself is owned by Buildroot's `S40bluetoothd`, augmented
  with `--experimental` via `/etc/default/bluetoothd` so the AVRCP
  `org.bluez.MediaPlayer1` track metadata (title/artist) is exposed on D-Bus and
  read by the Python service for the display. A2DP/AVRCP carries no cover art,
  so the display shows a generated **Bluetooth-logo placeholder tile** for this
  source instead of album art (see the Changed entry below). The radio
  auto-switches to Bluetooth when the phone
  reports playback over AVRCP, stopping whatever was playing. Security note:
  no-PIN + discoverable-while-idle means any device in range can connect — a
  deliberate, documented choice for a kitchen appliance. See
  [`doc/bluetooth.md`](doc/bluetooth.md) for usage, behaviour and troubleshooting.
- Project version single source (`lib/_version.py`, re-exported from `lib`),
  surfaced on the boot splash subtitle and via a `radio.py --version` flag.
- `ruff` (lint) and `mypy` (static type check) wired into
  `requirements-dev.txt` and CI. mypy is gated incrementally on the fully-typed,
  self-contained modules first (`mypy` reads its `files` list from
  `pyproject.toml`) and widened as older modules gain types. (`ruff format` is
  available for authoring but not yet gated repo-wide to avoid churn on the
  hand-formatted display code.)
- `transient_state.TransientState`: the display's OSD / preset-toast / art
  crossfade / idle-activity timing extracted from `DisplayController` into a
  dependency-free, unit-tested state machine (with unit tests).
- CI now runs across a Python version matrix (3.9 / 3.11 / 3.13).
- `scripts/check-repository.sh` now fails the build if `.coverage`, `.DS_Store`,
  or any `*.pyc` / `__pycache__` artifact is ever tracked.

### Changed
- **Bluetooth placeholder art.** When a Bluetooth phone is connected (no cover
  art over A2DP/AVRCP), the display now renders the **official Bluetooth logo on
  a muted-blue tile** instead of the name-derived initials tile (which, with a
  blank artist, showed a "?" on a violet chip). The glyph is the public-domain
  `Bluetooth.svg` path transcribed and stroked with Pillow — faithful to the
  official mark with no SVG rasteriser or new dependency
  (`logo_fallback.render_bluetooth_tile`, selected per source in
  `DisplayController._fallback_logo()`).
- `radio.py` startup progress now goes through `logging` instead of `print()`.
- `pyproject.toml` declares `requires-python = ">=3.9"` and project metadata.

## [0.1.0] - Unreleased

Initial tracked baseline of the Buildroot appliance radio (MPD internet radio,
AirPlay, Spotify Connect, SPI display, ADS1115 analog controls).

<!--
Release checklist (when cutting X.Y.Z):
  1. Bump __version__ in lib/_version.py and version in pyproject.toml.
  2. Move the Unreleased notes under a new "## [X.Y.Z] - DATE" heading.
  3. Record the pinned Buildroot revision + media-backend source hashes
     (see doc/buildroot.md, "Reproducible builds / pinned sources").
  4. Commit, then tag: git tag -a vX.Y.Z -m "vX.Y.Z" && git push --tags
-->
