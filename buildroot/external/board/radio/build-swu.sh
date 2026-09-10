#!/bin/sh
# Build the deterministic, unsigned SWUpdate artifact from the completed rootfs.
set -eu

BOARD_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH='' cd -- "${BOARD_DIR}/../../../.." && pwd)

# shellcheck source=/dev/null
. "${BOARD_DIR}/image-layout.conf"

set -- \
	--rootfs "${BINARIES_DIR}/rootfs.ext4" \
	--template "${BOARD_DIR}/sw-description.in" \
	--version-file "${REPO_DIR}/lib/_version.py" \
	--output-dir "${BINARIES_DIR}" \
	--slot-size "${ROOTFS_SLOT_SIZE}"

# Buildroot's target package is ARM and cannot validate an archive on the amd64
# build host. Prefer the explicitly configured native checker, then the native
# checker locations used by the supported build helper, and finally PATH.
if [ -z "${SWUPDATE_CHECKER:-}" ]; then
	SWUPDATE_CHECKER=""
	if [ -n "${BUILDROOT_DIR:-}" ]; then
		buildroot_parent=$(CDPATH='' cd -- "${BUILDROOT_DIR}/.." && pwd)
		for candidate in \
			"${buildroot_parent}/swupdate-checker-build/swupdate" \
			"${buildroot_parent}/swupdate-native/usr/bin/swupdate"; do
			if [ -x "$candidate" ]; then
				SWUPDATE_CHECKER="$candidate"
				break
			fi
		done
	fi
	if [ -z "$SWUPDATE_CHECKER" ]; then
		SWUPDATE_CHECKER=$(command -v swupdate || true)
	fi
fi
if [ -z "${SWUPDATE_CHECKER}" ] || [ ! -x "${SWUPDATE_CHECKER}" ]; then
	echo "build-swu.sh: native swupdate checker not found" >&2
	echo "build-swu.sh: set SWUPDATE_CHECKER or install swupdate on PATH" >&2
	exit 1
fi
set -- "$@" --swupdate-checker "${SWUPDATE_CHECKER}"

python3 "${REPO_DIR}/scripts/build_firmware_swu.py" "$@"