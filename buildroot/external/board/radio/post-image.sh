#!/bin/bash
# =============================================================================
# post-image.sh — Raspberry Kitchen Radio image assembly.
#
# Self-contained replacement for the stock board/raspberrypi/post-image.sh. It
# builds the SD-card image (boot.vfat FAT partition + ext4 rootfs -> sdcard.img)
# with genimage, with two radio-specific changes:
#
#   1. A LARGER FAT boot partition (default 64M, was Buildroot's stock 32M), so
#      the firmware + kernel + DTBs + config/cmdline + radio-config.txt.example
#      fit with headroom. Override with RADIO_BOOT_VFAT_SIZE (e.g. "128M").
#   2. radio-config.txt.example is placed on the FAT partition so a user who
#      flashes the prebuilt image can rename it to radio-config.txt and edit
#      WiFi / hostname / etc. with a text editor — no rebuild needed. The
#      /usr/sbin/provision-from-boot helper reads it at boot.
#
# Buildroot exports BINARIES_DIR / BUILD_DIR to post-image scripts. genimage and
# the FAT tooling are available because the defconfig enables
# BR2_PACKAGE_HOST_GENIMAGE / BR2_PACKAGE_HOST_MTOOLS / BR2_PACKAGE_HOST_DOSFSTOOLS.
#
# The file list mirrors the stock RPi post-image: every *.dtb and every file
# under rpi-firmware/, plus the kernel named by the "kernel=" line in config.txt.
# =============================================================================
set -e

# Boot partition size (genimage syntax, e.g. 32M / 64M / 128M).
BOOT_SIZE="${RADIO_BOOT_VFAT_SIZE:-64M}"

# This script's directory (in the external tree), to find our template + example.
BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
GENIMAGE_IN="${BOARD_DIR}/genimage.cfg.in"
EXAMPLE="${BOARD_DIR}/radio-config.txt.example"

GENIMAGE_CFG="${BINARIES_DIR}/genimage.cfg"
GENIMAGE_TMP="${BUILD_DIR}/genimage.tmp"

# --- Place the provisioning template on the FAT partition --------------------
# Copy it into BINARIES_DIR so genimage picks it up as a boot file (relative to
# --inputpath BINARIES_DIR), landing it on boot.vfat next to config.txt.
if [ -f "$EXAMPLE" ]; then
	cp -f "$EXAMPLE" "${BINARIES_DIR}/radio-config.txt.example"
fi

# --- Apply the per-device DAC overlay (RADIO_DAC) to the boot config.txt -----
# The tracked board config.txt ships a default overlay; build.sh exports the
# chosen overlay in RADIO_DAC (from the git-ignored local-device.conf). We edit
# only the generated copy under BINARIES_DIR (a build artifact), never a tracked
# file. Empty RADIO_DAC leaves the shipped default in place.
BOOT_CONFIG="${BINARIES_DIR}/rpi-firmware/config.txt"
VALID_DACS="iqaudio-dacplus hifiberry-dacplus merus-amp"
if [ -n "${RADIO_DAC:-}" ] && [ -f "$BOOT_CONFIG" ]; then
	for dac in $VALID_DACS; do
		sed -i "s/^#*dtoverlay=${dac}\$/#dtoverlay=${dac}/" "$BOOT_CONFIG"
	done
	sed -i "s/^#dtoverlay=${RADIO_DAC}\$/dtoverlay=${RADIO_DAC}/" "$BOOT_CONFIG"
	echo "post-image.sh: selected DAC overlay '${RADIO_DAC}' in boot config.txt"
fi

# --- Build the boot-file list (mirrors stock RPi post-image) -----------------
FILES=()
for f in "${BINARIES_DIR}"/*.dtb "${BINARIES_DIR}"/rpi-firmware/*; do
	[ -e "$f" ] || continue
	FILES+=( "${f#"${BINARIES_DIR}"/}" )
done

KERNEL=$(sed -n 's/^kernel=//p' "${BINARIES_DIR}/rpi-firmware/config.txt")
[ -n "$KERNEL" ] && FILES+=( "${KERNEL}" )

# Ship the provisioning template on the FAT partition (if present).
[ -f "${BINARIES_DIR}/radio-config.txt.example" ] && FILES+=( "radio-config.txt.example" )

# --- Generate the genimage config from our template --------------------------
# Write the boot-file list to a temp file and splice it in with sed's "r"
# command. This avoids embedding literal newlines in a sed replacement, which
# GNU sed accepts but some seds (e.g. BSD) reject — keeping the script portable.
FILE_LIST_TMP="$(mktemp)"
printf '\t\t\t"%s",\n' "${FILES[@]}" > "$FILE_LIST_TMP"

sed -e "s|#BOOT_SIZE#|${BOOT_SIZE}|" \
    -e "/#BOOT_FILES#/ {
r ${FILE_LIST_TMP}
d
}" \
    "$GENIMAGE_IN" > "$GENIMAGE_CFG"
rm -f "$FILE_LIST_TMP"

# --- Run genimage (same invocation as stock) ---------------------------------
trap 'rm -rf "${ROOTPATH_TMP}"' EXIT
ROOTPATH_TMP="$(mktemp -d)"
rm -rf "${GENIMAGE_TMP}"

genimage \
	--rootpath "${ROOTPATH_TMP}"   \
	--tmppath "${GENIMAGE_TMP}"    \
	--inputpath "${BINARIES_DIR}"  \
	--outputpath "${BINARIES_DIR}" \
	--config "${GENIMAGE_CFG}"

echo "post-image.sh: built sdcard.img with ${BOOT_SIZE} FAT boot partition (incl. radio-config.txt.example)"
exit 0

