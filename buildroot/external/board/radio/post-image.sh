#!/bin/bash
# =============================================================================
# post-image.sh — Raspberry Kitchen Radio image assembly.
#
# Self-contained replacement for the stock board/raspberrypi/post-image.sh. It
# builds the four-partition installation image with genimage: a stable 128 MiB
# FAT loader partition, two equal 768 MiB root slots, and a seed persistent-data
# partition. radio-config.txt remains editable on the FAT partition.
#
# Buildroot exports BINARIES_DIR / BUILD_DIR to post-image scripts. genimage and
# the FAT tooling are available because the defconfig enables
# BR2_PACKAGE_HOST_GENIMAGE / BR2_PACKAGE_HOST_MTOOLS / BR2_PACKAGE_HOST_DOSFSTOOLS.
#
# The file list mirrors the stock RPi post-image for firmware/DTB files and adds
# U-Boot plus the fixed A/B boot script.
# =============================================================================
set -e

# This script's directory (in the external tree), to find our templates.
BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
GENIMAGE_IN="${BOARD_DIR}/genimage.cfg.in"
LAYOUT_CONF="${BOARD_DIR}/image-layout.conf"
RADIO_CONFIG="${BOARD_DIR}/radio-config.txt"
REPO_DIR="$(cd "${BOARD_DIR}/../../../.." && pwd)"

# shellcheck source=/dev/null
. "$LAYOUT_CONF"

GENIMAGE_CFG="${BINARIES_DIR}/genimage.cfg"
GENIMAGE_TMP="${BUILD_DIR}/genimage.tmp"
OUTPUT_DIR="${BASE_DIR:-$(dirname "${BINARIES_DIR}")}"

# Fail before image assembly if a selectable profile has no overlay or kernel
# driver in this exact Buildroot output.
python3 "${REPO_DIR}/scripts/verify-audio-catalog.py" "${OUTPUT_DIR}"

# --- Place the provisioning file on the FAT partition ------------------------
# Copy it into BINARIES_DIR so genimage picks it up as a boot file (relative to
# --inputpath BINARIES_DIR), landing it on boot.vfat next to config.txt.
if [ -f "$RADIO_CONFIG" ]; then
	cp -f "$RADIO_CONFIG" "${BINARIES_DIR}/radio-config.txt"
fi
# --- Build the boot-file list (mirrors stock RPi post-image) -----------------
FILES=()
for f in "${BINARIES_DIR}"/*.dtb "${BINARIES_DIR}"/rpi-firmware/*; do
	[ -e "$f" ] || continue
	FILES+=( "${f#"${BINARIES_DIR}"/}" )
done

for required in u-boot.bin boot.scr uboot-env.bin; do
	if [ ! -s "${BINARIES_DIR}/${required}" ]; then
		echo "post-image.sh: missing required A/B boot artifact: ${required}" >&2
		exit 1
	fi
done

# The ${scriptaddr}/${...} tokens below are U-Boot environment references that
# must stay literal in the seeded environment strings, so single quotes are
# intentional here (they are not shell expansions).
# shellcheck disable=SC2016
for required_environment in \
	'scriptaddr=0x05400000' \
	'kernel_addr_r=0x00080000' \
	'bootcmd=fatload mmc 0:1 ${scriptaddr} boot.scr; source ${scriptaddr}'; do
	if ! grep -Fqx "$required_environment" "${BOARD_DIR}/uboot-env.txt"; then
		echo "post-image.sh: seeded U-Boot environment missing: ${required_environment}" >&2
		exit 1
	fi
done

ENV_IMAGE="${BINARIES_DIR}/uboot-env.bin"
ENV_IMAGE_SIZE=$(wc -c < "$ENV_IMAGE" | tr -d '[:space:]')
ENV_IMAGE_FLAG=$(od -An -tu1 -j4 -N1 "$ENV_IMAGE" | tr -d '[:space:]')
if [ "$ENV_IMAGE_SIZE" != "$UBOOT_ENV_SIZE" ]; then
	echo "post-image.sh: redundant environment has size ${ENV_IMAGE_SIZE}, expected ${UBOOT_ENV_SIZE}" >&2
	exit 1
fi
if [ "$ENV_IMAGE_FLAG" != "1" ]; then
	echo "post-image.sh: redundant environment does not have an active flag" >&2
	exit 1
fi

# Validate the resolved configuration, not merely the input fragment. U-Boot's
# CONFIG_ENV_OFFSET_REDUND depends on CONFIG_ENV_REDUNDANT; without the latter,
# olddefconfig silently drops the offset while Linux and mkenvimage continue to
# use the redundant format. That makes U-Boot and fw_printenv select different
# environment data.
UBOOT_CONFIG=$(find "${BUILD_DIR}" -maxdepth 2 -type f \
	-path '*/uboot-*/.config' -print -quit)
if [ -z "$UBOOT_CONFIG" ]; then
	echo "post-image.sh: resolved U-Boot configuration not found" >&2
	exit 1
fi
for required_config in \
	'CONFIG_ENV_IS_IN_MMC=y' \
	'CONFIG_ENV_REDUNDANT=y' \
	'CONFIG_ENV_SIZE=0x10000' \
	'CONFIG_ENV_OFFSET=0x100000' \
	'CONFIG_ENV_OFFSET_REDUND=0x200000'; do
	if ! grep -qx "$required_config" "$UBOOT_CONFIG"; then
		echo "post-image.sh: U-Boot environment configuration missing: ${required_config}" >&2
		exit 1
	fi
done
FILES+=( "u-boot.bin" "boot.scr" )

# Ship the provisioning file on the FAT partition (if present).
[ -f "${BINARIES_DIR}/radio-config.txt" ] && FILES+=( "radio-config.txt" )

# --- Build deterministic A/B and persistent-data filesystems -----------------
make_ext4_image() {
	source_dir="$1"
	image="$2"
	size="$3"
	label="$4"
	uuid="$5"

	rm -f "$image"
	truncate -s "$size" "$image"
	"${HOST_DIR}/sbin/mkfs.ext4" -d "$source_dir" -L "$label" \
		-U "$uuid" -E root_owner=0:0 -F "$image"
}

make_rootfs_slot() {
	image="$1"
	label="$2"
	uuid="$3"
	cp -f "${BINARIES_DIR}/rootfs.ext4" "$image"
	truncate -s "$ROOTFS_SLOT_SIZE" "$image"
	check_status=0
	"${HOST_DIR}/sbin/e2fsck" -f -p "$image" || check_status=$?
	[ "$check_status" -le 1 ]
	"${HOST_DIR}/sbin/resize2fs" "$image"
	"${HOST_DIR}/sbin/tune2fs" -L "$label" -U "$uuid" "$image"
}

set_data_inode() {
	path="$1"
	field="$2"
	value="$3"
	"${HOST_DIR}/sbin/debugfs" -w -R "set_inode_field $path $field $value" "$DATA_IMAGE"
}

ROOTFS_A="${BINARIES_DIR}/rootfs-a.ext4"
ROOTFS_B="${BINARIES_DIR}/rootfs-b.ext4"
DATA_IMAGE="${BINARIES_DIR}/data.ext4"
DATA_ROOT="$(mktemp -d)"
trap 'rm -rf "${ROOTPATH_TMP:-}" "$DATA_ROOT"' EXIT

mkdir -p "$DATA_ROOT/radio/logos" "$DATA_ROOT/network"
mkdir -p "$DATA_ROOT/identity/dropbear" "$DATA_ROOT/bluetooth"
mkdir -p "$DATA_ROOT/operations"
mkdir -p "$DATA_ROOT/update/upload" "$DATA_ROOT/update/queue"
mkdir -p "$DATA_ROOT/update/config-backups"
mkdir -p "$DATA_ROOT/update/backup-restore"
printf '1\n' > "$DATA_ROOT/radio/schema-version"
printf 'wifi_ssid wifi_psk\n' > "$DATA_ROOT/radio/provisioning-status"
printf '[]\n' > "$DATA_ROOT/update/history.json"

# Seed mutable state from the completed rootfs so the first compatibility-link
# setup does not discard generic defaults or generated identity/config files.
if [ -d "${TARGET_DIR}/etc/radio" ]; then
	cp -a "${TARGET_DIR}/etc/radio/." "$DATA_ROOT/radio/"
fi
if [ -f "${TARGET_DIR}/etc/wpa_supplicant.conf" ]; then
	cp -p "${TARGET_DIR}/etc/wpa_supplicant.conf" \
		"$DATA_ROOT/network/wpa_supplicant.conf"
else
	: > "$DATA_ROOT/network/wpa_supplicant.conf"
fi
chmod 0600 "$DATA_ROOT/network/wpa_supplicant.conf"
if [ -d "${TARGET_DIR}/etc/dropbear" ]; then
	cp -a "${TARGET_DIR}/etc/dropbear/." "$DATA_ROOT/identity/dropbear/"
fi
if [ -d "${TARGET_DIR}/var/lib/bluetooth" ]; then
	cp -a "${TARGET_DIR}/var/lib/bluetooth/." "$DATA_ROOT/bluetooth/"
fi

make_rootfs_slot "$ROOTFS_A" "$ROOTFS_A_LABEL" "$ROOTFS_A_UUID"
make_rootfs_slot "$ROOTFS_B" "$ROOTFS_B_LABEL" "$ROOTFS_B_UUID"

make_ext4_image "$DATA_ROOT" "$DATA_IMAGE" "$DATA_SIZE" \
	"$DATA_LABEL" "$DATA_UUID"
for path in /radio /radio/logos /update/upload /update/backup-restore; do
	set_data_inode "$path" uid 601
	set_data_inode "$path" gid 601
done
set_data_inode /radio/schema-version uid 601
set_data_inode /radio/schema-version gid 601
set_data_inode /radio/schema-version mode 0100644
for path in /network /network/wpa_supplicant.conf /identity \
	/identity/dropbear /bluetooth /update /update/history.json \
	/update/queue /update/config-backups /operations; do
	set_data_inode "$path" uid 0
	set_data_inode "$path" gid 0
done
set_data_inode /update/upload mode 040750
set_data_inode /update/backup-restore mode 040770
set_data_inode /update/queue mode 040700
set_data_inode /update/config-backups mode 040700
set_data_inode /update/history.json mode 0100600

# --- Generate the genimage config from our template --------------------------
# Write the boot-file list to a temp file and splice it in with sed's "r"
# command. This avoids embedding literal newlines in a sed replacement, which
# GNU sed accepts but some seds (e.g. BSD) reject — keeping the script portable.
FILE_LIST_TMP="$(mktemp)"
printf '\t\t\t"%s",\n' "${FILES[@]}" > "$FILE_LIST_TMP"

sed -e "/#BOOT_FILES#/ {
r ${FILE_LIST_TMP}
d
}" \
    "$GENIMAGE_IN" > "$GENIMAGE_CFG"
rm -f "$FILE_LIST_TMP"

# --- Run genimage (same invocation as stock) ---------------------------------
ROOTPATH_TMP="$(mktemp -d)"
rm -rf "${GENIMAGE_TMP}"

genimage \
	--rootpath "${ROOTPATH_TMP}"   \
	--tmppath "${GENIMAGE_TMP}"    \
	--inputpath "${BINARIES_DIR}"  \
	--outputpath "${BINARIES_DIR}" \
	--config "${GENIMAGE_CFG}"

# Seed a canonical redundant pair after genimage writes the MBR: the primary is
# active (flag 1) and the byte-identical secondary payload is obsolete (flag 0).
# The redundancy flag is outside the CRC-covered environment data.
ENV_REDUND_IMAGE="${BINARIES_DIR}/uboot-env-redund.bin"
cp -f "$ENV_IMAGE" "$ENV_REDUND_IMAGE"
printf '\000' | dd of="$ENV_REDUND_IMAGE" bs=1 seek=4 conv=notrunc status=none
dd if="$ENV_IMAGE" of="${BINARIES_DIR}/sdcard.img" \
	bs=1 seek="$UBOOT_ENV_OFFSET" conv=notrunc status=none
dd if="$ENV_REDUND_IMAGE" of="${BINARIES_DIR}/sdcard.img" \
	bs=1 seek="$UBOOT_ENV_REDUND_OFFSET" conv=notrunc status=none

"${BOARD_DIR}/build-swu.sh"

echo "post-image.sh: built A/B sdcard.img and versioned SWUpdate artifact"
exit 0

