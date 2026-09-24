PiSonic v0.4.0 for Raspberry Pi 3A+.

This release expands source control, sound tuning, display artwork, and device
administration while improving boot speed and simplifying the ADS1115 driver.

### Added
- Optional online cover-art lookup for Bluetooth tracks, with conservative
  MusicBrainz matching, bounded caching, and offline-safe fallback artwork.
- SVG-derived USB and Bluetooth source glyphs for the display and web dashboard.
- Volume-aware loudness compensation and automatic anti-clipping preamp control
  for the ten-band parametric equalizer.
- Root/SSH password changes from the authenticated web interface.
- A USB consumer-control HID interface that can pause a connected host when USB
  Audio yields to another source.
- Reliable AirPlay source arbitration using remote pause plus a local inhibit
  fallback, with dedicated AirPlay documentation.

### Changed
- U-Boot uses `bootdelay=0` for faster startup.
- USB Audio identifies the gadget as PiSonic.
- The vendored ADS1x15 library was replaced by a small first-party ADS1115
  driver focused on the appliance's single-ended input use case.
- AirPlay uses a pinned shairport-sync 5.6 development revision for AirPlay 2
  remote-control support.
- Preset buttons stop other active sources immediately.

### Fixed
- Restored AirPlay metadata and cover art with the shairport-sync development
  revision.
- Fixed clean Buildroot builds of the pinned shairport-sync development source.
