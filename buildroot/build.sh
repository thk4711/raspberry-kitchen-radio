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
#   3. Clone or validate the exact, clean pinned Buildroot revision.
#   4. Apply the radio defconfig (make radio_rpi3_defconfig).
#   5. Compile everything (make -jN).
#   6. Report sdcard.img and the versioned .swu (paths, sizes, SHA-256).
#
# The BR2_EXTERNAL tree and the radio-app source are this repository, located
# automatically relative to this script. You can override the build locations:
#
#   BUILDROOT_DIR     stock Buildroot checkout       (default: ~/embedded/buildroot)
#   BR2_DL_DIR        shared download cache          (default: ~/embedded/dl)
#   BUILDROOT_VERSION Buildroot git tag/branch       (default: 2026.05.2)
#   BUILDROOT_COMMIT  expected full commit ID         (pinned with version)
#   REPO_DIR          this repository                (default: auto-detected)
#
# Usage:
#   ./buildroot/build.sh                 # normal (incremental) build
#   ./buildroot/build.sh --clean         # make clean, then rebuild
#   ./buildroot/build.sh --dirclean      # wipe output/, then full rebuild
#   ./buildroot/build.sh --jobs 8        # override parallelism
#   ./buildroot/build.sh --no-apt        # skip the apt host-package step
#   ./buildroot/build.sh --allow-unverified-buildroot # development only
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
BUILDROOT_COMMIT="${BUILDROOT_COMMIT:-72d9d4fa636a371ef9eb99c92a735ce9f6d829d5}"
BUILDROOT_GIT_URL="${BUILDROOT_GIT_URL:-https://gitlab.com/buildroot.org/buildroot.git}"
DEFCONFIG_NAME="radio_rpi3_defconfig"
BUILD_MARKER="${BUILDROOT_DIR}/output/radio-build.started"
# --- Options -----------------------------------------------------------------
do_clean=0
do_dirclean=0
do_apt=1
jobs=""
allow_unverified_buildroot=0

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
		--clean)     do_clean=1; shift ;;
		--dirclean)  do_dirclean=1; shift ;;
		--rebuild)   do_clean=1; shift ;;
		--no-apt)    do_apt=0; shift ;;
		--allow-unverified-buildroot) allow_unverified_buildroot=1; shift ;;
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
	pkgs="build-essential bc bison flex libncurses-dev libssl-dev swupdate \
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

	if [ -e "$BUILDROOT_DIR" ]; then
		echo "build.sh: using existing Buildroot at $BUILDROOT_DIR"
	else
		command -v git >/dev/null 2>&1 || die "git is required to clone Buildroot"
		echo "build.sh: cloning Buildroot $BUILDROOT_VERSION into $BUILDROOT_DIR ..."
		git clone --depth 1 --branch "$BUILDROOT_VERSION" \
			"$BUILDROOT_GIT_URL" "$BUILDROOT_DIR" \
			|| die "failed to clone Buildroot $BUILDROOT_VERSION"
	fi

	validator_args=""
	if [ "$allow_unverified_buildroot" -eq 1 ]; then
		validator_args="--allow-unverified"
	fi
	BUILDROOT_ACTUAL_COMMIT=$("${SCRIPT_DIR}/validate-checkout.sh" \
		"$BUILDROOT_DIR" "$BUILDROOT_VERSION" "$BUILDROOT_COMMIT" $validator_args)
	export BUILDROOT_ACTUAL_COMMIT
}

repository_commit() {
	if command -v git >/dev/null 2>&1 && git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		git -C "$REPO_DIR" rev-parse --verify HEAD
	else
		printf '%s\n' unknown
	fi
}


# --- 4/5. Configure + compile ------------------------------------------------
build_image() {
	BR2_EXTERNAL_ABS=$(CDPATH='' cd -- "$EXTERNAL_DIR" && pwd)
	export BR2_DL_DIR
	log_file="${BUILDROOT_DIR}/output/radio-build.log"

	cd "$BUILDROOT_DIR"

	if [ "$do_dirclean" -eq 1 ]; then
		echo "build.sh: make BR2_EXTERNAL=$BR2_EXTERNAL_ABS distclean output dir ..."
		make BR2_EXTERNAL="$BR2_EXTERNAL_ABS" distclean || rm -rf output
	fi

	echo "build.sh: applying defconfig ($DEFCONFIG_NAME) ..."
	make BR2_EXTERNAL="$BR2_EXTERNAL_ABS" "$DEFCONFIG_NAME"

	if [ "$do_clean" -eq 1 ]; then
		echo "build.sh: make clean ..."
		make clean
	fi
	mkdir -p "${BUILDROOT_DIR}/output"
	rm -f "$BUILD_MARKER"
	touch "$BUILD_MARKER"

	RADIO_REPO_COMMIT=$(repository_commit)
	RADIO_BUILDROOT_COMMIT=$BUILDROOT_ACTUAL_COMMIT
	export RADIO_REPO_COMMIT RADIO_BUILDROOT_COMMIT
	{
		echo "build.sh: repository commit = $RADIO_REPO_COMMIT"
		echo "build.sh: Buildroot commit  = $RADIO_BUILDROOT_COMMIT"
	} | tee "$log_file"

	echo "build.sh: building with -j$jobs (log: $log_file) ..."
	echo "build.sh: BR2_DL_DIR=$BR2_DL_DIR"
	# Tee to a log so a long build can be inspected/copied afterwards.
	if command -v tee >/dev/null 2>&1; then
		make -j"$jobs" 2>&1 | tee -a "$log_file"
	else
		make -j"$jobs" >> "$log_file" 2>&1
	fi
}

# --- 6. Report ---------------------------------------------------------------
firmware_version() {
	version_file="${REPO_DIR}/lib/_version.py"
	version=$(sed -n 's/^__version__ = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"$/\1/p' "$version_file")
	[ -n "$version" ] || die "cannot parse X.Y.Z firmware version from $version_file"
	[ "$(printf '%s\n' "$version" | wc -l)" -eq 1 ] || die "multiple firmware versions in $version_file"
	printf '%s\n' "$version"
}

artifact_sha256() {
	path="$1"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$path" | cut -d' ' -f1
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$path" | cut -d' ' -f1
	else
		die "sha256sum or shasum is required to report build artifacts"
	fi
}

report_artifact() {
	label="$1"
	path="$2"
	echo " $label : $path"
	echo " Size    : $(du -h "$path" | cut -f1)"
	echo " SHA256  : $(artifact_sha256 "$path")"
}

report_images() {
	images_dir=$(CDPATH='' cd -- "${BUILDROOT_DIR}/output/images" && pwd) \
		|| die "Buildroot images directory is missing"
	img="${images_dir}/sdcard.img"
	version=$(firmware_version)
	swu="${images_dir}/kitchen-radio-${version}.swu"
	"${SCRIPT_DIR}/validate-artifacts.sh" "$images_dir" "$version" "$BUILD_MARKER" \
		|| die "build artifacts failed validation"

	echo
	echo "=========================================================="
	echo " Build complete."
	report_artifact "Install" "$img"
	report_artifact "Update " "$swu"
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
echo "build.sh: expected     = $BUILDROOT_COMMIT"
echo "build.sh: dl cache     = $BR2_DL_DIR"
echo

install_host_packages
prepare_buildroot
build_image
report_images

