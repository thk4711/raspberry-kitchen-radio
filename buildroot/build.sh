#!/bin/sh
# =============================================================================
# build.sh — build (or rebuild) the Raspberry Kitchen Radio Buildroot image.
#
# Intended to run on an x64 (amd64) Debian/Ubuntu build host. It performs every
# step needed to turn a fresh clone of this repository into a flashable SD-card
# image:
#
#   1. Install the Buildroot host prerequisites via apt (idempotent).
#   2. Create the working directories and a shared download cache.
#   3. Clone a stock Buildroot checkout (pinned version) if not already present.
#   4. Validate the appliance configuration (WiFi credentials, etc.).
#   5. Apply the radio defconfig (make radio_rpi3_defconfig).
#   6. Compile everything (make -jN).
#   7. Report the resulting output/images/sdcard.img (path, size, SHA-256).
#
# The BR2_EXTERNAL tree and the radio-app source are this repository, located
# automatically relative to this script. You can override the build locations:
#
#   BUILDROOT_DIR     stock Buildroot checkout       (default: ~/embedded/buildroot)
#   BR2_DL_DIR        shared download cache          (default: ~/embedded/dl)
#   BUILDROOT_VERSION Buildroot git tag/branch       (default: 2026.05.2)
#   REPO_DIR          this repository                (default: auto-detected)
#
# Usage:
#   ./buildroot/build.sh                 # normal (incremental) build
#   ./buildroot/build.sh --configure     # run configure.sh first, then build
#   ./buildroot/build.sh --clean         # make clean, then rebuild
#   ./buildroot/build.sh --dirclean      # wipe output/, then full rebuild
#   ./buildroot/build.sh --jobs 8        # override parallelism
#   ./buildroot/build.sh --no-apt        # skip the apt host-package step
#   ./buildroot/build.sh --help
# =============================================================================
set -eu

# --- Resolve paths -----------------------------------------------------------
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR="${REPO_DIR:-$(CDPATH='' cd -- "${SCRIPT_DIR}/.." && pwd)}"
EXTERNAL_DIR="${SCRIPT_DIR}/external"

BUILDROOT_DIR="${BUILDROOT_DIR:-$HOME/embedded/buildroot}"
BR2_DL_DIR="${BR2_DL_DIR:-$HOME/embedded/dl}"
BUILDROOT_VERSION="${BUILDROOT_VERSION:-2026.05.2}"
BUILDROOT_GIT_URL="${BUILDROOT_GIT_URL:-https://gitlab.com/buildroot.org/buildroot.git}"
DEFCONFIG_NAME="radio_rpi3_defconfig"
# Per-device settings written by configure.sh (git-ignored). Optional: a build
# with none present simply uses the tracked defconfig / board defaults.
LOCAL_CONF="${SCRIPT_DIR}/local-device.conf"

# --- Options -----------------------------------------------------------------
do_configure=0
do_clean=0
do_dirclean=0
do_apt=1
jobs=""

die() {
	echo "build.sh: ERROR: $*" >&2
	exit 1
}

usage() {
	sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
}

# --- Argument parsing --------------------------------------------------------
while [ $# -gt 0 ]; do
	case "$1" in
		--configure) do_configure=1; shift ;;
		--clean)     do_clean=1; shift ;;
		--dirclean)  do_dirclean=1; shift ;;
		--rebuild)   do_clean=1; shift ;;
		--no-apt)    do_apt=0; shift ;;
		--jobs)      jobs="${2:?--jobs needs a value}"; shift 2 ;;
		--jobs=*)    jobs="${1#*=}"; shift ;;
		-h|--help)   usage; exit 0 ;;
		*)           die "unknown option: $1 (see --help)" ;;
	esac
done

[ -n "$jobs" ] || jobs="$(nproc 2>/dev/null || echo 4)"

# --- 1. Host prerequisites (apt) ---------------------------------------------
install_host_packages() {
	if [ "$do_apt" -eq 0 ]; then
		echo "build.sh: skipping apt host-package step (--no-apt)."
		return 0
	fi
	if ! command -v apt-get >/dev/null 2>&1; then
		echo "build.sh: apt-get not found — assuming host packages are present."
		echo "build.sh: (this build flow expects a Debian/Ubuntu host)."
		return 0
	fi

	# Packages required by Buildroot's host prerequisites plus what our kernel /
	# firmware / go builds need. See buildroot/README.md and the Buildroot manual.
	pkgs="build-essential bc bison flex libncurses-dev libssl-dev \
		rsync cpio unzip wget file git python3 gawk perl \
		mtools dosfstools genext2fs"

	sudo_cmd=""
	if [ "$(id -u)" -ne 0 ]; then
		if command -v sudo >/dev/null 2>&1; then
			sudo_cmd="sudo"
		else
			echo "build.sh: not root and no sudo; skipping apt. Install manually:" >&2
			echo "  $pkgs" >&2
			return 0
		fi
	fi

	echo "build.sh: installing host packages via apt (needs sudo)..."
	$sudo_cmd apt-get update
	# shellcheck disable=SC2086 # word-splitting the package list is intended.
	$sudo_cmd apt-get install -y $pkgs
}

# --- 2/3. Working dirs + stock Buildroot checkout ----------------------------
prepare_buildroot() {
	mkdir -p "$BR2_DL_DIR"
	mkdir -p "$(dirname -- "$BUILDROOT_DIR")"

	if [ -d "$BUILDROOT_DIR/.git" ] || [ -f "$BUILDROOT_DIR/Makefile" ]; then
		echo "build.sh: using existing Buildroot at $BUILDROOT_DIR"
		return 0
	fi

	command -v git >/dev/null 2>&1 || die "git is required to clone Buildroot"
	echo "build.sh: cloning Buildroot $BUILDROOT_VERSION into $BUILDROOT_DIR ..."
	git clone --depth 1 --branch "$BUILDROOT_VERSION" \
		"$BUILDROOT_GIT_URL" "$BUILDROOT_DIR" \
		|| die "failed to clone Buildroot $BUILDROOT_VERSION"
}


# --- 4. Validate appliance configuration -------------------------------------
validate_config() {
	wpa_conf="${EXTERNAL_DIR}/board/radio/rootfs-overlay/etc/wpa_supplicant.conf"

	if [ "$do_configure" -eq 1 ]; then
		echo "build.sh: running configure.sh (--configure) ..."
		"${SCRIPT_DIR}/configure.sh"
	fi

	if [ ! -f "$wpa_conf" ] || grep -q 'YOUR_WIFI_\|YOUR_SSID\|YOUR_PASSWORD' "$wpa_conf" 2>/dev/null; then
		echo "build.sh: WiFi credentials are missing or still placeholders." >&2
		echo "build.sh: run the configuration script first, e.g.:" >&2
		echo "    ${SCRIPT_DIR}/configure.sh --hostname kuechenradio \\" >&2
		echo "        --ssid MyNetwork --psk 'my-wifi-password'" >&2
		die "appliance not configured (no valid wpa_supplicant.conf)"
	fi
	echo "build.sh: appliance configuration looks valid."
}

# --- Per-device settings (git-ignored local-device.conf) ---------------------
# Reads a RADIO_* key from the fragment; prints empty if absent.
local_value() {
	[ -f "$LOCAL_CONF" ] || return 0
	sed -n "s/^$1=\(.*\)\$/\1/p" "$LOCAL_CONF" | head -n1
}

# Merge the device hostname / root password onto the applied .config via
# Buildroot's kconfig merge helper. Nothing is written if the fragment or the
# individual keys are absent (falls back to the tracked defconfig defaults).
apply_local_kconfig() {
	[ -f "$LOCAL_CONF" ] || return 0
	_hostname=$(local_value RADIO_HOSTNAME)
	_root_pw=$(local_value RADIO_ROOT_PASSWORD)
	[ -n "$_hostname" ] || [ -n "$_root_pw" ] || return 0

	_frag="${BUILDROOT_DIR}/output/build/.radio-local-kconfig"
	mkdir -p "$(dirname "$_frag")"
	: > "$_frag"
	[ -n "$_hostname" ] && printf 'BR2_TARGET_GENERIC_HOSTNAME="%s"\n' "$_hostname" >> "$_frag"
	[ -n "$_root_pw" ]  && printf 'BR2_TARGET_GENERIC_ROOT_PASSWD="%s"\n' "$_root_pw" >> "$_frag"

	_merge="${BUILDROOT_DIR}/support/kconfig/merge_config.sh"
	if [ -x "$_merge" ]; then
		echo "build.sh: merging per-device kconfig overrides ..."
		"$_merge" -m -O "$BUILDROOT_DIR" "${BUILDROOT_DIR}/.config" "$_frag" >/dev/null
		make BR2_EXTERNAL="$BR2_EXTERNAL_ABS" olddefconfig >/dev/null
	else
		die "kconfig merge helper not found: $_merge"
	fi
}

# --- 5/6. Configure + compile ------------------------------------------------
build_image() {
	BR2_EXTERNAL_ABS=$(CDPATH='' cd -- "$EXTERNAL_DIR" && pwd)
	export BR2_DL_DIR
	# Export the device hostname / DAC so the tracked post-build.sh and
	# post-image.sh scripts can apply them without any tracked file being
	# edited. Empty values leave the shipped defaults in place.
	RADIO_HOSTNAME=$(local_value RADIO_HOSTNAME); export RADIO_HOSTNAME
	RADIO_DAC=$(local_value RADIO_DAC); export RADIO_DAC
	log_file="${BUILDROOT_DIR}/radio-build.log"

	cd "$BUILDROOT_DIR"

	if [ "$do_dirclean" -eq 1 ]; then
		echo "build.sh: make BR2_EXTERNAL=$BR2_EXTERNAL_ABS distclean output dir ..."
		make BR2_EXTERNAL="$BR2_EXTERNAL_ABS" distclean || rm -rf output
	fi

	echo "build.sh: applying defconfig ($DEFCONFIG_NAME) ..."
	make BR2_EXTERNAL="$BR2_EXTERNAL_ABS" "$DEFCONFIG_NAME"

	# Merge per-device kconfig overrides (hostname, root password) from the
	# git-ignored fragment onto the tracked defconfig-derived .config, so no
	# device-specific value ever lives in a tracked file.
	apply_local_kconfig

	if [ "$do_clean" -eq 1 ]; then
		echo "build.sh: make clean ..."
		make clean
	fi

	echo "build.sh: building with -j$jobs (log: $log_file) ..."
	echo "build.sh: BR2_DL_DIR=$BR2_DL_DIR"
	# Tee to a log so a long build can be inspected/copied afterwards.
	if command -v tee >/dev/null 2>&1; then
		make -j"$jobs" 2>&1 | tee "$log_file"
	else
		make -j"$jobs" > "$log_file" 2>&1
	fi
}

# --- 7. Report ---------------------------------------------------------------
report_image() {
	img="${BUILDROOT_DIR}/output/images/sdcard.img"
	if [ ! -f "$img" ]; then
		die "build finished but $img was not produced"
	fi

	echo
	echo "=========================================================="
	echo " Build complete."
	echo " Image : $img"
	if command -v du >/dev/null 2>&1; then
		echo " Size  : $(du -h "$img" | cut -f1)"
	fi
	if command -v sha256sum >/dev/null 2>&1; then
		echo " SHA256: $(sha256sum "$img" | cut -d' ' -f1)"
	elif command -v shasum >/dev/null 2>&1; then
		echo " SHA256: $(shasum -a 256 "$img" | cut -d' ' -f1)"
	fi
	echo "=========================================================="
	echo
	echo "Flash it to an SD card:"
	echo "  Linux : lsblk        then  sudo dd if=\"$img\" of=/dev/sdX bs=4M oflag=direct conv=fsync"
	echo "  macOS : diskutil list then sudo dd if=\"$img\" of=/dev/rdiskN bs=4m"
}

# --- Main --------------------------------------------------------------------
echo "build.sh: repo         = $REPO_DIR"
echo "build.sh: BR2_EXTERNAL = $EXTERNAL_DIR"
echo "build.sh: buildroot    = $BUILDROOT_DIR ($BUILDROOT_VERSION)"
echo "build.sh: dl cache     = $BR2_DL_DIR"
echo

install_host_packages
prepare_buildroot
validate_config
build_image
report_image

