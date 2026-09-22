# Firmware-update architecture

This document describes the complete A/B firmware-update design used by the
PiSonic. It is intended as a reference for developers building a
similar Raspberry Pi appliance with Buildroot, U-Boot, SWUpdate, and a shared
persistent-data partition.

For operating the finished appliance, see [Firmware updates and
recovery](firmware-updates.md).

## Goals and trust model

The design aims to provide:

- inactive-slot installation: never overwrite the running root filesystem;
- deterministic A/B mapping and no user-supplied block-device paths;
- a kernel and root filesystem from the same firmware build;
- boot-attempt accounting before Linux starts;
- automatic fallback when a trial cannot prove local health;
- redundant, power-loss-tolerant boot environment storage;
- persistent application configuration independent of either firmware slot;
- bounded validation and progress reporting around SWUpdate.

It does **not** currently authenticate firmware cryptographically. SHA-256
protects integrity, not provenance. Production systems with a stronger threat
model should enable signed SWUpdate images, protect signing keys offline, define
key rotation and revocation, and consider verified boot for the stable loader.

The implementation assumes one SD card appears as `/dev/mmcblk0`. A port must
replace this assumption with a reliable board-specific device identity rather
than accepting a path from a web request or package.

## Storage and ownership model

The fixed MBR layout is defined by `board/radio/image-layout.conf` and
`genimage.cfg.in`:

| Region | Start/size | Owner and purpose |
| --- | --- | --- |
| Raw reservation | bytes 0 through 8 MiB | MBR and bootloader state, outside all partitions. |
| Primary environment | offset `0x100000`, size `0x10000` | U-Boot/libubootenv redundant copy. |
| Secondary environment | offset `0x200000`, size `0x10000` | U-Boot/libubootenv redundant copy. |
| `p1` FAT | offset 8 MiB, 128 MiB | Raspberry Pi firmware, DTBs, `u-boot.bin`, `boot.scr`, provisioning file. |
| `p2` ext4 | 768 MiB | Slot A root filesystem and `/boot/zImage`. |
| `p3` ext4 | 768 MiB | Slot B root filesystem and `/boot/zImage`. |
| `p4` ext4 | 128 MiB seed | Shared `/data`, expanded to the card's end at first boot. |

The stable FAT loader is not part of a normal `.swu`. Updating it independently
would couple recoverability to the operation being recovered from. Loader
updates require a separate, carefully qualified design.

Each root slot contains its own kernel. U-Boot loads `/boot/zImage` from the same
partition it passes as `root=`. This avoids combining a new root filesystem with
an old shared kernel or vice versa.

Mutable state belongs in `p4`, not in A or B. This project persists radio
configuration, WiFi, administrator credentials, Dropbear identity, Bluetooth
pairing, update history, logos, and calibration under `/data`. Firmware-owned
generated files are recreated in each slot from persistent inputs.

## Buildroot configuration

The relevant target selections in `radio_rpi3_defconfig` are:

```text
BR2_TARGET_UBOOT=y
BR2_TARGET_UBOOT_BUILD_SYSTEM_KCONFIG=y
BR2_TARGET_UBOOT_USE_DEFCONFIG=y
BR2_TARGET_UBOOT_BOARD_DEFCONFIG="rpi_3_32b"
BR2_TARGET_UBOOT_CONFIG_FRAGMENT_FILES="$(BR2_EXTERNAL_RADIO_PATH)/board/radio/uboot.fragment"
BR2_TARGET_UBOOT_FORMAT_BIN=y
BR2_PACKAGE_HOST_UBOOT_TOOLS=y
BR2_PACKAGE_HOST_UBOOT_TOOLS_ENVIMAGE=y
BR2_PACKAGE_HOST_UBOOT_TOOLS_ENVIMAGE_SIZE="0x10000"
BR2_PACKAGE_HOST_UBOOT_TOOLS_ENVIMAGE_REDUNDANT=y
BR2_PACKAGE_HOST_UBOOT_TOOLS_BOOT_SCRIPT=y
BR2_TARGET_ROOTFS_EXT2=y
BR2_TARGET_ROOTFS_EXT2_4=y
BR2_PACKAGE_HOST_GENIMAGE=y
BR2_PACKAGE_LIBUBOOTENV=y
BR2_PACKAGE_SWUPDATE=y
```

`host-uboot-tools` converts `boot.cmd` to `boot.scr` and `uboot-env.txt` to a
redundant-format `uboot-env.bin`. The host genimage, DOS filesystem, and mtools
packages assemble the complete card image. The target includes libubootenv so
Linux and SWUpdate can read and write the same environment as U-Boot.

SWUpdate needs libconfig for `sw-description`, zlib for the gzip payload, and
OpenSSL for SHA-256 verification. Its embedded web server, website, downloader,
Suricatta, script handlers, partition handlers, archive handlers, signing, and
encryption are disabled. The authenticated appliance application is the only web
entry point, and SWUpdate receives only a fixed root-owned package path.

## U-Boot configuration and environment consistency

`uboot.fragment` enables:

```text
CONFIG_ENV_SIZE=0x10000
CONFIG_ENV_IS_IN_MMC=y
CONFIG_ENV_OFFSET=0x100000
CONFIG_ENV_REDUNDANT=y
CONFIG_ENV_OFFSET_REDUND=0x200000
CONFIG_CMD_EXT4=y
CONFIG_CMD_SETEXPR=y
CONFIG_CMD_SOURCE=y
CONFIG_CMD_SAVEENV=y
```

`CONFIG_ENV_REDUNDANT=y` is essential. In U-Boot 2026.04,
`CONFIG_ENV_OFFSET_REDUND` depends on it; specifying only the second offset lets
Kconfig silently remove that offset. Linux can then interpret both copies as a
redundant environment while U-Boot interprets only the first as non-redundant.
The resulting tools select different state even though all reads appear valid.

Three definitions must remain identical:

1. resolved U-Boot `.config`;
2. generated `uboot-env.bin` format and size;
3. target `/etc/fw_env.config` offsets and size.

`post-image.sh` therefore checks the **resolved** `.config`, not just the input
fragment, validates the environment binary's 65536-byte size and active flag,
and fails before publishing an image on disagreement.

The generated image initially has a canonical pair: primary flag 1 (active),
secondary flag 0 (obsolete), with identical CRC-covered data. U-Boot and
libubootenv subsequently alternate copies and update redundancy flags when
saving. The flags are sequence/status metadata; do not compare them with a
hard-coded assumption after the system has saved its environment.

## Environment variables

`uboot-env.txt` seeds:

| Variable | Meaning |
| --- | --- |
| `active_slot` | A or B selected for the next boot. |
| `previous_slot` | Last accepted slot retained for fallback. |
| `upgrade_available` | `1` means `active_slot` is an unaccepted trial. |
| `bootcount` | Attempts consumed by the current trial. |
| `bootlimit` | Trial attempts allowed before fallback; shipped as `3`. |
| `rollback_from` | One-shot identity of a failed slot after fallback, else `none`. |
| `scriptaddr` | RAM destination for `boot.scr`, `0x05400000`. |
| `kernel_addr_r` | RAM destination for `zImage`, `0x00080000`. |
| `bootcmd` | Loads `boot.scr` from FAT `p1` and executes it. |
| `bootdelay` | Autoboot countdown in seconds; shipped as `0` so the appliance boots straight into the kernel with no delay before SD-card activity. |

Persistent environments replace compiled defaults rather than layering only the
changed values over them. Addresses referenced by `bootcmd` or `boot.scr` must
therefore be present in the seed. If `scriptaddr` is absent, `fatload` receives
an empty address and `source` may execute stale memory.

### Boot delay and U-Boot debugging

`bootdelay=0` disables U-Boot's "Hit any key to stop autoboot" countdown, which
otherwise defaults to the compiled-in `2` seconds and delays every boot before
the kernel is loaded from the SD card. The trade-off is that **the U-Boot
console cannot be interrupted at boot** while it is `0`.

To regain an interrupt window for U-Boot debugging:

- Temporarily on a running device: `fw_setenv bootdelay 3` then reboot; restore
  with `fw_setenv bootdelay 0`. This writes the redundant MMC environment that
  U-Boot actually reads and needs no rebuild.
- Permanently in a rebuilt image: change `bootdelay=0` to e.g. `bootdelay=3` in
  `buildroot/external/board/radio/uboot-env.txt` and rebuild. Keep this in sync
  with the `'bootdelay=0'` entry in `board/radio/post-image.sh`'s
  `required_environment` list and the `bootdelay=0` assertion in
  `tests/test_buildroot_swupdate.py` (adjust or relax those checks when
  intentionally shipping a non-zero delay).

## Normal U-Boot boot flow

The Raspberry Pi firmware reads `config.txt` from FAT `p1` and starts
`u-boot.bin`. U-Boot then:

1. loads the valid redundant environment from raw MMC;
2. runs `bootcmd`;
3. loads `boot.scr` from FAT `p1` at `${scriptaddr}`;
4. validates slot and counter variables, using safe defaults for malformed data;
5. performs trial accounting when `upgrade_available=1`;
6. maps A to `rootpart=2`, or B to `rootpart=3`;
7. clears inherited `bootargs` and constructs an authoritative command line;
8. loads `/boot/zImage` from the selected ext4 partition at `${kernel_addr_r}`;
9. boots it with the Raspberry Pi firmware-provided device tree.

The generated command line includes:

```text
root=/dev/mmcblk0pN rootwait ... radio.slot=A|B boot_source=uboot
```

The neutral FAT `cmdline.txt` must not contain a fixed root or slot that can
override this policy. Linux tooling trusts `/proc/cmdline` only when it contains
exactly one matching `root=` and `radio.slot=` pair.

If loading or booting the kernel returns during a trial, `boot.scr` immediately
resets. The next U-Boot run consumes another attempt. For an accepted slot, it
prints an error and stops rather than inventing an untracked fallback.

## Upgrade state machine

For an accepted A-to-B update, the key states are:

| Stage | Running Linux | `active_slot` | `previous_slot` | `upgrade_available` | `bootcount` |
| --- | --- | --- | --- | ---: | ---: |
| Before install | A / `p2` | A | B | 0 | 0 |
| Installed, before reboot | A / `p2` | B | A | 1 | 0 |
| First trial boot | B / `p3` | B | A | 1 | 1 |
| Accepted | B / `p3` | B | A | 0 | 0 |

B-to-A is symmetric.

SWUpdate writes the raw rootfs first and commits the manifest's `bootenv` values
only as part of a successful selected installation. The selection contains the
target-specific `active_slot`/`previous_slot` pair and common trial values. A
failed payload write must not intentionally select a partially written slot.

## SWUpdate target configuration

`swupdate.config` enables only the required path:

- hardware compatibility from `/etc/hwrevision`;
- U-Boot environment updates through `/etc/fw_env.config`;
- SHA-256 verification via OpenSSL;
- gzip decompression;
- libconfig manifest parsing;
- raw block image and bootloader-environment handlers;
- control and progress sockets under `/run/swupdate`.

The target does not run an SWUpdate daemon or web server. The application starts
SWUpdate locally for one fixed package and one derived software selection.

On the target, hardware compatibility is verified against the device's real
`/etc/hwrevision`. The build host has no such file, so the host-side self-check
(`swupdate -c`) passes the revision explicitly with `-H radio:<revision>` (the
revision comes from `HARDWARE_REVISION` in `scripts/build_firmware_swu.py`, the
same value written into the manifest and release metadata). The board token is
only logged; SWUpdate matches solely the revision against the manifest's
`hardware-compatibility` list, so the check remains meaningful — a mismatched
revision still fails — while never depending on host state.

## `.swu` format and build

`post-image.sh` invokes `build-swu.sh` after the root filesystem is complete.
`scripts/build_firmware_swu.py` creates a deterministic SVR4 `070702` CRC CPIO:

```text
sw-description
rootfs.ext4.gz
TRAILER!!!
```

Member order is significant: SWUpdate requires `sw-description` first. The root
filesystem is gzip-compressed at level 9 with an empty original filename and
`mtime=0`. CPIO metadata is deterministic. One payload is referenced by both
`stable,slot-a` and `stable,slot-b`; the selected manifest branch determines its
fixed destination:

```text
slot-a -> /dev/mmcblk0p2
slot-b -> /dev/mmcblk0p3
```

The manifest records:

- semantic firmware version;
- hardware revision `1`;
- persistent schema, reader schema, and rollback-reader contract;
- compressed member size and SHA-256;
- `type="raw"`, `compressed="zlib"`, and `installed-directly=true`;
- target-specific trial environment values.

The builder rejects a rootfs larger than a slot, unresolved placeholders,
unexpected members, bad order, size/hash mismatches, invalid targets, and schema
contract errors. It runs a native host SWUpdate checker in check mode for both
selections before atomically publishing `pisonic-<version>.swu`.

`validate-artifacts.sh` then requires a fresh nonempty `sdcard.img`, exactly one
matching fresh `.swu`, and no stale versioned package. `build.sh` reports size
and SHA-256 for both artifacts.

Example host inspection, without installing:

```sh
cpio -itv < pisonic-<version>.swu
swupdate -c -i pisonic-<version>.swu -e stable,slot-a
swupdate -c -i pisonic-<version>.swu -e stable,slot-b
sha256sum pisonic-<version>.swu
```

Use a native checker matching the target SWUpdate grammar; the ARM target binary
cannot run on an amd64 build host. The build selects the checker in this order:
`SWUPDATE_CHECKER`, then the staged `swupdate-checker-build`/`swupdate-native`
trees beside the Buildroot checkout, then `swupdate` on `PATH`. A candidate is
only accepted if it actually runs — a binary whose shared libraries (for example
`libubootenv.so.0`, from the `libubootenv0.1` package) cannot be resolved exits
127 and is skipped. `build.sh` preflights this before the long build and fails
fast with an actionable message when no runnable checker is found.

## Linux installation tooling

The runtime components deliberately separate unprivileged upload, privileged
validation, SWUpdate execution, and boot health:

| Component | Responsibility |
| --- | --- |
| `radio_web.firmware_installer` | Claims upload, validates archive and compatibility, derives inactive slot, manages lock/status/history, starts SWUpdate. |
| `/usr/sbin/radio-swupdate-inactive` | Re-derives A/B from `/proc/cmdline` and invokes the fixed opposite SWUpdate selection. |
| `/usr/bin/swupdate` | Checks the package, streams gzip payload to the raw inactive partition, commits boot environment. |
| `/usr/bin/radio-swupdate-progress` | Bridges SWUpdate progress IPC to bounded machine-readable lines. |
| `radio_web.firmware_health` | Checks trial health, accepts it, or records failure and reboots. |
| `S99firmware-health` | Reconciles previous results and starts health evaluation in the background only for a trial. |
| `radio_web.firmware_slots` | Inspects the other slot read-only and prepares a verified manual trial. |
| `fw_printenv` / `fw_setenv` | libubootenv command-line access to the redundant environment. |

Important paths are:

| Path | Lifetime/purpose |
| --- | --- |
| `/data/update/upload/firmware.swu.ready` | Persistent, bounded web-owned staged upload. |
| `/data/update/upload/current.json` | Capability-protected progress state used to reconnect across reboot. |
| `/data/update/queue/firmware.swu` | Persistent, root-owned fixed package consumed by installer. |
| `/data/update/history.json` | Persistent bounded result history. |
| `/run/firmware-update/install.lock` | Runtime worker PID/operation exclusion. |
| `/run/firmware-update/status.json` | Runtime atomic UI status. |
| `/run/firmware-update/swupdate.log` | Runtime SWUpdate output. |
| `/run/swupdate/` | Runtime SWUpdate control/progress sockets. |
| `/tmp/firmware-health.log` | Current-boot health-service output. |

For the complete p4 inventory—including settings, credentials, identities,
schema/migration files, build-time seeding, early-boot links, permissions and
deliberately volatile state—see [Persistent data](persistent-data.md).

The installer never accepts a slot, device, package path, or command from the
web client. It derives the inactive slot from the trusted kernel command line,
requires a matching root partition, validates the restricted two-member archive,
runs SWUpdate check mode, and only then installs.

## Health acceptance

The late BusyBox service first reconciles an acceptance or U-Boot fallback that
completed before history was updated. If `upgrade_available=1`, it starts a
non-blocking evaluator with a 120-second deadline. Checks are intentionally local:

- `radio.slot` is A or B and matches `root=/dev/mmcblk0p2|p3`;
- U-Boot `active_slot` matches the running slot;
- `/data` is read-write ext4;
- the persistent schema migration is complete;
- the local `/healthz` endpoint responds successfully;
- the PiSonic heartbeat is recent;
- MPD is running when Internet Radio is enabled.

When healthy, it atomically writes `bootcount=0` and `upgrade_available=0`,
records `accepted`, and marks the migration generation accepted. libubootenv's
`fw_setenv -s` files require exact `key=value` lines; space-separated lines are
ignored. Environment writers must verify syntax and, for slot switching, read
back the committed state.

## Fallback mechanics

During every trial boot, `boot.scr` increments and saves `bootcount` before
loading Linux. If health does not pass before its deadline, Linux records
`trial_failed`, syncs, and reboots without clearing the trial.

The U-Boot condition is `bootcount > bootlimit`. With limit 3, three trial
attempts are made. At the beginning of the fourth boot U-Boot:

1. sets `rollback_from` to the failed `active_slot`;
2. restores `active_slot=previous_slot`;
3. clears `upgrade_available`;
4. resets `bootcount=0`;
5. saves the redundant environment;
6. boots the retained slot.

The retained Linux service records `automatic_rollback` and clears the one-shot
`rollback_from` marker. U-Boot owns boot selection and attempt counting because
those decisions must still work when Linux cannot start.

## Persistent-data compatibility

Rollback is safe only if retained firmware can read state written by newer
firmware. Release metadata and the SWU manifest carry:

- current persistent schema;
- minimum schema the target reader can consume;
- minimum reader schema required after migration for rollback.

The installer rejects an update requiring an irreversible migration relative to
the retained slot. Manual switching applies the same compatibility checks. A
different appliance should design this contract before its first release;
adding it after incompatible state has shipped is much harder.

## Porting checklist

For another Raspberry Pi appliance:

1. Define immutable slot, loader, environment, and persistent-data ownership.
2. Choose non-overlapping environment offsets outside every partition and
   appropriate to the storage medium's alignment/erase behavior.
3. Enable U-Boot redundancy itself, not only a redundant offset.
4. Make U-Boot, image generation, and Linux `fw_env.config` share offsets, size,
   and binary format; validate the resolved build artifacts.
5. Seed every variable referenced after persistent environment import, including
   load addresses and `bootcmd`.
6. Ensure each slot supplies a compatible kernel, modules, rootfs, and release
   metadata. Avoid one mutable shared kernel unless its compatibility is proven.
7. Make the kernel command line identify the selected slot and root exactly once.
8. Never derive a target block device from untrusted upload metadata or web input.
9. Keep mutable identity and configuration outside both slots and define schema
   compatibility across upgrades and rollback.
10. Restrict SWUpdate handlers and package members to the minimum required set.
11. Define local health checks that prove the appliance's core purpose without
    depending on optional Internet services.
12. Keep trial counting in the bootloader and acceptance in Linux.
13. Provide serial recovery before testing failures.
14. Add signed updates and verified boot when authenticity is required.
15. Qualify A-to-B, B-to-A, failed health, corrupt slot, interrupted write, power
    loss, full storage, and worn-media behavior on representative hardware.

## Source map

| Concern | Primary source |
| --- | --- |
| Buildroot selections | `buildroot/external/configs/radio_rpi3_defconfig` |
| Partition constants | `buildroot/external/board/radio/image-layout.conf` |
| genimage map | `buildroot/external/board/radio/genimage.cfg.in` |
| U-Boot Kconfig | `buildroot/external/board/radio/uboot.fragment` |
| Initial environment | `buildroot/external/board/radio/uboot-env.txt` |
| Boot policy | `buildroot/external/board/radio/boot.cmd` |
| Linux environment map | `buildroot/external/board/radio/rootfs-overlay/etc/fw_env.config` |
| SWUpdate Kconfig | `buildroot/external/board/radio/swupdate.config` |
| Manifest template | `buildroot/external/board/radio/sw-description.in` |
| SWU builder | `scripts/build_firmware_swu.py` |
| Image integration | `buildroot/external/board/radio/post-image.sh` |
| Runtime installer | `radio_web/firmware_installer.py` |
| Health/fallback reconciliation | `radio_web/firmware_health.py` |
| Manual switch | `radio_web/firmware_slots.py` |