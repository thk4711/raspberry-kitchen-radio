# ALSA audio path and routing

This is the reference for **how audio flows through ALSA** on PiSonic: where
every source converges, which optional stages (parametric EQ, software volume)
are layered on top, and how the two receiver bridges (`bluealsa-aplay`,
`alsaloop`) feed into the same route.

Feature-specific depth lives in the focused guides and is only cross-linked from
here:

- Per-card pinouts and the profile catalog — [`sound-devices.md`](sound-devices.md).
- The LADSPA plugin ABI, live `.rt` updates and EQ controls —
  [`equalizer.md`](equalizer.md).
- The USB gadget capture and inhibition logic — [`usb-audio.md`](usb-audio.md).
- The A2DP receiver — [`bluetooth.md`](bluetooth.md).
- The manual per-file fallback for the built-in card —
  [`analog-audio.md`](analog-audio.md).

## Overview: one convergence point, layered stages

Every playback source converges on the ALSA `default` device
(`pcm.!default` in `/etc/asound.conf`). Because the software mixer,
parametric EQ and stable-card routing all hang off that single point, the
physical volume knob and one EQ configuration apply identically to **Internet
Radio (MPD), AirPlay (shairport-sync), Spotify Connect (go-librespot), Bluetooth
(bluealsa-aplay) and USB Audio (alsaloop)**.

The optional stages are inserted in a fixed order between the applications and
the hardware:

```text
 sources ─▶ ALSA default ─▶ [LADSPA EQ] ─▶ [Radio Volume softvol] ─▶ [dmix] ─▶ stable card ─▶ DAC / amplifier
 (MPD, shairport-sync,        pcm.!default   optional, web-toggled   optional, only          shares one PCM     CARD= id, never hw:0
  go-librespot, bluealsa-                                            when the card has        among sources
  aplay, alsaloop)                                                   no hardware mixer
```

Both optional stages (`[…]`) are present only in some configurations:

- The **LADSPA EQ** stage exists only while the equalizer is enabled on the web
  Audio page. It also hosts the optional loudness compensation, whose bass/treble
  boost tapers with the current volume (fed live from the ADC volume loop).
- The **`Radio Volume` softvol + dmix** stage exists only for cards that have no
  usable hardware volume control (most bare I2S DACs). Cards with a hardware
  mixer (built-in headphones → `PCM`, many DAC/amp HATs → `Digital`) use that
  control directly instead.

## The convergence point: `pcm.!default`

All receivers target ALSA `default`, so the route is decided in one place:

- **MPD** is configured explicitly (`device "sysdefault:CARD=…"` /
  `default`) so it can never silently fall back to card 0 (HDMI).
- **shairport-sync**, **go-librespot**, **bluealsa-aplay** and **alsaloop** use
  the ALSA `default` device directly.

Every profile routes by a **stable ALSA card id** (`CARD=Headphones`,
`CARD=IQaudIODAC`, `CARD=sndrpihifiberry`, …) rather than `hw:0`. The numeric
card index can change when HDMI or the USB Audio gadget is present, so a numeric
index would misroute audio; the stable id does not.

> `/etc/asound.conf` must be world-readable (`0644`). MPD runs as the
> unprivileged `mpd` user; if it cannot read the file, ALSA silently falls back
> to card 0 (usually HDMI). The privileged helper always writes it `0644`.

## Volume: hardware mixer vs. `Radio Volume` softvol

Which mixer the knob drives depends on the selected profile:

- **Hardware mixer** — the built-in headphone jack exposes `PCM`; most DAC/amp
  HATs expose `Digital`. The knob (and the max-volume cap) drive that hardware
  control, and `asound.conf` contains no softvol stage.
- **Software volume (`softvol`)** — cards with no usable hardware volume are
  wrapped in a shared softvol PCM named **`Radio Volume`** layered over `dmix`
  and the stable card. Because every source uses `default`, the single
  `Radio Volume` control governs all of them. Its range is `-60.0 … 0.0 dB`
  across `101` steps.

The web Audio page's maximum-volume cap and the physical knob map onto whichever
of these two controls the active profile uses. To read the softvol control on
target you may need to start playback once so ALSA creates it, then:

```sh
amixer sget 'Radio Volume'
```

## Parametric EQ insertion

When the equalizer is enabled, the helper renames the rendered default PCM to
`pcm.radio_output` and inserts a project LADSPA stage in front of it:

```text
pcm.!default (plug) ─▶ pcm.radio_equalizer (type ladspa) ─▶ pcm.radio_output ─▶ … (softvol/dmix/card)
```

The LADSPA PCM is fixed to two channels and requires native
FLOAT/non-interleaved samples; the surrounding `plug` stages convert ordinary
application formats. Toggling the whole EQ on/off is a structural change that
rewrites `asound.conf` and briefly restarts the audio consumers; ordinary band
and preamp tweaks are pushed **live** through `/run/radio/equalizer.rt` without a
stream restart. See [`equalizer.md`](equalizer.md#developer-architecture) for the
control ABI and the live-update design.

**Headphone special case.** With EQ enabled the built-in headphone profile uses
the raw stable PCM `hw:CARD=Headphones,DEV=0` instead of `sysdefault:CARD=…`.
alsa-lib 1.2.15 cannot negotiate the LADSPA FLOAT/non-interleaved stream through
the nested `sysdefault` plug (a plug→plug nesting yields an empty hw-parameter
interval); the raw stable PCM avoids that. The normal EQ-bypass route keeps
`sysdefault`.

## The two receiver bridges

Two sources are not applications that open ALSA themselves — they are bridges
that copy a received stream into ALSA `default`:

- **Bluetooth (A2DP):** `bluealsa` receives the A2DP stream and
  `bluealsa-aplay` routes the decoded PCM to ALSA `default`. See
  [`bluetooth.md`](bluetooth.md).
- **USB Audio:** the DWC2 gadget presents a UAC1/UAC2 capture device;
  `alsaloop` copies it into ALSA `default` with adaptive libsamplerate
  resampling because the USB host clock and the I2S DAC clock are independent.
  A `/run/usb-audio-inhibited` marker lets another source take over. See
  [`usb-audio.md`](usb-audio.md).

Because both bridges target `default`, the EQ, `Radio Volume` softvol and stable
card apply to them exactly as they do to MPD, AirPlay and Spotify.

## `dmix` and shared access

Softvol and hardware-volume I2S profiles place a `dmix` PCM over the stable
card so multiple sources can open playback at once. It is shared through a
group-owned IPC segment:

```text
ipc_key 1024
ipc_gid audio
ipc_perm 0660
```

All audio services run in (or share) the `audio` group so they can attach to the
same `dmix` instance.

## Who generates `asound.conf`

`/etc/asound.conf` is generated by the privileged web helper when you apply a
sound-card selection or toggle the EQ:

- `radio_web/audio_hardware_apply.py` — `render_asound()` renders the stable-card
  route with the optional softvol layer; `_with_equalizer()` wraps it in the
  LADSPA stage. See [`web-interface.md`](web-interface.md) for the Audio page.

For a hand-edited fallback (for example when the web UI is unavailable), the
manual per-file procedure for the built-in card is in
[`analog-audio.md`](analog-audio.md).


## Worked examples

These are exactly what the renderer emits.

### Built-in headphone jack, EQ off (hardware `PCM` mixer, no softvol)

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

### Software-volume I2S card, EQ off (`Radio Volume` softvol over dmix)

```ini
pcm.radio_dmix {
    type dmix
    ipc_key 1024
    ipc_gid audio
    ipc_perm 0660
    slave {
        pcm "hw:CARD=sndrpihifiberry,DEV=0"
        rate 48000
        format S16_LE
        period_time 0
        period_size 1024
        buffer_size 4096
    }
}

pcm.radio_softvol {
    type softvol
    slave.pcm "radio_dmix"
    control {
        name "Radio Volume"
        card sndrpihifiberry
    }
    min_dB -60.0
    max_dB 0.0
    resolution 101
}

pcm.!default {
    type plug
    slave.pcm "radio_softvol"
}

ctl.!default {
    type hw
    card sndrpihifiberry
}
```

### Any profile with EQ enabled

The rendered default PCM is renamed to `pcm.radio_output` and the LADSPA stage is
appended, with `pcm.!default` pointing at it:

```ini
# ... (radio_output = the route from the examples above) ...

# User-configured ten-band parametric equalizer.
pcm.radio_equalizer {
    type ladspa
    channels 2
    path "/usr/lib/ladspa"
    slave.pcm "radio_output"
    playback_plugins {
        0 {
            label "radio_equalizer"
            policy duplicate
            input.controls {
                    0 -6
                    ...
            }
        }
    }
}

pcm.!default {
    type plug
    slave.pcm "radio_equalizer"
}
```

## Verification and troubleshooting

Inspect the cards, the exposed PCMs and the active route:

```sh
cat /proc/asound/cards
aplay -l
aplay -L
grep -A8 'pcm.radio_equalizer' /etc/asound.conf   # only present when EQ is on
amixer -c <card-id> scontrols
```

Confirm which processes hold a PCM open while audio plays:

```sh
for p in /proc/[0-9]*; do
    ls -l "$p/fd" 2>/dev/null |
        grep /dev/snd/pcm |
        sed "s|^|${p##*/} |"
done

ps | grep -E 'radio.py|mpd|shairport|librespot|bluealsa|alsaloop'
```

- **No sound with EQ on:** bypass the EQ and apply again; then check that
  `/etc/asound.conf` still contains `type ladspa`, `channels 2` and the
  `radio_equalizer` label, and that `/usr/lib/ladspa/radio_equalizer.so` exists.
- **Audio on the wrong device / HDMI:** confirm `/etc/asound.conf` is `0644`
  (MPD reads it as the `mpd` user) and that the route uses a `CARD=` id, not
  `hw:0`.
- **Softvol knob does nothing:** start playback once so ALSA creates
  `Radio Volume`, then re-check with `amixer sget 'Radio Volume'`.

## Related documentation

- [Supported sound devices](sound-devices.md) — per-card pinouts, stable ids and
  mixers, and on-device qualification.
- [Parametric equalizer](equalizer.md) — EQ controls, LADSPA ABI and live updates.
- [USB audio source](usb-audio.md) — the `alsaloop`/libsamplerate bridge.
- [Bluetooth](bluetooth.md) — the `bluealsa` A2DP receiver.
- [Using the built-in analog audio output](analog-audio.md) — manual fallback.
- [Web administration interface](web-interface.md) — the Audio page that applies
  these settings.

