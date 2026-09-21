# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **HiFiBerry Amp / Amp+ is now hardware-tested.** Raspberry Pi 3A+ verification
  confirmed TAS5713 discovery, stable ALSA routing through `sndrpihifiberry`, the
  hardware `Master` volume control and `S16_LE` / 44100 Hz playback.

## [0.3.0] - 2026-09-16

### Added
- **Selectable round-display support** adds the 240×240 GC9A01 panel alongside
  the existing 240×280 ST7789. A shared panel factory, base driver, shape-aware
  layout, circular volume gauge, and web setting make panel selection testable
  off-target and configurable without maintaining separate display stacks.
- **Display rotation and wiring references** add a `rotate_180` setting, a full
  radio hardware schematic, and a generated color-coded sound-device pinout
  published through GitHub Pages.
- **Bounded operational recovery summary** preserves only privacy-safe boot,
  shutdown, radio-restart, firmware-slot, and health outcomes across reboots
  while routine logs remain volatile. The 16 KiB/32-event summary is included in
  diagnostics but excluded from user backup and restore.
- **Enforced Python formatting baseline** applies Ruff format to all first-party
  Python and verifies it in CI and pre-commit while excluding vendored code.
- **TI TAS5713 amplifier support** via the `hifiberry-amp` overlay (HiFiBerry
  Amp / Amp+). New experimental audio profile `hifiberry_amp` using the codec's
  hardware `Master` control on ALSA card `sndrpihifiberry`; the TAS5713 codec
  driver (`CONFIG_SND_SOC_TAS5713`) is now built into the image.
- **Automated release-consistency validation** checks the application and package
  versions, changelog release/date, canonical update artifact name, generated
  firmware metadata, public documentation examples, and Git tag type/date in CI.
- **Repository-contained build orchestrator** (`scripts/build_image.py`) supports
  local builds without SSH and opt-in remote builds with verified artifact
  transfer. A dummy INI example provides reusable settings without embedding a
  maintainer's host, user, or filesystem paths.
- **Strict Buildroot checkout validation** rejects non-Git, wrong-revision, and
  dirty source trees by default. Build logs and firmware metadata record both
  source commits; an explicit development-only override permits local changes.

### Changed
- **Display architecture and themes** now use a panel-neutral `lib/display`
  package, full-frame compositor, panel-specific geometry, and configurable OSD
  arc span and ring thickness while retaining the existing ST7789 behavior.
- **Release quality gates** expand firmware, partition, networking, route-handler,
  and off-target hardware tests, raise the first-party coverage floor to 83%, and
  enforce Ruff formatting and complete production-code mypy checks in CI.

### Fixed
- **Remote image builds** now honor non-default SSH ports, auto-detect parallel
  jobs on the build host, validate SWUpdate hardware compatibility, avoid an
  ambiguous shell fallback, and embed the exact source commit even though the
  staged source intentionally excludes `.git`.
- Corrected documentation indexing and development dependency declarations used
  by CI type checking.

### Reproducible build inputs
- Buildroot `2026.05.2`, commit
  `72d9d4fa636a371ef9eb99c92a735ce9f6d829d5`.
- go-librespot `v0.9.0`, vendored source archive SHA-256
  `9e4e1ab1871267ba5cace600a7b3025681b5177195ce9a246e5c91e63b021328`.
- shairport-sync `4.3.7`, source archive SHA-256
  `a1242d100b61fe1fffbbf706e919ed51d6a341c9fb8293fb42046e32ae2b3338`.
- nqptp `1.2.4`, source archive SHA-256
  `1df1d5edd5b713010d6495b3abca4c1cf4ad8fa6029df0abeb9e4de8e0eb707a`.

## [0.2.0] - 2026-09-10

### Added
- **Local web administration interface** (`radio_web`) for configuring the
  appliance from a browser: dashboard status, music sources and stations,
  display settings, device settings (name, timezone, NTP), and a Maintenance
  page. The server runs as an unprivileged `radio-web` user; every privileged
  operation goes through a fixed-whitelist, root-owned unix-socket helper.
  CSRF protection and fresh-password confirmation guard destructive actions.
- **Selectable sound card from the web UI** backed by a declarative, tiered
  I2S catalog (IQaudIO, generic PCM510x/MAX98357A, HiFiBerry, Allo BOSS,
  Audiophonics I-SABRE Q2M, Allo Katana, JustBoom, MERUS) plus the Pi's
  built-in headphone jack. Profiles route by stable ALSA card id, use hardware
  volume where available and a shared softvol control otherwise; applying edits
  `config.txt`/`asound.conf`/MPD safely and is a reboot-level operation.
- **USB Audio Class 1 music source.** The Pi 3A+ DWC2 controller runs in
  peripheral mode and exposes a stereo 48-kHz UAC1 gadget bridged to the I2S
  DAC, with automatic source switching (D+/D-/GND-only wiring; no VBUS).
- **Bluetooth (A2DP) music source.** A phone/tablet can stream over the on-chip
  Bluetooth radio (attached over the PL011 UART). BlueZ + bluez-alsa are built,
  the adapter stays discoverable and auto-accepts pairing with no PIN while
  idle, AVRCP track metadata drives the display, and the radio auto-switches to
  Bluetooth on playback. See [`doc/bluetooth.md`](doc/bluetooth.md).
- **Ten-band ALSA/LADSPA parametric equalizer** on the Audio page, with a
  draggable response graph, validated persistent settings, per-band and global
  controls, live apply (via a memory-mapped runtime file, no playback
  interruption), and a user/developer guide at `doc/equalizer.md`.
- **Safe A/B firmware updates.** A fixed four-partition image (loader, A/B root
  slots, shared `/data`) with U-Boot redundant-environment slot selection,
  trial-boot counting and automatic rollback, and a health check that accepts
  firmware only after local data/schema/web/heartbeat/MPD checks pass. A guided
  Maintenance overlay handles authenticated upload, install, reboot, reconnect
  and health reporting as one workflow, and can switch to the retained
  compatible slot. Image builds emit a deterministic, versioned SWUpdate package
  alongside `sdcard.img`.
- **Persistent-data compatibility contract** shared by release metadata and
  update packages, with ordered atomic early-boot migrations, rollback-protected
  trial acceptance, and rejection of schema combinations that would leave the
  retained firmware unable to read `/data`.
- **Authenticated backup and restore** in Maintenance: versioned,
  integrity-checked archives of radio/network/device/admin settings and
  Bluetooth pairings (firmware/update bookkeeping excluded). Restore rejects
  traversal, links, special files, incompatible schemas, oversized archives and
  hash mismatches, and rolls back if activation fails.
- **Project tooling & hygiene.** Single-source project version
  (`lib/_version.py`, boot splash + `--version`), `ruff` and `mypy` wired into
  CI (mypy gated incrementally), a Python version matrix (3.9/3.11/3.13), the
  `transient_state.TransientState` display state machine extracted with unit
  tests, and repository-artifact hygiene checks.
- **Contributor & community files:** `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, GitHub issue/PR templates, `.editorconfig`, and a
  `.pre-commit-config.yaml` mirroring the repo's lint/format/hygiene rules.

### Changed
- Reorganised the web UI's display and audio settings for clarity, standardised
  control styling (explicit colors for save/apply/utility/warning/destructive
  actions, no rounded corners), and clarified the Maintenance distinction
  between restarting the player and rebooting/shutting down the device.
- **Bluetooth simplified to read-only status.** The separate Bluetooth page was
  removed; adapter status now appears as a read-only card on the dashboard, and
  the unused privileged helper actions were dropped.
- **Bluetooth placeholder art.** When a Bluetooth phone is connected (no cover
  art), the display renders the official Bluetooth logo on a muted-blue tile
  instead of a name-derived initials tile.
- Documented the complete persistent-data inventory and fail-closed early-boot
  `/data` mount/link setup, and expanded firmware-update documentation into
  operator, architecture/porting, and troubleshooting/recovery guides.
- Generic images now ship with obvious defaults (`changeme` hostname/password,
  UTC, SSH disabled) and a one-shot boot `radio-config.txt`; the display SPI
  chip-select is configurable (`spi_device`).
- `radio.py` startup progress now goes through `logging` instead of `print()`,
  and CI enforces a coverage floor so test coverage cannot silently regress.

### Removed
- **Build-time per-device configuration** (`buildroot/configure.sh`, its
  `local-device.conf` handoff, generated WiFi overlay, and `build.sh
  --configure`). Builds now always produce a reusable generic image; edit the
  shipped `radio-config.txt` after flashing and use the web UI thereafter.
- **Internal planning documents** (`WEB-INTERFACE.md`,
  `doc/sound-card-selection-plan.md`) now that the features they described are
  implemented and covered by the user-facing docs; their code citations were
  stripped with no behaviour change.

## [0.1.0] - 2026-09-04

Initial tracked baseline of the Buildroot appliance radio (MPD internet radio,
AirPlay, Spotify Connect, Bluetooth A2DP, USB Audio, SPI display, ADS1115 analog
controls, and the local web administration interface).

### Fixed
- **Web dashboard and first-use setup permissions.** Player snapshots are now
  published mode `0644` so the unprivileged `radio-web` service can display
  now-playing information. Buildroot applies `radio-web` ownership to
  `/etc/radio` and `/var/lib/radio-web` through a fakeroot device/permission
  table, allowing first-use password and managed-setting writes. Unexpected web
  request failures now return HTTP 500 instead of an empty response, and the
  service keeps a volatile `/tmp/radio-web.log` for diagnostics.

### Changed
- **Web administration — Bluetooth simplified to read-only status.** The
  separate **Bluetooth** page has been removed. Its adapter status (visible
  name, powered, discoverable) now appears as a **read-only** card on the
  dashboard (`radio_web/bluetooth_store.adapter_info()` via
  `system_status.collect()`). The page's "Enable pairing mode" button was
  redundant — the appliance keeps the adapter discoverable and auto-accepts
  pairing with no PIN whenever nothing is connected (`S42bluetooth`), so no
  manual action is needed. The now-unused privileged helper actions
  `bt_remove`, `bt_disconnect` and `bt_pairing_on` were dropped from
  `radio_web/helper.py` and `radio_web/helper_protocol.py`; forget/disconnect,
  if ever needed, is a `bluetoothctl` task on the device itself.

### Added
- **Web administration — selectable sound card.** A new **Sound card** page
  (`/audio-hardware`) lets you switch the active sound card from the web UI —
  as shipped, between the **IQaudIO DAC+ (I2S)** and the Raspberry Pi's
  **built-in headphone jack** — automating the manual procedure in
  [`doc/analog-audio.md`](doc/analog-audio.md). The choice is saved to
  `/etc/radio/audio_hardware.ini` from a single declarative profile catalog
  (`radio_web/audio_hardware_store.py`), and a new re-validated helper action
  `set_audio_hardware` (in `radio_web/audio_hardware_apply.py`) applies it: it
  briefly mounts the FAT boot partition read-write to edit **only** the
  `dtparam=audio=` / `dtoverlay=` lines in `config.txt` (backing it up first and
  always unmounting), writes `/etc/asound.conf` (I2S dmix→`hw:0,0`, or a stable
  `sysdefault:CARD=…` block for on-board audio), rewrites the MPD `audio_output`
  `device`/`mixer_control`, writes any `modules-load.d` entry, and sets the
  profile-derived mixer and per-card amplifier-enable GPIO in the sound-card
  catalog so the volume knob, MPD and the player's amp control agree;
  `audio.ini` now stores only the user-selected maximum-volume cap. Because a DAC
  overlay is resolved by the firmware at boot, applying is a **reboot-level**
  operation that ends on a "Reboot required" confirmation — nothing is changed
  live, and the SD-card `radio-config.txt` remains the recovery route. Adding a
  future I2S card is a one-row catalog change (plus, optionally, the build-time
  `VALID_DACS` list). A missing `audio_hardware.ini` resolves to the shipped
  sound-card default. See
  [`doc/web-interface.md`](doc/web-interface.md).
- **Web administration — device settings, maintenance & privilege split
  (Phase 7).** The local web UI (`radio_web`) gains a **Device settings** page
  (device name, timezone, NTP server — written to `/etc/radio/device.ini` and
  applied like `provision-from-boot`; the device name is saved now and applied
  on reboot, and is locked when the SD-card `radio-config.txt` overrides it) and
  a **Maintenance** page (restart radio / MPD, reboot, shut down, and a
  size-capped, secret-free diagnostics `tar.gz` download of the volatile `/tmp`
  logs). Reboot/shutdown require a confirmation page and a fresh password check.
  This phase also completes the **privilege split**:
  the web server now runs as a dedicated unprivileged `radio-web` user, and all
  privileged operations go through a new **root-owned unix-socket helper**
  (`radio_web.helper`, started by `S79radio-helper` before `S80radio-web`) that
  accepts only a fixed whitelist of action ids, each mapped to one hard-coded
  `shell=False` operation and re-validated server-side.
- **USB Audio Class 1 music source.** The Pi 3A+ DWC2 controller now runs in
  peripheral mode and exposes a stereo S16_LE/48-kHz playback-only UAC1 gadget.
  BusyBox service `S39usb-audio` owns the ConfigFS lifecycle and an adaptive
  `alsaloop`/libsamplerate bridge to the shared I2S DAC output. The Python
  `USBAudioService` auto-selects active host streams and uses an inhibition latch
  to switch reliably to other sources when a host keeps its stream open. Added
  unit tests, diagnostics, and explicit D+/D−/GND-only wiring guidance that
  forbids connecting USB VBUS/+5 V.
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
- **Web administration — reorganised display/audio pages.** The web UI's
  display and audio settings were regrouped for clarity:
  - The **Display & audio** page (`/settings`) is now just **Display** — it keeps
    only the display theme/behaviour options.
  - The **Sound card** page (`/audio-hardware`) is now **Audio** and also hosts
    the **Maximum volume** cap (moved off the old Display & audio page). The
    maximum volume applies with a radio restart; the sound-card selection remains
    a reboot-level change.
  - The **ALSA mixer name** field was removed from the UI: the mixer control is
    already set automatically by the sound-card selection, so there is nothing to
    configure by hand (`audio_store.validate_settings` now preserves the stored
    `mixer`/`amp` when a form omits them).
  - The **Physical controls** card (ADC debug & calibration link) moved from the
    old Display & audio page to **Device settings** (`/device`).
  - The read-only **Dependencies** (D-Bus/Avahi) box was removed from the
    **Music sources** page since there was nothing there for the user to
    configure.

  The `/settings` and `/audio-hardware` URLs and the managed
  `/etc/radio/display.ini` / `/etc/radio/audio.ini` files are unchanged, so a
  fresh image behaves exactly as before.

<!--
Release checklist (when cutting X.Y.Z):
  1. Bump __version__ in lib/_version.py and version in pyproject.toml.
  2. Move the Unreleased notes under a new "## [X.Y.Z] - DATE" heading.
  3. Record the pinned Buildroot revision + media-backend source hashes
     (see doc/buildroot.md, "Reproducible builds / pinned sources").
  4. Commit the release, create an annotated tag dated on the changelog date:
     git tag -a vX.Y.Z -m "vX.Y.Z"
  5. Run python3 scripts/check-release-consistency.py.
  6. Run a clean supported-host Buildroot build and retain its build log.
  7. Confirm exactly one fresh, non-empty sdcard.img and one matching versioned
     kitchen-radio-X.Y.Z.swu; reject stale or ambiguous outputs.
  8. Confirm SWUpdate check mode accepts the .swu for both slot-a and slot-b,
     and verify release metadata, hardware compatibility, payload size/hash,
     and exclusion of credentials or device-specific state.
  9. Record and publish the size and SHA-256 of both release artifacts.
 10. On a disposable Pi 3A+ card, test A-to-B and B-to-A installation, healthy
     trial acceptance, automatic rollback, manual switch/rollback, and retained
     persistent configuration. Record the hardware evidence; do not infer this
     result from host tests.
 11. Push the release commit and its annotated tag: git push origin main vX.Y.Z

Steps 5-7 and 9 (and the GitHub release upload) are automated by
scripts/release.py; see doc/releasing.md. Steps 1-4, 8, 10, and 11 stay manual.
Typical use, after completing steps 1-4:
    python3 scripts/release.py X.Y.Z --notes notes.md          # build + draft
    python3 scripts/release.py X.Y.Z --notes notes.md \
        --skip-build --publish --force                          # after step 10

Tag policy: release tags must be annotated. v0.2.0 is the sole historical
exception: it was published as a lightweight tag before this policy was enforced
and is retained unchanged to avoid rewriting a public tag.
-->
