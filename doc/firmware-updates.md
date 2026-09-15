# Firmware updates and recovery

This is the operator guide for updating a Raspberry Kitchen Radio, activating a
new A/B firmware slot, switching back to retained firmware, and recovering when
the web interface cannot be reached. It applies to the four-partition Raspberry
Pi 3A+ appliance image; it does not convert an older two-partition card.

Developers implementing or porting this design should also read:

- [Firmware-update architecture](firmware-update-architecture.md) — Buildroot,
  image layout, redundant U-Boot environment, SWUpdate package construction,
  Linux tooling, health confirmation, fallback, and a porting checklist.
- [Buildroot reference](buildroot.md) — the complete appliance image and service
  configuration outside the update subsystem.

## What an update changes

The SD card has a stable FAT loader, two ext4 firmware slots, and shared data:

| Region | Role during an update |
| --- | --- |
| Raw area before `p1` | Holds two redundant U-Boot environment copies. |
| `p1` | Stable Raspberry Pi firmware, U-Boot, DTBs, and `boot.scr`; not updated by `.swu`. |
| `p2` | Firmware slot A, including A's `/boot/zImage`. |
| `p3` | Firmware slot B, including B's `/boot/zImage`. |
| `p4` | Persistent `/data`; not replaced by `.swu`. |

An update always writes the inactive root slot. If A is running, it writes B;
if B is running, it writes A. The running root filesystem, partition table,
loader, and `/data` remain untouched. After installation, U-Boot starts the new
slot as a trial. Linux accepts it only after local health checks pass.

For a complete inventory of settings, credentials, identities and update state
stored on p4—and how the build and early boot guarantee that writes reach that
partition—see [Persistent data](persistent-data.md). Flashing a complete
`sdcard.img`, unlike installing a `.swu`, replaces p4 and destroys that state.

## Security and safety before updating

- Use a versioned `kitchen-radio-<version>.swu` obtained through a trusted
  channel. Packages are **not cryptographically signed**.
- The SHA-256 detects accidental corruption but does not authenticate the
  publisher. An attacker able to replace both package and checksum can replace
  the firmware.
- The administration interface uses **plain HTTP**. Upload firmware and enter
  the administrator password only on a trusted LAN. Never expose port 8080 to
  the Internet.
- Keep stable power while the inactive slot is written. Do not manually reboot
  or remove the card while installation is active.
- Keep serial-console or previously enabled SSH access available for the first
  qualification of a new build.
- Use the `.swu` from a build containing all intended fixes. Updating a slot
  installs the userspace health code contained in that package.

## Pre-update checks

These read-only commands establish the current accepted state:

```sh
cat /proc/cmdline
fw_printenv active_slot previous_slot upgrade_available bootcount bootlimit rollback_from
grep ' /data ' /proc/mounts
cat /data/update/history.json
```

For accepted A, expect `root=/dev/mmcblk0p2`, `radio.slot=A`,
`active_slot=A`, `upgrade_available=0`, and `bootcount=0`. For accepted B,
replace A with B and `p2` with `p3`. `/data` must be a read-write ext4 mount from
`/dev/mmcblk0p4`. Stop if Linux's root/slot and U-Boot's `active_slot` disagree,
or if another trial is pending.

## Install an update

The build emits `kitchen-radio-<version>.swu`. A local build helper may copy it to the configured output directory with a timestamp. Use the `.swu` from the same
build as its matching `sdcard.img`, and compare its SHA-256 with the build output
when transfer integrity matters.

1. Open `http://<hostname>.local:8080`, log in, and select **Maintenance**.
2. In the Firmware box, choose **Update firmware**.
3. Follow the overlay: enter the administrator password, select the `.swu`,
   review its filename, size and inactive target slot, then confirm the update.
4. Leave the appliance powered while upload, validation, writing, syncing and
   reboot progress are shown. Closing the browser does not stop the detached
   installer.
5. The radio reboots automatically. Keep the overlay open; it reconnects when
   the web service returns and waits for the new trial firmware to pass its local
   health checks. A refreshed Maintenance page resumes an update started in the
   same browser tab.

The appliance rejects replacement uploads, shutdown, reboot, and conflicting
boot changes while an installation is active.

## Verify installation before reboot

The running kernel still identifies the old slot, while the environment selects
the new trial. Check:

```sh
cat /proc/cmdline
fw_printenv active_slot previous_slot upgrade_available bootcount bootlimit rollback_from
cat /run/firmware-update/status.json
cat /run/firmware-update/swupdate.log
cat /data/update/history.json
```

After A installs B, the expected environment is:

```text
active_slot=B
previous_slot=A
upgrade_available=1
bootcount=0
bootlimit=3
rollback_from=none
```

After B installs A, swap A and B. Status must report `state":"success"`, the log
must report SWUpdate success, and history must contain an `installed` entry.

## Trial boot and successful activation

The guided update reboots automatically after installation. On a serial console,
a correct boot starts with:

```text
Loading Environment from MMC... Reading from redundant MMC(0)... OK
```

U-Boot increments and saves `bootcount`, maps A to `p2` or B to `p3`, loads that
slot's own `/boot/zImage`, and constructs matching kernel arguments. Verify:

```sh
cat /proc/cmdline
fw_printenv active_slot previous_slot upgrade_available bootcount rollback_from
```

For a B trial, Linux must show:

```text
root=/dev/mmcblk0p3 ... radio.slot=B boot_source=uboot
```

For A, it must show `p2` and `radio.slot=A`.

The background health evaluator has up to 120 seconds to confirm slot/root
agreement, writable persistent data, completed schema migration, the local web
health endpoint, the radio heartbeat, and MPD when Internet Radio is enabled.
Internet access, NTP, WiFi connectivity, and a working external stream are not
acceptance requirements.

After acceptance, verify:

```sh
fw_printenv active_slot previous_slot upgrade_available bootcount rollback_from
cat /tmp/firmware-health.log
cat /data/update/history.json
```

Expected values are `upgrade_available=0`, `bootcount=0`, and
`rollback_from=none`, while `active_slot` remains the newly booted slot. History
must contain `accepted`. Reboot once more and confirm the same slot remains
selected.

Do not manually start `S99firmware-health`; it is an init-owned service.

## Automatic rollback

If health checks do not pass, the evaluator records `trial_failed`, leaves the
trial environment intact, syncs storage, and reboots. Each U-Boot trial boot
increments and saves `bootcount`. With the shipped `bootlimit=3`, attempts 1, 2,
and 3 boot the trial; when the next boot increments the counter beyond the
limit, U-Boot selects `previous_slot`, clears the trial state, resets the
counter, and records the failed slot in `rollback_from`.

On the recovered Linux boot, the health service reconciles this marker into an
`automatic_rollback` history entry and resets `rollback_from=none`. Persistent
settings, credentials, identities, pairing data, logos, and calibration remain
on `/data` throughout.

Allow fallback to finish without interrupting its retries. Investigate repeated
trial boots before attempting another installation.

## Manually switch or roll back

Maintenance inspects the retained slot through a fixed read-only mount:

- **Roll back to `<version>`** means the retained compatible firmware is older.
- **Switch to other firmware** means it is equal or newer.

Review the slot and version, follow the confirmation page, and enter the current
administrator password. The helper verifies compatibility and environment
state, writes a complete trial transaction, reads it back, and reboots. The
selected slot must pass the same health process; failure returns to the starting
slot. Firmware-owned generated files are recreated from persistent inputs rather
than copied between slots.

## Interrupted upload or installation

- A partial upload cannot become the fixed ready package and cannot be installed.
- Closing the browser does not stop a detached installation worker.
- Keep power applied and revisit Firmware if status is uncertain.
- Runtime status and logs disappear at reboot; persistent history does not.
- Do not assume arbitrary power-loss safety without destructive tests on the
  actual SD card and target board.

## Recover when the web interface is unreachable

Use the least destructive option first:

1. Allow automatic fallback to finish.
2. Find the appliance in the router's client list and try its hostname and IP.
3. Use previously enabled SSH or a prepared 115200 8N1 serial console.
4. Power down, remove the card, and edit `radio-config.txt` on FAT `p1` to repair
   WiFi or hostname. To enable recovery SSH, set a unique `root_password` and
   `enable_ssh=1` together; safely eject and boot.
5. Use low-level U-Boot recovery only after preserving evidence and identifying
   the failure.
6. Reflash a trusted `sdcard.img` only as a last resort. Reflashing replaces the
   partition table and destroys `/data`; preserve recoverable data first.

HDMI exposes `tty1`, but this Pi 3A+ uses its USB-A connector as a USB Audio
peripheral and cannot accept a USB keyboard in that mode.

## Quick diagnostic snapshot

Collect these read-only results before changing state:

```sh
cat /proc/cmdline
fw_printenv active_slot previous_slot upgrade_available bootcount bootlimit rollback_from
fw_printenv scriptaddr kernel_addr_r bootcmd
cat /etc/fw_env.config
grep ' /data ' /proc/mounts
cat /etc/radio-release.json
cat /run/firmware-update/status.json
cat /run/firmware-update/swupdate.log
cat /tmp/firmware-health.log
cat /data/update/history.json
```

Some runtime files legitimately do not exist when no installation or trial has
run since boot.