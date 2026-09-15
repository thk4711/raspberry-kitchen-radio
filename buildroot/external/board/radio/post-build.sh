#!/bin/sh
# Buildroot post-build hook for the radio image.
# $1 = TARGET_DIR
set -e
TARGET_DIR="$1"
BOARD_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${BOARD_DIR}/../../../.." && pwd)"

# Embed the immutable release identity consumed by the root-only inactive-slot
# inspector. The version remains sourced only from lib/_version.py.
python3 "${REPO_DIR}/scripts/generate_release_metadata.py" \
	"${REPO_DIR}/lib/_version.py" "${TARGET_DIR}/etc/radio-release.json"

# --- Ensure an 'mpd' user/group exists (mpd.conf runs mpd as user mpd) -------
if ! grep -q '^mpd:' "${TARGET_DIR}/etc/passwd"; then
	echo 'mpd:x:600:600:Music Player Daemon:/var/lib/mpd:/sbin/nologin' >> "${TARGET_DIR}/etc/passwd"
fi
if ! grep -q '^mpd:' "${TARGET_DIR}/etc/group"; then
	echo 'mpd:x:600:' >> "${TARGET_DIR}/etc/group"
fi
# Add mpd to the audio group so it can open the ALSA device. Keep this
# idempotent: post-build can be run repeatedly against the same target tree, and
# the previous append-only sed turned "audio:x:29:mpd" into
# "audio:x:29:mpdmpd...", which is not valid group membership.
add_user_to_group() {
	group_name="$1"
	group_gid="$2"
	user_name="$3"
	group_file="${TARGET_DIR}/etc/group"
	tmp_file="${group_file}.tmp"

	if grep -q "^${group_name}:" "${group_file}"; then
		awk -F: -v OFS=: -v group_name="${group_name}" -v user_name="${user_name}" '
			function append_member(member) {
				if (member == "") {
					return
				}
				members = (members == "") ? member : members "," member
			}
			$1 == group_name {
				members = ""
				found = 0
				n = split($4, existing_members, ",")
				for (i = 1; i <= n; i++) {
					member = existing_members[i]
					# Also repair the old malformed "mpdmpd..." value.
					if (member == user_name || member ~ ("^(" user_name ")+$")) {
						if (!found) {
							append_member(user_name)
						}
						found = 1
						continue
					}
					append_member(member)
				}
				if (!found) {
					append_member(user_name)
				}
				$4 = members
			}
			{ print }
		' "${group_file}" > "${tmp_file}"
		mv "${tmp_file}" "${group_file}"
	else
		echo "${group_name}:x:${group_gid}:${user_name}" >> "${group_file}"
	fi
}
add_user_to_group audio 29 mpd

# --- Ensure an unprivileged 'radio-web' user/group exists --------------------
# The web administration interface (S80radio-web) runs as this dedicated,
# unprivileged user; the root-owned privileged helper
# (S79radio-helper) performs the few operations that need root. The helper's
# unix socket is group-owned by radio-web so only this user's group can talk to
# it. nologin shell: this account never gets an interactive login.
if ! grep -q '^radio-web:' "${TARGET_DIR}/etc/passwd"; then
	echo 'radio-web:x:601:601:Radio web admin:/var/lib/radio-web:/sbin/nologin' >> "${TARGET_DIR}/etc/passwd"
fi
if ! grep -q '^radio-web:' "${TARGET_DIR}/etc/group"; then
	echo 'radio-web:x:601:' >> "${TARGET_DIR}/etc/group"
fi

# --- Managed config directory (web-editable *.ini) ---------------------------
# The web UI writes /etc/radio/*.ini. Create the paths
# here, but assign their target ownership in board/radio/device_table.txt. This
# post-build script runs as the non-root build user, so chown here would either
# fail or record host ownership rather than target ownership.
mkdir -p "${TARGET_DIR}/etc/radio"
chmod 0755 "${TARGET_DIR}/etc/radio"
mkdir -p "${TARGET_DIR}/etc/radio/logos"
chmod 0755 "${TARGET_DIR}/etc/radio/logos"
mkdir -p "${TARGET_DIR}/var/lib/radio-web"
chmod 0755 "${TARGET_DIR}/var/lib/radio-web"


# --- Writable runtime dirs (in case rootfs is read-only) ---------------------
mkdir -p "${TARGET_DIR}/var/lib/mpd/playlists" \
         "${TARGET_DIR}/var/lib/mpd/music" \
         "${TARGET_DIR}/var/lib"

# Keep immutable firmware templates separate from their generated runtime files.
mkdir -p "${TARGET_DIR}/usr/share/radio/templates"
cp "${TARGET_DIR}/etc/mpd.conf" "${TARGET_DIR}/usr/share/radio/templates/mpd.conf"

# --- fstab: persistent data plus tmpfs scratch space --------------------------
mkdir -p "${TARGET_DIR}/data"
# This appliance has a fixed SD-card map. Use p4 directly instead of relying on
# optional BusyBox LABEL= resolution, and replace stale entries on incremental
# builds so /data is listed exactly once.
awk '$2 != "/data" { print }' "${TARGET_DIR}/etc/fstab" > "${TARGET_DIR}/etc/fstab.tmp"
mv "${TARGET_DIR}/etc/fstab.tmp" "${TARGET_DIR}/etc/fstab"
echo '/dev/mmcblk0p4 /data ext4 defaults,noatime 0 2' >> "${TARGET_DIR}/etc/fstab"
if ! grep -q 'tmpfs.*/tmp' "${TARGET_DIR}/etc/fstab"; then
	echo 'tmpfs /tmp tmpfs mode=1777,nosuid,nodev 0 0' >> "${TARGET_DIR}/etc/fstab"
fi

# --- No persistent logs: redirect /var/log to the RAM-backed tmpfs -----------
# Appliance logging policy: nothing should ever write persistent logs to the SD
# card. All runtime logs are sent to /dev/null (MPD, WiFi, media backends) or
# tmpfs (chrony, provisioning). As a safety net, make /var/log a symlink to
# /tmp (a tmpfs, mounted above) so any component that ignores that policy and
# writes to /var/log lands in RAM and vanishes on reboot.
if [ ! -L "${TARGET_DIR}/var/log" ]; then
	rm -rf "${TARGET_DIR}/var/log"
	ln -sf /tmp "${TARGET_DIR}/var/log"
fi

# --- Generic first-boot WiFi state -------------------------------------------
# A clean image intentionally contains no network credentials. The early
# provision-from-boot step creates this file from radio-config.txt before
# S41wlan starts. Keep an empty, root-only file so all runtime paths have a
# stable target even when provisioning is skipped or invalid.
WPA_CONF="${TARGET_DIR}/etc/wpa_supplicant.conf"
: > "${WPA_CONF}"
chmod 0600 "${WPA_CONF}"

# --- Service layout -----------------------------------------------------------
# Keep BusyBox init, but start only one owner for each media backend:
#   * S50mpd (our script) starts MPD once.
#   * S90radio starts the Python app; the app itself launches nqptp,
#     shairport-sync and go-librespot using RADIO_*_BINARY env overrides.
# Disable upstream duplicate/competing scripts so services are not started
# twice. Do not leave them as /etc/init.d/S??*.disabled: Buildroot's BusyBox
# rcS runs every /etc/init.d/S??* regular file, regardless of extension or mode.
# Move them outside that glob instead. The chmods make the overlay robust even if
# host-side mode bits are lost.
mkdir -p "${TARGET_DIR}/etc/init.d/disabled"
for script in S80swupdate S90nqptp S99shairport-sync S95mpd; do
	for candidate in \
		"${TARGET_DIR}/etc/init.d/${script}" \
		"${TARGET_DIR}/etc/init.d/${script}.disabled"; do
		if [ -f "${candidate}" ]; then
			destination="${TARGET_DIR}/etc/init.d/disabled/$(basename "${candidate}")"
			rm -f "${destination}"
			mv "${candidate}" "${destination}"
		fi
	done
done
# Clean up any stale disabled S?? files left by older images/builds; these would
# still be executed by rcS if they stayed directly under /etc/init.d.
for candidate in "${TARGET_DIR}"/etc/init.d/S??*.disabled; do
	[ -f "${candidate}" ] || continue
	destination="${TARGET_DIR}/etc/init.d/disabled/$(basename "${candidate}")"
	rm -f "${destination}"
	mv "${candidate}" "${destination}"
done
for script in S12data-resize S13zram S14watchdog S15operational-summary S39usb-audio S41wlan S42bluetooth S50mpd S79radio-helper S80radio-web S90radio S99firmware-health S50dropbear; do
	if [ -f "${TARGET_DIR}/etc/init.d/${script}" ]; then
		chmod 0755 "${TARGET_DIR}/etc/init.d/${script}"
	fi
done
# USB Audio helpers are run by S39usb-audio or used interactively.
for helper in \
	"${TARGET_DIR}/usr/sbin/radio-usb-audio-gadget" \
	"${TARGET_DIR}/usr/sbin/radio-usb-audio-bridge" \
	"${TARGET_DIR}/usr/bin/radio-usb-audio-stream-playing"; do
	[ ! -f "$helper" ] || chmod 0755 "$helper"
done
# The SD-card provisioning helper runs from inittab sysinit (before rcS).
if [ -f "${TARGET_DIR}/usr/sbin/provision-from-boot" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/provision-from-boot"
fi
if [ -f "${TARGET_DIR}/usr/sbin/radio-persistent-paths" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/radio-persistent-paths"
fi
if [ -f "${TARGET_DIR}/usr/sbin/radio-persistent-boot" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/radio-persistent-boot"
fi
if [ -f "${TARGET_DIR}/usr/sbin/radio-persistent-config" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/radio-persistent-config"
fi
if [ -f "${TARGET_DIR}/usr/sbin/radio-swupdate-inactive" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/radio-swupdate-inactive"
fi
if [ -f "${TARGET_DIR}/usr/sbin/radio-firmware-health" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/radio-firmware-health"
fi
# The early boot-splash launcher also runs from inittab sysinit (detached, so
# it never blocks boot). Ensure it is executable even if host-side mode bits
# were lost when the overlay was checked out.
if [ -f "${TARGET_DIR}/usr/sbin/radio-boot-splash" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/radio-boot-splash"
fi
# Mountpoint used by provision-from-boot for the briefly writable FAT partition.
mkdir -p "${TARGET_DIR}/mnt/boot"
# Separate mountpoint the privileged web helper uses to briefly mount the FAT
# boot partition READ-WRITE when changing the selected sound card (it edits only
# the dtparam=audio=/dtoverlay= lines in config.txt, then unmounts). Kept
# distinct from /mnt/boot so a runtime edit never collides with boot provisioning.
# See radio_web/audio_hardware_apply.py.
mkdir -p "${TARGET_DIR}/mnt/boot-rw"
# Fixed read-only mountpoint used only by the privileged firmware slot inspector.
mkdir -p "${TARGET_DIR}/mnt/firmware-other-ro"
if [ -f "${TARGET_DIR}/etc/udhcpc-wlan.script" ]; then
	chmod 0755 "${TARGET_DIR}/etc/udhcpc-wlan.script"
fi
if [ -f "${TARGET_DIR}/var/lib/wlan-last-lease.env" ]; then
	chmod 0600 "${TARGET_DIR}/var/lib/wlan-last-lease.env"
fi

exit 0
