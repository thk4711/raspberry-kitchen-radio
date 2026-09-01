#!/bin/sh
# =============================================================================
# configure.sh — Raspberry Kitchen Radio appliance configuration
#
# Sets the per-device settings that someone building this appliance needs to
# change before building the Buildroot image:
#
#   * hostname        (also the AirPlay / Spotify device name)
#   * WiFi SSID + PSK (the Pi 3A+ has no Ethernet, so WiFi is required)
#   * root password   (for console / SSH login)
#   * DAC overlay     (which I2S DAC/amp board is fitted)
#
# It writes these settings to a git-ignored fragment, buildroot/local-device.conf,
# and (re)generates the git-ignored WiFi credentials file. It NEVER edits tracked
# Buildroot source, so two devices can be configured from one clean checkout and
# no secret ever lands in a tracked diff:
#   * buildroot/local-device.conf                        (git-ignored)
#       - RADIO_HOSTNAME / RADIO_ROOT_PASSWORD / RADIO_DAC
#   * buildroot/external/board/radio/rootfs-overlay/etc/wpa_supplicant.conf
#       - regenerated from wpa_supplicant.conf.example   (git-ignored)
#
# build.sh reads local-device.conf back at build time and feeds the values into
# the image: hostname + root password via a generated kconfig fragment merged
# onto the tracked defconfig, and the DAC overlay selection applied to the FAT
# boot config.txt after the tracked template is copied.
#
# The generated wpa_supplicant.conf deliberately contains ONLY a network={...}
# block with key_mgmt=WPA-PSK. The target wpa_supplicant is built without
# CONFIG_CTRL_IFACE, so ctrl_interface / update_config / country are rejected
# inside this file — both post-build.sh and the S41wlan init script fail if they
# are present. The WiFi regulatory domain is NOT set here: use the
# radio-config.txt "wifi_country=" key, which S41wlan applies via `iw reg set`.
#
# Run it from anywhere; paths are resolved relative to this script.
#
#   ./buildroot/configure.sh --show
#   ./buildroot/configure.sh --hostname kuechenradio --ssid MyNet --psk 'secret'
#   ./buildroot/configure.sh --dac hifiberry-dacplus
#   ./buildroot/configure.sh            # interactive (prompts for each value)
#
# The script is idempotent: running it again with the same values is a no-op.
# =============================================================================
set -eu

# --- Resolve paths -----------------------------------------------------------
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
EXTERNAL_DIR="${SCRIPT_DIR}/external"
DEFCONFIG="${EXTERNAL_DIR}/configs/radio_rpi3_defconfig"
POST_BUILD="${EXTERNAL_DIR}/board/radio/post-build.sh"
CONFIG_TXT="${EXTERNAL_DIR}/board/radio/config.txt"
OVERLAY_ETC="${EXTERNAL_DIR}/board/radio/rootfs-overlay/etc"
WPA_CONF="${OVERLAY_ETC}/wpa_supplicant.conf"
WPA_EXAMPLE="${OVERLAY_ETC}/wpa_supplicant.conf.example"

# Per-device settings are written to this git-ignored fragment (never to the
# tracked defconfig / post-build.sh / config.txt). build.sh reads it back and
# feeds the values into the image, so two devices can be configured from one
# clean checkout without ever touching tracked source.
LOCAL_CONF="${SCRIPT_DIR}/local-device.conf"

# Valid DAC overlays (must match the commented options in config.txt).
VALID_DACS="iqaudio-dacplus hifiberry-dacplus merus-amp"

# --- Options -----------------------------------------------------------------
opt_hostname=""
opt_ssid=""
opt_psk=""
opt_root_password=""
opt_dac=""
non_interactive=0
show_only=0

usage() {
	cat <<'EOF'
Usage: configure.sh [OPTIONS]

Configure the Raspberry Kitchen Radio Buildroot appliance before building.

Options:
  --hostname NAME        Set the device hostname (AirPlay/Spotify name too).
  --ssid SSID            WiFi network name.
  --psk PASSPHRASE       WiFi passphrase (8..63 chars for WPA-PSK).
  --root-password PASS   Root login password (console / SSH).
  --dac OVERLAY          DAC overlay: iqaudio-dacplus | hifiberry-dacplus | merus-amp
  --non-interactive      Do not prompt; only apply the values given via flags.
  --show                 Print the current configuration and exit.
  -h, --help             Show this help and exit.

With no flags (and without --non-interactive) the script prompts for each
value, offering the current value as the default. Any subset of flags may be
combined; omitted values are prompted for (or left unchanged in
--non-interactive mode).

Examples:
  configure.sh --show
  configure.sh --hostname kuechenradio --ssid MyNet --psk 'secret'
  configure.sh --dac hifiberry-dacplus
EOF
}

die() {
	echo "configure.sh: ERROR: $*" >&2
	exit 1
}

# --- Argument parsing --------------------------------------------------------
while [ $# -gt 0 ]; do
	case "$1" in
		--hostname)      opt_hostname="${2:?--hostname needs a value}"; shift 2 ;;
		--hostname=*)    opt_hostname="${1#*=}"; shift ;;
		--ssid)          opt_ssid="${2:?--ssid needs a value}"; shift 2 ;;
		--ssid=*)        opt_ssid="${1#*=}"; shift ;;
		--psk)           opt_psk="${2:?--psk needs a value}"; shift 2 ;;
		--psk=*)         opt_psk="${1#*=}"; shift ;;
		--root-password) opt_root_password="${2:?--root-password needs a value}"; shift 2 ;;
		--root-password=*) opt_root_password="${1#*=}"; shift ;;
		--dac)           opt_dac="${2:?--dac needs a value}"; shift 2 ;;
		--dac=*)         opt_dac="${1#*=}"; shift ;;
		--non-interactive) non_interactive=1; shift ;;
		--show)          show_only=1; shift ;;
		-h|--help)       usage; exit 0 ;;
		*)               die "unknown option: $1 (see --help)" ;;
	esac
done

# --- Sanity: required source files exist -------------------------------------
[ -f "$DEFCONFIG" ]   || die "defconfig not found: $DEFCONFIG"
[ -f "$POST_BUILD" ]  || die "post-build.sh not found: $POST_BUILD"
[ -f "$CONFIG_TXT" ]  || die "config.txt not found: $CONFIG_TXT"
[ -f "$WPA_EXAMPLE" ] || die "wpa_supplicant.conf.example not found: $WPA_EXAMPLE"

# --- Read current values -----------------------------------------------------
get_defconfig_value() {
	# $1 = key; prints the unquoted value or empty string.
	sed -n "s/^$1=\"\(.*\)\"\$/\1/p" "$DEFCONFIG" | head -n1
}

get_local_value() {
	# $1 = key; prints the value from the git-ignored fragment, or empty.
	[ -f "$LOCAL_CONF" ] || return 0
	sed -n "s/^$1=\(.*\)\$/\1/p" "$LOCAL_CONF" | head -n1
}

# Prefer a value already chosen for this device (local fragment); otherwise
# fall back to the tracked default shipped in the defconfig / config.txt.
current_hostname=$(get_local_value RADIO_HOSTNAME)
[ -n "$current_hostname" ] || current_hostname=$(get_defconfig_value BR2_TARGET_GENERIC_HOSTNAME)
current_root_password=$(get_local_value RADIO_ROOT_PASSWORD)
[ -n "$current_root_password" ] || current_root_password=$(get_defconfig_value BR2_TARGET_GENERIC_ROOT_PASSWD)

# Active DAC overlay: local choice, else the uncommented one in config.txt.
current_dac=$(get_local_value RADIO_DAC)
if [ -z "$current_dac" ]; then
	current_dac=$(sed -n 's/^dtoverlay=\([A-Za-z0-9_-]*\).*/\1/p' "$CONFIG_TXT" \
		| grep -E "^($(echo "$VALID_DACS" | tr ' ' '|'))$" | head -n1 || true)
fi

# Current WiFi SSID (from the git-ignored real config, if present).
current_ssid=""
current_wifi_state="not configured (wpa_supplicant.conf missing)"
if [ -f "$WPA_CONF" ]; then
	current_ssid=$(sed -n 's/^[[:space:]]*ssid="\(.*\)".*/\1/p' "$WPA_CONF" | head -n1)
	if echo "$current_ssid" | grep -q 'YOUR_WIFI_\|YOUR_SSID'; then
		current_wifi_state="placeholder (edit before building)"
	elif [ -n "$current_ssid" ]; then
		current_wifi_state="configured"
	else
		current_wifi_state="present but no ssid found"
	fi
fi

# --- --show ------------------------------------------------------------------
if [ "$show_only" -eq 1 ]; then
	echo "Current Raspberry Kitchen Radio appliance configuration:"
	echo "  hostname       : ${current_hostname:-<unset>}"
	if [ -n "$current_root_password" ]; then
		echo "  root password  : <set>"
	else
		echo "  root password  : <unset>"
	fi
	echo "  DAC overlay    : ${current_dac:-<unknown>}"
	echo "  WiFi SSID      : ${current_ssid:-<none>}"
	echo "  WiFi status    : ${current_wifi_state}"
	echo "  (PSK is not displayed.)"
	exit 0
fi


# --- Interactive prompting (fills the gaps not passed as flags) --------------
prompt_value() {
	# $1 = prompt text, $2 = current/default value. Echoes chosen value.
	_prompt="$1"; _default="$2"
	if [ "$non_interactive" -eq 1 ]; then
		printf '%s' "$_default"
		return 0
	fi
	if [ -n "$_default" ]; then
		printf '%s [%s]: ' "$_prompt" "$_default" >&2
	else
		printf '%s: ' "$_prompt" >&2
	fi
	IFS= read -r _reply || _reply=""
	if [ -z "$_reply" ]; then
		printf '%s' "$_default"
	else
		printf '%s' "$_reply"
	fi
}

prompt_secret() {
	# $1 = prompt text. Reads without echo. Empty keeps current (returns "").
	_prompt="$1"
	if [ "$non_interactive" -eq 1 ]; then
		printf ''
		return 0
	fi
	printf '%s (leave empty to keep current): ' "$_prompt" >&2
	stty -echo 2>/dev/null || true
	IFS= read -r _reply || _reply=""
	stty echo 2>/dev/null || true
	printf '\n' >&2
	printf '%s' "$_reply"
}

# Hostname
if [ -z "$opt_hostname" ]; then
	opt_hostname=$(prompt_value "Hostname" "$current_hostname")
fi
opt_hostname=${opt_hostname:-$current_hostname}

# Root password
if [ -z "$opt_root_password" ]; then
	opt_root_password=$(prompt_value "Root password" "$current_root_password")
fi
opt_root_password=${opt_root_password:-$current_root_password}

# DAC overlay
if [ -z "$opt_dac" ]; then
	opt_dac=$(prompt_value "DAC overlay ($VALID_DACS)" "$current_dac")
fi
opt_dac=${opt_dac:-$current_dac}

# WiFi SSID
if [ -z "$opt_ssid" ]; then
	opt_ssid=$(prompt_value "WiFi SSID" "$current_ssid")
fi
opt_ssid=${opt_ssid:-$current_ssid}

# WiFi PSK — only prompted interactively when not given as a flag. An empty
# reply keeps the existing PSK (if the config already exists).
psk_provided=1
if [ -z "$opt_psk" ]; then
	opt_psk=$(prompt_secret "WiFi PSK")
	[ -z "$opt_psk" ] && psk_provided=0
fi

# --- Validation --------------------------------------------------------------
[ -n "$opt_hostname" ] || die "hostname must not be empty"
echo "$opt_hostname" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9-]{0,62}$' \
	|| die "invalid hostname '$opt_hostname' (letters, digits, hyphen; max 63)"

[ -n "$opt_root_password" ] || die "root password must not be empty"

echo "$opt_dac" | grep -qE "^($(echo "$VALID_DACS" | tr ' ' '|'))$" \
	|| die "invalid DAC overlay '$opt_dac' (allowed: $VALID_DACS)"

# WiFi: decide whether we (re)write wpa_supplicant.conf. We must end up with a
# non-placeholder SSID and a PSK. If the PSK was not provided we can only keep
# an existing valid config.
wifi_write=1
if [ -z "$opt_ssid" ] || echo "$opt_ssid" | grep -q 'YOUR_WIFI_\|YOUR_SSID'; then
	die "a real WiFi SSID is required (got '${opt_ssid:-<empty>}')"
fi
if [ "$psk_provided" -eq 0 ]; then
	# Keep existing PSK only if a real config already exists.
	if [ -f "$WPA_CONF" ] && grep -q '^[[:space:]]*psk=' "$WPA_CONF" \
		&& ! grep -q 'YOUR_WIFI_\|YOUR_PASSWORD' "$WPA_CONF"; then
		wifi_write=0
		echo "configure.sh: keeping existing WiFi PSK for SSID '$opt_ssid'."
	else
		die "no WiFi PSK provided and no existing valid PSK to keep (use --psk)"
	fi
else
	# WPA-PSK passphrase length constraint.
	psk_len=$(printf '%s' "$opt_psk" | wc -c | tr -d ' ')
	if [ "$psk_len" -lt 8 ] || [ "$psk_len" -gt 63 ]; then
		die "WiFi PSK must be 8..63 characters (got $psk_len)"
	fi
fi


# --- Apply: write per-device settings to the git-ignored fragment ------------
# No tracked file is modified. build.sh reads these back at build time and
# feeds them into the image (hostname + root password via a kconfig fragment;
# DAC overlay via a config.txt selection).
umask 077
tmp_local="${LOCAL_CONF}.tmp.$$"
{
	echo "# Raspberry Kitchen Radio — per-device settings (generated by configure.sh)."
	echo "# Git-ignored. Do NOT commit. Consumed by build.sh."
	printf 'RADIO_HOSTNAME=%s\n' "$opt_hostname"
	printf 'RADIO_ROOT_PASSWORD=%s\n' "$opt_root_password"
	printf 'RADIO_DAC=%s\n' "$opt_dac"
} > "$tmp_local"
mv "$tmp_local" "$LOCAL_CONF"
chmod 0600 "$LOCAL_CONF"

# --- Apply: WiFi (wpa_supplicant.conf) ---------------------------------------
if [ "$wifi_write" -eq 1 ]; then
	tmp="${WPA_CONF}.tmp.$$"
	{
		echo "# Raspberry Kitchen Radio — WiFi credentials (generated by configure.sh)."
		echo "# Do NOT commit this file. Keep only a network={...} block: the target"
		echo "# wpa_supplicant is built without CONFIG_CTRL_IFACE and rejects global"
		echo "# fields such as ctrl_interface / update_config / country."
		echo "network={"
		printf '    ssid="%s"\n' "$opt_ssid"
		printf '    psk="%s"\n' "$opt_psk"
		echo "    key_mgmt=WPA-PSK"
		echo "}"
	} > "$tmp"
	mv "$tmp" "$WPA_CONF"
	chmod 0600 "$WPA_CONF"
fi

# --- Post-apply self-check (mirror post-build.sh / S41wlan rules) ------------
[ -f "$WPA_CONF" ] || die "wpa_supplicant.conf was not created: $WPA_CONF"
if grep -q 'YOUR_WIFI_\|YOUR_SSID\|YOUR_PASSWORD' "$WPA_CONF"; then
	die "wpa_supplicant.conf still contains placeholder credentials"
fi
if grep -q '^[[:space:]]*\(ctrl_interface\|update_config\|country\)=' "$WPA_CONF"; then
	die "wpa_supplicant.conf contains unsupported global fields"
fi
if ! grep -q '^[[:space:]]*key_mgmt=WPA-PSK[[:space:]]*$' "$WPA_CONF"; then
	die "wpa_supplicant.conf is missing key_mgmt=WPA-PSK"
fi

# --- Summary -----------------------------------------------------------------
echo "configure.sh: configuration written to $LOCAL_CONF (git-ignored)."
echo "  hostname       : $opt_hostname"
echo "  root password  : <set>"
echo "  DAC overlay    : $opt_dac"
echo "  WiFi SSID      : $opt_ssid"
if [ "$wifi_write" -eq 1 ]; then
	echo "  WiFi PSK       : <written to wpa_supplicant.conf>"
else
	echo "  WiFi PSK       : <kept existing>"
fi
echo
echo "Next: build the image with"
echo "  ./buildroot/build.sh"

