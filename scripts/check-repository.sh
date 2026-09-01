#!/bin/sh
set -eu
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
test ! -e buildroot/external/board/radio/rootfs-overlay/var/lib/wlan-last-lease.env
test ! -e lib/spotify_service/spotify.conf
# Per-device settings and WiFi credentials must never be tracked.
if git ls-files --error-unmatch buildroot/local-device.conf >/dev/null 2>&1; then
    echo "buildroot/local-device.conf must not be tracked (device-specific state)" >&2
    exit 1
fi
# Build/test artifacts must never be committed (they are git-ignored, but this
# also fails the build if one is force-added or a mistake tracks one).
if git ls-files -z \
    | grep -zqE '(^|/)(\.coverage(\..+)?|\.DS_Store)$|(^|/)__pycache__/|\.py[cod]$'; then
    echo "Artifacts (.coverage/.DS_Store/__pycache__/*.pyc) must not be tracked" >&2
    exit 1
fi
if git ls-files -z | xargs -0 file | grep -q 'CRLF line terminators'; then
    echo "Tracked files with CRLF line endings found" >&2
    exit 1
fi