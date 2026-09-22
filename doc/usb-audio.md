# USB audio source

The Raspberry Pi 3A+ can appear to a computer, phone or tablet as a playback-only
USB sound card named **PiSonic USB Audio**. Starting a USB stream
automatically selects this source and stops the previously active source.

> **Wire the connection before enabling it.** The required connection carries
> only **D−, D+ and GND**. Do **not** connect USB VBUS/+5 V. See
> [hardware.md](hardware.md#usb-audio-gadget-wiring) for the pin table and safety
> notes.

## Profile and data path

The gadget defaults to USB Audio Class 1 for broad Windows, macOS and Linux
support. The Audio page can switch the gadget to an experimental UAC2 profile.

### UAC1 compatibility mode (default)

| Property | Value |
| --- | --- |
| Direction | Host playback to Internet Radio only (no microphone) |
| Channels | 2 (stereo) |
| Format | Signed 16-bit little-endian PCM |
| Rate | Fixed 48 kHz |
| Product | `PiSonic USB Audio` |
| VID:PID | `1d6b:0101` (private prototype use only) |

### UAC2 high-resolution mode

UAC2 advertises stereo host playback at 44.1, 48 and 96 kHz with 24-bit
samples. The pinned kernel exposes multiple UAC2 rates, but one sample size per
direction; this profile therefore uses packed 24-bit samples (`S24_3LE`). UAC2
host compatibility is not as broad as UAC1. The setting is stored in
`/etc/radio/usb_audio.ini` and defaults to UAC1 when absent or invalid.

### Output format depends on the selected sound-card profile

USB input capability and output capability are separate. The selected sound-card
profile on the Audio page declares the ALSA format and nominal rate used by the
shared output route and by the USB bridge:

| Output profile type | Output format | Output rate | Result |
| --- | --- | --- | --- |
| Built-in headphone jack | `S16_LE` | 44.1 kHz | The Pi headphone device is limited to 16-bit output. UAC2 input is converted before playback. |
| Qualified I2S DAC | Profile-specific, for example `S32_LE` | Profile-specific, for example 48 kHz | 24-bit precision can be retained in a 32-bit ALSA container when the DAC driver supports it. |

The profile fields are `output_format` and `output_rate` in
`radio_web/audio_hardware_profiles.json`. Applying a sound-card profile writes
the generated policy to:

```text
/etc/radio/usb_audio_output.ini
```

For example, the IQaudIO profile currently declares:

```ini
[audio_output]
format = S32_LE
rate = 48000
```

`S32_LE` commonly represents 24-bit audio in a 32-bit sample container. It does
not mean that every DAC supports 32-bit output; each profile must be verified
against its actual ALSA hardware parameters. The built-in headphone profile
intentionally remains `S16_LE`.

Changing the selected output profile requires applying it and rebooting. Do not
manually change `usb_audio_output.ini` on a running appliance: it is generated
from the selected profile and will be replaced by the next profile application.

Select the mode on the web interface's Audio page, reboot, and disconnect/reconnect
the host so it enumerates the new descriptors. UAC2 does not by itself guarantee
bit-perfect I2S output: the shared ALSA output path and independent USB/I2S clocks
may still require conversion or adaptive clock correction.

```text
USB host -> DWC2 -> ConfigFS UAC1/UAC2 -> USB gadget ALSA capture
         -> format/rate conversion as required by the selected profile
         -> alsaloop/libsamplerate -> ALSA default -> profile output -> DAC/amplifier
```

`alsaloop` starts only while the host has activated the streaming interface. It
uses adaptive libsamplerate synchronization because the USB and I2S DAC clocks
are independent.

## Source switching

USB Audio has no in-band command to pause the host, so PiSonic uses two
mechanisms together when another source takes over:

1. **Inhibit marker (authoritative fallback).** The app creates
   `/run/usb-audio-inhibited`; the bridge closes `alsaloop` and ignores the
   still-open USB stream. The inhibition is removed after the host closes that
   stream (`Capture Rate` returns to zero), so a later, newly started USB stream
   can take over normally. Pressing a radio preset therefore reliably switches
   away even if the computer keeps its audio device open.
2. **HID media key (best effort).** The USB gadget is *composite* — it presents a
   consumer-control HID interface alongside the USB sound card. On stop the app
   runs `radio-usb-audio-hid playpause`, which writes a Play/Pause consumer usage
   to `/dev/hidg0`. A host that maps media keys to its player then actually
   pauses playback, rather than continuing to stream into a muted device.

The HID key is best effort: not every host acts on consumer keys, and the key
targets whatever the host treats as the active media app, so the inhibit marker
is always kept as the guaranteed switch. If `usb_f_hid` is unavailable the gadget
still comes up as an audio-only device and the HID step becomes a no-op.

### Composite HID gadget

The gadget adds `functions/hid.usb0` (a one-byte consumer-control report:
Play/Pause, Scan Next, Scan Previous, Stop) next to the UAC function and links
both into the same configuration. This changes the device from an audio-only
class to a composite device, so **hosts must re-enumerate** (disconnect/reconnect,
or a fresh image) to see the new interface; Windows in particular caches
descriptors by VID/PID. The kernel needs `CONFIG_USB_CONFIGFS_F_HID=y`
(`linux-usb-audio.fragment`).

The default MPD station is started before the source-polling thread. An
already-active USB host therefore wins according to the documented
AirPlay/USB/MPD source priority instead of being mistaken for an older stream
that should be inhibited.

## On-target validation

```sh
# Service and USB Device Controller
/etc/init.d/S39usb-audio status
ls -l /sys/class/udc
cat /sys/kernel/config/usb_gadget/radio-usb-audio/UDC

# ALSA devices and boolean stream state
cat /proc/asound/cards
arecord -l
aplay -l
radio-usb-audio-stream-playing

# Composite HID media-key interface
ls -l /dev/hidg0                       # present once the host enumerates the HID function
cat /sys/kernel/config/usb_gadget/radio-usb-audio/functions/hid.usb0/report_length
radio-usb-audio-hid playpause          # send a Play/Pause key to the host

# While the host is playing (UAC1 normally reports 48000; UAC2 may report
# 44100, 48000 or 96000)
amixer -c UAC1Gadget cget "iface=PCM,name='Capture Rate'"  # UAC1
amixer -c UAC2Gadget cget "iface=PCM,name='Capture Rate'"  # UAC2
cat /etc/radio/usb_audio_output.ini
ps | grep '[a]lsaloop'
```

The selected physical output should remain the target of ALSA `default`. Sound
card profiles route by stable ALSA card id, so creating the gadget cannot change
the destination merely by changing numeric card order. Confirm this after the
first build with `aplay -L` and `/proc/asound/cards`.

To verify the selected output's real capabilities, use the hardware device
rather than `default`:

```sh
aplay -D hw:CARD=<stable-card-id>,DEV=0 --dump-hw-params /dev/zero
```

Check the reported `FORMAT` and `RATE` before assigning a high-resolution
`output_format` or `output_rate` to a profile. A format/rate declared by a
profile is a routing policy, not a substitute for target hardware validation.

## Troubleshooting

- No `/sys/class/udc/3f980000.usb`: verify
  `dtoverlay=dwc2,dr_mode=peripheral` and reboot.
- Host does not enumerate: verify D+/D− continuity and common GND, ensure VBUS is
  disconnected, and inspect `dmesg | grep -Ei 'dwc2|gadget|uac'`.
- Host sees the card but there is no sound: check `Capture Rate`, `alsaloop`, the
  inhibit marker, and the ALSA default device:

  ```sh
  cat /run/usb-audio-inhibited 2>/dev/null
  aplay -L
  ```

- UAC2 plays through the built-in headphone jack but does not sound high
  resolution: this is expected. The headphone device supports `S16_LE`; select
  a verified I2S output profile if 24-bit output is required.

- iOS showing `Playback Inactive` is the upstream UAC1 alternate-interface
  string, not the ConfigFS product name. The appliance kernel patch replaces it
  with the appliance function name `PiSonic USB Audio`; a rebuilt image
  and USB disconnect/reconnect are required for that descriptor change.

- Restart the complete stack with `/etc/init.d/S39usb-audio restart`.
- `not attached` is normal while no host is connected.

Changing the USB descriptors may require disconnecting/reconnecting the host;
Windows can cache descriptors by VID/PID. A distributed or commercial product
must use appropriately assigned USB identifiers rather than the prototype
Linux Foundation values.