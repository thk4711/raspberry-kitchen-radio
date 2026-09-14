# Using the built-in analog audio output

The Raspberry Pi 3A+'s built-in headphone output is the shipped default because
it is always available. This guide documents that configuration and can also be
used to switch an appliance back from an external I2S DAC or amplifier board.

> **Tip:** the web administration interface now automates this switch. Its
> **Sound card** page (`/audio-hardware`, see [`web-interface.md`](web-interface.md))
> makes the same `config.txt` / `asound.conf` / `mpd.conf` / mixer edits from a
> radio-button list and applies them on reboot. This document remains the manual
> fallback and reference for what those edits are.

## 1. Change the firmware configuration

Mount the FAT boot partition if it is not already mounted and back up its
configuration:

```sh
mkdir -p /mnt/boot
mount /dev/mmcblk0p1 /mnt/boot
cp /mnt/boot/config.txt /mnt/boot/config.txt.i2s-backup
```

In `/mnt/boot/config.txt`, disable I2S and the external DAC overlay, and enable
the on-board audio device:

```ini
# dtparam=i2s=on
dtparam=audio=on
#dtoverlay=iqaudio-dacplus
```

Leave the alternative `hifiberry-dacplus`, `hifiberry-amp` and `merus-amp`
overlays commented out as well.

## 2. Load the on-board audio driver

The Buildroot image contains the driver, but it must be loaded at boot. Create
`/etc/modules-load.d/audio.conf` containing:

```text
snd-bcm2835
```

For example:

```sh
mkdir -p /etc/modules-load.d
printf '%s\n' 'snd-bcm2835' > /etc/modules-load.d/audio.conf
```

## 3. Route ALSA to the headphone card

Use the stable ALSA card ID rather than a numeric card index. HDMI and the USB
Audio gadget can change the numeric card ordering.

Back up `/etc/asound.conf`, then replace it with:

```ini
pcm.!default {
    type plug
    slave.pcm "sysdefault:CARD=Headphones"
}

ctl.!default {
    type hw
    card Headphones
}
```

Make the file readable by all audio services:

```sh
chmod 0644 /etc/asound.conf
```

This permission is required because MPD runs as the unprivileged `mpd` user.
If MPD cannot read `/etc/asound.conf`, ALSA silently falls back to card 0, which
is normally HDMI when on-board audio is enabled.

## 4. Select the headphone mixer

The headphone output uses the `PCM` mixer rather than the I2S DAC's `Digital`
mixer. Change `/opt/raspberry-kitchen-radio/radio.conf` to:

```ini
[audio]
mixer = PCM
```

## 5. Configure MPD explicitly

Set the `audio_output` block in `/etc/mpd.conf` to:

```conf
audio_output {
    type          "alsa"
    name          "DAC"
    device        "sysdefault:CARD=Headphones"
    mixer_device  "hw:CARD=Headphones"
    mixer_type    "hardware"
    mixer_control "PCM"
}
```

Explicit MPD routing provides an additional safeguard against falling back to
HDMI.

## 6. Other audio sources

AirPlay (`shairport-sync`), Spotify Connect (`go-librespot`), Bluetooth
(`bluealsa-aplay`), and the USB Audio bridge use the ALSA `default` device and
therefore follow `/etc/asound.conf`. The appliance uses `go-librespot`, not
Raspotify.

## 7. Reboot and verify

Reboot the appliance:

```sh
reboot
```

After reconnecting over SSH, check the cards and headphone mixer:

```sh
cat /proc/asound/cards
aplay -l
amixer -c Headphones sget PCM
```

While audio is playing, inspect the open PCM devices:

```sh
for p in /proc/[0-9]*; do
    ls -l "$p/fd" 2>/dev/null |
        grep /dev/snd/pcm |
        sed "s|^|${p##*/} |"
done
```

Processes should use the `Headphones` card (for example
`/dev/snd/pcmC1D0p`). The exact card number can vary, so confirm it against
`/proc/asound/cards`. The HDMI PCM should remain closed.

## Backups used on the development appliance

The configuration session retained these originals:

```text
/mnt/boot/config.txt.i2s-backup
/etc/asound.conf.i2s-backup
/etc/mpd.conf.i2s-backup
/opt/raspberry-kitchen-radio/radio.conf.i2s-backup
```