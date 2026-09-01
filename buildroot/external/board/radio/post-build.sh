#!/bin/sh
# Buildroot post-build hook for the radio image.
# $1 = TARGET_DIR
set -e
TARGET_DIR="$1"

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

# --- Writable runtime dirs (in case rootfs is read-only) ---------------------
mkdir -p "${TARGET_DIR}/var/lib/mpd/playlists" \
         "${TARGET_DIR}/var/lib/mpd/music" \
         "${TARGET_DIR}/var/lib"

# --- fstab: tmpfs for scratch the app writes to /tmp -------------------------
if ! grep -q 'tmpfs.*/tmp' "${TARGET_DIR}/etc/fstab"; then
	echo 'tmpfs /tmp tmpfs mode=1777,nosuid,nodev 0 0' >> "${TARGET_DIR}/etc/fstab"
fi

# --- No persistent logs: redirect /var/log to the RAM-backed tmpfs -----------
# Appliance logging policy: nothing should ever write persistent logs to the SD
# card. All runtime logs are sent to /dev/null (MPD, WiFi, media backends) or
# tmpfs (chrony, provisioning). As a safety net, make /var/log a symlink to
# /tmp (a tmpfs, mounted above) so any component that ignores that policy and
# writes to /var/log lands in RAM and vanishes on reboot, never filling the SD
# card. Buildroot may ship /var/log as a real directory, so remove it first.
# (No syslogd/klogd is enabled in the defconfig, so /var/log/messages is not
# produced today; this only guards against future/third-party writers.)
if [ ! -L "${TARGET_DIR}/var/log" ]; then
	rm -rf "${TARGET_DIR}/var/log"
	ln -sf /tmp "${TARGET_DIR}/var/log"
fi

# --- Set hostname (used as the AirPlay/Spotify device name) ------------------
# The per-device hostname is supplied by build.sh via RADIO_HOSTNAME (from the
# git-ignored local-device.conf). Fall back to the tracked default so a build
# from a clean checkout still produces a working image.
echo "${RADIO_HOSTNAME:-kuechenradio}" > "${TARGET_DIR}/etc/hostname"

# --- WiFi sanity check --------------------------------------------------------
# The Pi 3A+ has no Ethernet. Fail the image build if the git-ignored real
# credentials were not created before building; otherwise the target boots fine
# but can never associate with an AP.
WPA_CONF="${TARGET_DIR}/etc/wpa_supplicant.conf"
if [ ! -f "${WPA_CONF}" ]; then
	echo "ERROR: missing /etc/wpa_supplicant.conf in target rootfs." >&2
	echo "Copy rootfs-overlay/etc/wpa_supplicant.conf.example to wpa_supplicant.conf" >&2
	echo "and set real WiFi credentials, or run buildroot/configure.sh." >&2
	exit 1
fi
if grep -q 'YOUR_WIFI_\|YOUR_SSID\|YOUR_PASSWORD' "${WPA_CONF}"; then
	echo "ERROR: /etc/wpa_supplicant.conf still contains placeholder WiFi credentials." >&2
	echo "Set real SSID/PSK before building, or run buildroot/configure.sh." >&2
	exit 1
fi
if grep -q '^[[:space:]]*\(ctrl_interface\|update_config\|country\)=' "${WPA_CONF}"; then
	echo "ERROR: /etc/wpa_supplicant.conf contains unsupported global fields." >&2
	echo "The target wpa_supplicant is built without CONFIG_CTRL_IFACE and rejects" >&2
	echo "ctrl_interface/update_config/country. Keep only the network={...} block." >&2
	echo "(Set the WiFi region via radio-config.txt 'wifi_country=', not here.)" >&2
	exit 1
fi
if ! grep -q '^[[:space:]]*key_mgmt=WPA-PSK[[:space:]]*$' "${WPA_CONF}"; then
	echo "ERROR: /etc/wpa_supplicant.conf is missing key_mgmt=WPA-PSK." >&2
	echo "The target wpa_supplicant rejects the config without this line." >&2
	echo "Regenerate the file with buildroot/configure.sh or add key_mgmt=WPA-PSK." >&2
	exit 1
fi

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
for script in S90nqptp S99shairport-sync S95mpd; do
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
for script in S13zram S14watchdog S41wlan S42bluetooth S50mpd S90radio S50dropbear; do
	if [ -f "${TARGET_DIR}/etc/init.d/${script}" ]; then
		chmod 0755 "${TARGET_DIR}/etc/init.d/${script}"
	fi
done
# The SD-card provisioning helper runs from inittab sysinit (before rcS).
if [ -f "${TARGET_DIR}/usr/sbin/provision-from-boot" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/provision-from-boot"
fi
# The early boot-splash launcher also runs from inittab sysinit (detached, so
# it never blocks boot). Ensure it is executable even if host-side mode bits
# were lost when the overlay was checked out.
if [ -f "${TARGET_DIR}/usr/sbin/radio-boot-splash" ]; then
	chmod 0755 "${TARGET_DIR}/usr/sbin/radio-boot-splash"
fi
# Mountpoint used by provision-from-boot for the read-only FAT boot partition.
mkdir -p "${TARGET_DIR}/mnt/boot"
if [ -f "${TARGET_DIR}/etc/udhcpc-wlan.script" ]; then
	chmod 0755 "${TARGET_DIR}/etc/udhcpc-wlan.script"
fi
if [ -f "${TARGET_DIR}/var/lib/wlan-last-lease.env" ]; then
	chmod 0600 "${TARGET_DIR}/var/lib/wlan-last-lease.env"
fi

exit 0
