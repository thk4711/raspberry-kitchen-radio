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
# checker locations used by the supported build helper, and finally PATH. A
# candidate is only accepted if it actually runs: a binary whose shared
# libraries (for example libubootenv.so.0) cannot be resolved exits 127, which
# must be skipped rather than selected and failed on at the very last step.
checker_runs() {
	# $1: candidate path. Returns success unless the candidate fails to execute
	# (dynamic-loader failure exits 127). Check mode against an empty file makes
	# the loader resolve every needed library without needing a real package.
	_probe=$(mktemp) || return 1
	"$1" -c -i "$_probe" -e stable,slot-a >/dev/null 2>&1
	_status=$?
	rm -f "$_probe"
	[ "$_status" -ne 127 ]
}

if [ -n "${SWUPDATE_CHECKER:-}" ]; then
	if [ ! -x "${SWUPDATE_CHECKER}" ] || ! checker_runs "${SWUPDATE_CHECKER}"; then
		echo "build-swu.sh: configured SWUPDATE_CHECKER cannot run: ${SWUPDATE_CHECKER}" >&2
		echo "build-swu.sh: install its shared libraries (for example libubootenv0.1) or fix the path" >&2
		exit 1
	fi
else
	SWUPDATE_CHECKER=""
	if [ -n "${BUILDROOT_DIR:-}" ]; then
		buildroot_parent=$(CDPATH='' cd -- "${BUILDROOT_DIR}/.." && pwd)
		for candidate in \
			"${buildroot_parent}/swupdate-checker-build/swupdate" \
			"${buildroot_parent}/swupdate-native/usr/bin/swupdate"; do
			if [ -x "$candidate" ] && checker_runs "$candidate"; then
				SWUPDATE_CHECKER="$candidate"
				break
			fi
		done
	fi
	if [ -z "$SWUPDATE_CHECKER" ]; then
		path_checker=$(command -v swupdate || true)
		if [ -n "$path_checker" ] && checker_runs "$path_checker"; then
			SWUPDATE_CHECKER="$path_checker"
		fi
	fi
fi
if [ -z "${SWUPDATE_CHECKER}" ] || [ ! -x "${SWUPDATE_CHECKER}" ]; then
	echo "build-swu.sh: no working native swupdate checker found" >&2
	echo "build-swu.sh: install the swupdate package (Debian/Ubuntu: apt-get install swupdate," >&2
	echo "build-swu.sh: which pulls libubootenv0.1) or set SWUPDATE_CHECKER to a runnable binary" >&2
	exit 1
fi
set -- "$@" --swupdate-checker "${SWUPDATE_CHECKER}"

python3 "${REPO_DIR}/scripts/build_firmware_swu.py" "$@"