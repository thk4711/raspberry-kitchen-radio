include $(sort $(wildcard $(BR2_EXTERNAL_RADIO_PATH)/package/*/*.mk))

# --- shairport-sync source override -----------------------------------------
#
# We track the upstream shairport-sync *development* branch (5.6-dev line)
# instead of the stable release pinned by mainline Buildroot (4.3.7). The
# development branch adds EXPERIMENTAL remote-control support for AirPlay 2
# clients over the D-Bus / MPRIS interfaces; the radio app uses the
# org.gnome.ShairportSync.RemoteControl interface to pause AirPlay when another
# music source takes over. See doc/airplay.md.
#
# HOW the override is wired (NOT here): repointing the mainline package's
# _VERSION/_SITE from this external.mk does not work reliably. The mainline
# package/shairport-sync/shairport-sync.mk is included after this file, assigns
# SHAIRPORT_SYNC_VERSION with a plain `=`, and finalises the package with
# $(eval $(autotools-package)) at its own end. Buildroot's infra snapshots some
# derived paths with immediate `:=` expansion and others with recursive `=`, so
# an `override` here produces an INCONSISTENT package: the source is extracted
# into shairport-sync-<commit> while the build/stamp dir stays
# shairport-sync-4.3.7, and the build fails ("tar: ... No such file").
#
# Instead, buildroot/build.sh clones the pinned development commit into a
# persistent cache dir and passes it to Buildroot as
# SHAIRPORT_SYNC_OVERRIDE_SRCDIR. Buildroot then rsyncs that directory into
# output/build/shairport-sync-custom and SKIPS download/extract/hash entirely,
# so there is no tarball, no .hash mismatch, and no <pkg>-<version> confusion.
# To bump the pin, edit SHAIRPORT_SYNC_DEV_COMMIT in buildroot/build.sh.
#
# Caveats (see doc/airplay.md): this is a MAJOR jump (4.3.7 -> 5.6-dev) —
# config-file keys, dependencies (e.g. JACK removed) and behaviour can change.
# Validate airplay.conf parsing and the D-Bus RemoteControl interface on-target
# after a bump; "development" is experimental, so prefer a health-checked trial
# boot on the A/B appliance before accepting a new pin.
