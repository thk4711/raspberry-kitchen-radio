# Raspberry Kitchen Radio A/B boot policy. Paths and devices are fixed.
if test "${active_slot}" != "A" && test "${active_slot}" != "B"; then
	setenv active_slot A
fi
if test "${previous_slot}" != "A" && test "${previous_slot}" != "B"; then
	setenv previous_slot B
fi
if test "${upgrade_available}" != "1"; then
	setenv upgrade_available 0
fi
if test -z "${bootcount}"; then
	setenv bootcount 0
fi
if test -z "${bootlimit}"; then
	setenv bootlimit 3
fi

if test "${upgrade_available}" = "1"; then
	setexpr bootcount ${bootcount} + 1
	if test ${bootcount} -gt ${bootlimit}; then
		setenv rollback_from ${active_slot}
		setenv active_slot ${previous_slot}
		setenv upgrade_available 0
		setenv bootcount 0
	fi
	saveenv
fi

if test "${active_slot}" = "B"; then
	setenv rootpart 3
	setenv rootslot B
else
	setenv rootpart 2
	setenv rootslot A
fi

setenv bootargs
setenv bootargs "root=/dev/mmcblk0p${rootpart} rootwait console=tty1 quiet loglevel=3 logo.nologo radio.slot=${rootslot} boot_source=uboot"
if ext4load mmc 0:${rootpart} ${kernel_addr_r} /boot/zImage; then
	bootz ${kernel_addr_r} - ${fdt_addr}
fi

# A load/boot failure during a trial must consume another attempt immediately.
if test "${upgrade_available}" = "1"; then
	reset
fi
echo "Unable to boot firmware slot ${rootslot}"