################################################################################
#
# go-librespot
#
################################################################################

# go-librespot is fetched from upstream GitHub at an immutable release tag.
#
# Reproducibility contract (item 8): the accompanying go-librespot.hash pins the
# SHA-256 of the upstream GitHub source archive for the tag below. That archive
# is immutable and byte-stable, so a clean build verifies the exact source
# content before Buildroot's golang infra vendors the module dependencies.
#
# The go.mod 'go 1.25' / toolchain go1.25.5 directive is satisfied by the host-Go
# shipped with the pinned Buildroot version used to build this appliance
# (BUILDROOT_VERSION=2026.05.2 in buildroot/build.sh). Building with that pinned
# Buildroot + this tag reproduces the same accepted source across build hosts.
GO_LIBRESPOT_VERSION = v0.9.0
GO_LIBRESPOT_SITE = $(call github,devgianlu,go-librespot,$(GO_LIBRESPOT_VERSION))
# Upstream LICENSE is the GNU GPL v3 with the "or (at your option) any later
# version" grant (see the file header and the "How to Apply" notice), so the
# precise SPDX identifier is GPL-3.0-or-later (the previous "LGPL-3.0" was wrong
# on both the family and the version-suffix).
GO_LIBRESPOT_LICENSE = GPL-3.0-or-later
GO_LIBRESPOT_LICENSE_FILES = LICENSE

# go-librespot links against ALSA (cgo) for audio output and libvorbis/libogg
# for decoding.
GO_LIBRESPOT_DEPENDENCIES = host-pkgconf alsa-lib libvorbis libogg

# Strip the binary (cgo is required for the ALSA backend; the golang infra
# enables it automatically because of the cgo deps above).
GO_LIBRESPOT_LDFLAGS = -s -w

# Build the CLI entrypoint. The module main package lives in ./cmd/daemon.
GO_LIBRESPOT_BUILD_TARGETS = cmd/daemon

# Install the resulting binary as "go-librespot" on the target.
define GO_LIBRESPOT_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/bin/daemon \
		$(TARGET_DIR)/usr/bin/go-librespot
endef

$(eval $(golang-package))
