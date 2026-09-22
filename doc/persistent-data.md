# Persistent data partition

PiSonic keeps firmware and mutable device state separate.
Firmware lives in interchangeable root slots A and B (`p2` and `p3`), while the
final ext4 partition (`p4`) is mounted at `/data` and shared by both slots. A
normal `.swu` update replaces only the inactive firmware slot, so the settings,
credentials, identities and update records described here survive an update,
trial boot and automatic rollback.

This document is the canonical inventory of the persistent-data filesystem and
explains how the image build, early boot and runtime writers ensure that data
reaches `p4`.

## What belongs in `/data` and why

### PiSonic configuration

`/data/radio` is exposed to applications as the compatibility directory
`/etc/radio`. Files are created only when the corresponding feature is configured,
unless noted otherwise.

| Canonical path | Compatibility path | Why it persists |
| --- | --- | --- |
| `/data/radio/admin.secret` | `/etc/radio/admin.secret` | Salted PBKDF2 web-administrator password hash. Losing it would reset web access after a slot change. Mode `0600`. |
| `/data/radio/stations.ini` | `/etc/radio/stations.ini` | User-edited station presets. |
| `/data/radio/sources.ini` | `/etc/radio/sources.ini` | Enabled music sources. |
| `/data/radio/device.ini` | `/etc/radio/device.ini` | Device name, timezone, NTP source and SSH enablement. |
| `/data/radio/display.ini` | `/etc/radio/display.ini` | Display preferences. |
| `/data/radio/audio.ini` | `/etc/radio/audio.ini` | Maximum-volume setting. |
| `/data/radio/audio_hardware.ini` | `/etc/radio/audio_hardware.ini` | Selected sound-card profile. |
| `/data/radio/equalizer.ini` | `/etc/radio/equalizer.ini` | Parametric equalizer and preamp settings. |
| `/data/radio/adc.ini` | `/etc/radio/adc.ini` | ADS1115 volume, button and power calibration. |
| `/data/radio/usb_audio.ini` | `/etc/radio/usb_audio.ini` | Selected UAC1/UAC2 USB Audio mode. |
| `/data/radio/usb_audio_output.ini` | `/etc/radio/usb_audio_output.ini` | Output parameters generated from the selected sound-card profile and consumed by the USB Audio bridge. |
| `/data/radio/wifi-country` | `/etc/radio/wifi-country` | WiFi regulatory country applied by `S41wlan`. |
| `/data/radio/wlan-static.env` | `/etc/radio/wlan-static.env` | Optional static address, prefix, gateway and DNS handoff for `S41wlan`. |
| `/data/radio/logos/` | `/etc/radio/logos/` | Uploaded, normalized station logos that override shipped assets. |
| `/data/radio/*.ini.bak` | `/etc/radio/*.ini.bak` | One-generation backups made before managed settings are replaced. |
| `/data/radio/schema-version` | `/etc/radio/schema-version` | Persistent-data format last written. Seeded in a new image. |
| `/data/radio/schema-compatibility.json` | `/etc/radio/schema-compatibility.json` | Writer and minimum-reader schema contract used to decide whether update and rollback are safe. Created by persistent preparation. |

Ordinary managed files use mode `0644`; secrets use `0600`. The directory is
owned by the dedicated `radio-web` UID/GID 601 so the unprivileged web service
can update settings without gaining access to root-only state elsewhere in
`/data`.

### Network and device identity

| Canonical path | Compatibility/runtime use | Why it persists |
| --- | --- | --- |
| `/data/network/wpa_supplicant.conf` | `/etc/wpa_supplicant.conf` | WiFi SSID and passphrase. The new slot must reconnect without reprovisioning. Mode `0600`. |
| `/data/identity/root-password.hash` | Applied to root's entry in `/etc/shadow` during early boot | Preserves the root password without sharing the complete slot-owned shadow database. Mode `0600`. |
| `/data/radio/root-credential-provisioned` | Read by the unprivileged web service | Non-secret capability marker allowing the UI to offer SSH only after root has persisted an unlocked password hash. Mode `0644`; contains no credential data. |
| `/data/identity/dropbear/` | `/etc/dropbear` | Dropbear host keys, preserving PiSonic's SSH identity and avoiding host-key warnings after an update. |
| `/data/bluetooth/` | `/var/lib/bluetooth` | BlueZ adapter/device records and phone pairing state. |

The identity, network and Bluetooth trees are root-owned. The web process can
change network settings only through the privileged helper's fixed operation.

### Firmware update, migration and partition state

| Canonical path | Lifetime and purpose |
| --- | --- |
| `/data/update/upload/firmware.swu.part` | Incomplete upload. Removed on success or failure and never installable. |
| `/data/update/upload/firmware.swu.ready` | Completely received, web-owned package awaiting the installer's atomic claim. |
| `/data/update/upload/firmware.swu.json` | Bounded display metadata: safe filename, byte count, SHA-256 and upload time. |
| `/data/update/upload/current.json` | Capability-protected progress record used by the initiating browser to reconnect across automatic reboot. Only the token hash is stored. |
| `/data/update/queue/firmware.swu` | Root-owned package claimed for validation and installation; removed when the detached worker finishes. |
| `/data/update/history.json` | Root-owned, bounded history of the latest 20 installs, trial results and automatic rollbacks. Seeded as `[]`. |
| `/data/update/config-backups/schema-*` | Root-only snapshots of `radio`, `network` and `identity` made before a schema migration. At most three are retained, including the protected accepted generation. |
| `/data/update/data-resize/pending` | Marker used while expanding final partition `p4`; removed after the filesystem reaches its target size. |
| `/data/update/data-resize/mbr.backup` | Original MBR retained by the guarded one-time data-partition expansion. |

Upload files are bounded by the 768 MiB firmware limit and a free-space margin.
The queue, history, migration backups and resize state are root-owned; only
`/data/update/upload` is writable by UID/GID 601.

### Bounded operational recovery state

| Canonical path | Lifetime and purpose |
| --- | --- |
| `/data/operations/summary.json` | Root-written recovery summary capped at 16 KiB and 32 events: boot ID, firmware slot, clean/unclean reboot classification, radio process-exit/heartbeat restart counters, and the last allowlisted firmware-health result. Mode `0644`. |

This file is the deliberately small exception to volatile logging. It contains
no free-form log text, credentials, URLs, station/source metadata, network
identifiers, or hostnames. An `unclean` reboot means only that the normal
shutdown marker was absent; it cannot reliably distinguish power loss, watchdog
reset, kernel failure, and another hard reset. The operational tree is excluded
from web backup and restore, so restoring user configuration cannot replace
reliability evidence.

## How the data gets onto partition 4

Persistence is established at image-build time and checked again on every boot.

### 1. The image build creates and seeds `data.ext4`

`buildroot/external/board/radio/post-image.sh` creates a temporary directory tree
for the data filesystem. It creates the canonical directories (including the
root-owned `/data/operations`), seeds
`schema-version` and an empty `history.json`, and copies initial mutable state
from the completed root filesystem:

- `/etc/radio/.` to `/data/radio/`;
- `/etc/wpa_supplicant.conf` to `/data/network/wpa_supplicant.conf`;
- `/etc/dropbear/.` to `/data/identity/dropbear/`;
- `/var/lib/bluetooth/.` to `/data/bluetooth/`.

The script builds that tree as `data.ext4`, then uses `debugfs` to assign target
UID/GID values and permission bits. This is necessary because the build host's
user IDs are not the appliance's IDs. `genimage.cfg.in` places `data.ext4` in the
fixed final Linux partition `p4`; it is not merely a `/data` directory inside
either root slot.

### 2. BusyBox mounts the fixed partition before any consumer

`post-build.sh` writes this generated `fstab` entry:

```text
/dev/mmcblk0p4 /data ext4 defaults,noatime 0 2
```

BusyBox `init` runs `mount -a`, then invokes `radio-persistent-boot` before the
hostname and all `rcS` services. The coordinator checks `/proc/mounts` and fails
closed unless `/data` is a read-write ext4 filesystem supplied by SD-card
partition 4. This prevents an unmounted `/data` directory in a firmware slot
from silently receiving writes that would disappear on the next slot switch.

### 3. Early boot establishes compatibility paths

`radio-persistent-paths` creates missing directories/files, restores ownership
and permissions, and installs four compatibility symlinks:

```text
/etc/radio              -> /data/radio
/etc/wpa_supplicant.conf -> /data/network/wpa_supplicant.conf
/etc/dropbear           -> /data/identity/dropbear
/var/lib/bluetooth      -> /data/bluetooth
```

It removes an incorrect old compatibility path before creating the symlink, but
leaves an already-correct link untouched. The rest of the software can therefore
use conventional `/etc` and `/var/lib` paths while the canonical bytes live on
`p4`.

### 4. Migration, provisioning and rendering are ordered

Still before services start, `radio-persistent-boot` runs these steps in order:

1. establish persistent paths;
2. lock, back up and migrate the shared schema;
3. apply one-shot `pisonic-config.txt` settings into persistent canonical files;
4. regenerate firmware-owned runtime files from those persistent inputs.

The sequence stops on an error. Schema and compatibility metadata are published
only after a migration succeeds, and the root password hash is applied to the
current slot without replacing its complete `/etc/shadow`.

### 5. Runtime writers preserve the symlinks

Managed web settings use a temporary file in `/etc/radio` itself, `fsync`, exact
permission bits and `os.replace`. Because `/etc/radio` is a **directory symlink**,
both the temporary file and destination are actually inside `/data/radio`, and
the atomic rename cannot detach the directory link.

`/etc/wpa_supplicant.conf` is different: it is a **file symlink**. Renaming a new
file directly over that pathname would replace the symlink and leave the new
credentials in the active root slot. Both `provision-from-boot` and the
privileged network helper therefore resolve the final target first and atomically
replace `/data/network/wpa_supplicant.conf` instead.

Firmware upload and installation code does not use compatibility links. It uses
fixed `/data/update/...` paths, applies size/ownership/link-count checks, flushes
files and directories before atomic publication, and never accepts a package
path from HTTP.

## Why normal firmware updates leave `/data` alone

## Web backup and restore

The authenticated Maintenance page can export the user/device state in
`/data/radio`, `/data/network`, `/data/identity`, and `/data/bluetooth`. The
versioned archive includes a persistent-schema contract and a size plus SHA-256
digest for every file. It can therefore retain settings, WiFi, administrator and
root password hashes, SSH host identity, uploaded logos, and Bluetooth pairings
across a complete SD-card reflash.

All of `/data/update` is excluded, including staged firmware, installation
history, migration backups, backup/restore staging, and partition-resize state.
Restore accepts regular files and directories only, uses a fixed path allowlist,
checks compatibility and bounded expanded size before extraction, and keeps a
temporary rollback copy while the selected trees are replaced.

A `.swu` contains one root-filesystem payload with fixed selections for inactive
`p2` and `p3`. It contains no `data.ext4`, `p4` device, partition table, FAT
loader or provisioning file. The installer also checks the candidate's declared
persistent-schema reader/writer contract before writing. Consequently a normal
update, failed trial and automatic rollback all see the same `/data` filesystem.

A complete `sdcard.img` is different: flashing it rewrites the partition table
and all four partitions, including `p4`. Reflashing therefore destroys persistent
data unless it is backed up separately.

## What deliberately does not persist

| Path/state | Reason |
| --- | --- |
| `/run/firmware-update/*`, `/run/swupdate/*` | Current-boot locks, status, IPC sockets and installer log. Persistent result state is recorded separately. |
| `/tmp/firmware-health.log`, `/tmp/radio-web.log`, `/tmp/radio-status.json`, `/tmp/radio-adc.json` | Diagnostics and live status are volatile by design. `/tmp` is tmpfs. |
| `/var/log` | Symlink to `/tmp`; routine logging must not wear the SD card. |
| MPD cache/state/stickers | Disposable playback cache, not user configuration. |
| Live ALSA state | Hardware runtime state is regenerated, not copied between firmware slots. |
| `/run/radio/equalizer.rt` | Live equalizer parameter cache the LADSPA plugin re-reads. Regenerated from the persistent `equalizer.ini` at boot and on every Apply; tmpfs, so a reboot/update simply rebuilds it. |
| `/etc/mpd.conf`, `/etc/asound.conf` and module configuration | Firmware-owned outputs are rendered from the persistent sound profile and settings. The derived `usb_audio_output.ini` handoff is an explicit exception and is listed in the persistent inventory above. |
| `/etc/hostname`, `/etc/chrony.conf`, `/etc/localtime` | Slot-owned files regenerated during early boot from `/data/radio/device.ini`. |

Keeping caches, logs and generated implementation details out of the persistence
surface avoids carrying stale firmware-owned state into a newer or rolled-back
slot.

The sole operational exception is the bounded, privacy-safe
`/data/operations/summary.json` described above; it records recovery facts, not
routine service output.

## On-device verification

These read-only commands verify the mount and links:

```sh
grep ' /data ' /proc/mounts
readlink /etc/radio
readlink /etc/wpa_supplicant.conf
readlink /etc/dropbear
readlink /var/lib/bluetooth
find /data -maxdepth 3 \( -type f -o -type d \)
```

Expect `/dev/mmcblk0p4 /data ext4 rw,...` and the four targets shown above. Stop
before updating if `/data` is absent/read-only or a compatibility link points
elsewhere.