#!/bin/sh
# apply-backlight-bcm12.sh
#
# Move the SPI display backlight (BL) from BCM 22 to BCM 12 in display.conf on a
# running Kitchen Radio device. Idempotent and safe to re-run.
#
# IMPORTANT: this only changes software configuration. You MUST also move the
# display BL jumper from physical pin 15 (BCM 22) to physical pin 32 (BCM 12)
# with the Pi powered OFF, or the backlight will stay dark after reboot.
#
# Recommended supported path is a firmware update via the web UI. Use this only
# for a deliberate manual on-device change. Run it ON the device, e.g.:
#   ssh root@<device> 'sh -s' < scripts/apply-backlight-bcm12.sh
#
# Exit codes: 0 success/no-op, 1 config file missing, 2 unexpected bl value.

set -eu

CONF="/opt/raspberry-kitchen-radio/lib/display_1_inch_69/display.conf"

if [ ! -f "$CONF" ]; then
    echo "ERROR: $CONF not found on this system." >&2
    exit 1
fi

current="$(sed -n 's/^bl[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$CONF" | head -n1)"

if [ "$current" = "12" ]; then
    echo "Already configured: bl = 12. No change needed."
    exit 0
fi

if [ "$current" != "22" ]; then
    echo "ERROR: expected 'bl = 22' or 'bl = 12', found bl = '${current:-<none>}'." >&2
    echo "Refusing to edit automatically; inspect $CONF by hand." >&2
    exit 2
fi

backup="${CONF}.bak.$(date +%Y%m%d%H%M%S)"
cp "$CONF" "$backup"
echo "Backed up $CONF -> $backup"

# Stop the radio so it is not holding the display, if the init script exists.
if [ -x /etc/init.d/S90radio ]; then
    /etc/init.d/S90radio stop || true
fi

sed -i 's/^bl[[:space:]]*=[[:space:]]*22/bl = 12/' "$CONF"

new="$(sed -n 's/^bl[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$CONF" | head -n1)"
if [ "$new" != "12" ]; then
    echo "ERROR: edit failed; restoring backup." >&2
    cp "$backup" "$CONF"
    exit 2
fi

echo "Updated: bl = 12"
echo
echo "NEXT STEPS (hardware, do with power OFF):"
echo "  1. poweroff"
echo "  2. Move display BL jumper: physical pin 15 (BCM 22) -> physical pin 32 (BCM 12)"
echo "  3. Power on and verify the backlight lights."
