#!/bin/sh
# Validate that one build produced exactly the expected fresh release artifacts.
set -eu

if [ "$#" -ne 3 ]; then
	echo "usage: validate-artifacts.sh IMAGES_DIR VERSION BUILD_MARKER" >&2
	exit 2
fi

images_dir="$1"
version="$2"
build_marker="$3"
img="${images_dir}/sdcard.img"
swu="${images_dir}/pisonic-${version}.swu"

[ -f "$build_marker" ] || {
	echo "validate-artifacts.sh: build marker is missing: $build_marker" >&2
	exit 1
}
[ -s "$img" ] || {
	echo "validate-artifacts.sh: installation image is missing or empty: $img" >&2
	exit 1
}
[ -s "$swu" ] || {
	echo "validate-artifacts.sh: firmware update is missing or empty: $swu" >&2
	exit 1
}
if [ -z "$(find "$img" -newer "$build_marker" 2>/dev/null)" ]; then
	echo "validate-artifacts.sh: installation image is stale: $img" >&2
	exit 1
fi
if [ -z "$(find "$swu" -newer "$build_marker" 2>/dev/null)" ]; then
	echo "validate-artifacts.sh: firmware update is stale: $swu" >&2
	exit 1
fi

stale=$(find "$images_dir" -maxdepth 1 -type f \
	-name 'pisonic-*.swu' ! -name "$(basename "$swu")" -print -quit)
[ -z "$stale" ] || {
	echo "validate-artifacts.sh: stale firmware artifact remains: $stale" >&2
	exit 1
}

echo "validate-artifacts.sh: release artifacts are present, fresh, and unambiguous"