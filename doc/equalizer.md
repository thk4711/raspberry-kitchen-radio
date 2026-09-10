# Parametric equalizer

The radio has an optional ten-band parametric equalizer in the authenticated web
interface. It processes the common ALSA playback path, so one configuration
applies to Internet Radio, AirPlay, Spotify Connect, Bluetooth and USB Audio.

## Using the equalizer

Open **Audio** at `http://<radio-address>:8080/audio-hardware`. The
**Parametric equalizer** form is at the top of the page, above the sound-card,
USB Audio, volume and output Apply/Restore controls.

### Global controls

- **Enable equalizer** inserts or bypasses the complete DSP stage.
- **Preamp Gain** applies gain before the filters. Its range is `-24` to `0 dB`
  and it defaults to `-3 dB` to leave headroom out of the box. Reduce it further
  when boosting bands to preserve headroom; a practical starting point is a
  preamp reduction equal to the largest positive band gain.
- The graph covers 20 Hz to 20 kHz on a logarithmic frequency axis and shows the
  combined preamp and filter response.

### Band controls

Each of the ten bands has:

- **On** — enables that band without changing its stored parameters.
- **Curve** — Bell, Low shelf, High shelf, High-pass or Low-pass.
- **Gain** — `-15` to `+15 dB`; it is not used by high-pass or low-pass filters.
- **Q** — `0.1` to `10`; larger values make a bell narrower and control the
  transition/resonance of the other filter types.
- **Frequency** — `20` to `20000 Hz`.

For bell and shelf filters, drag a colored dot horizontally to change frequency
and vertically to change gain. Numeric inputs remain the authoritative,
keyboard-accessible controls. When the global EQ is bypassed, the response line
is flat and the dots are subdued. Dots cannot be dragged while the complete EQ
is disabled; when it is enabled, only dots belonging to enabled bands can move.

### Save, apply and reset

- **Save and Apply** validates, stores the controls and updates the sound. Ordinary changes
  (preamp, per-band type/frequency/gain/Q, and enabling or disabling individual
  bands) are applied **live**: they are pushed to a small runtime file that the
  LADSPA plugin re-reads without closing any stream, so playback is not
  interrupted. Only toggling the **whole** equalizer on or off has to insert or
  remove the DSP stage in the ALSA route, which still briefly restarts the radio
  application and all audio receivers — ALSA can add or drop a LADSPA PCM only
  when a playback PCM is (re)opened, so MPD, AirPlay, Spotify, Bluetooth and USB
  Audio close and reopen their streams that one time.
- **Reset flat** immediately resets the form and graph, then applies the reset:
  the EQ is disabled, the preamp returns to its `-3 dB` default and all band gains
  become `0 dB`, all bands become disabled Bell filters with `Q = 1`, and the dots
  return to 60, 120, 250, 500, 1000, 2000, 4000, 8000, 12000 and 16000 Hz.

The browser graph is a preview. Audio changes only after **Save and Apply** (or
**Reset flat**) completes.

**Save and Apply** and **Reset flat** submit without reloading the page:
the request is sent in the background and a short status line appears above the
band controls, so you keep your scroll position at the equalizer. When
JavaScript is unavailable the same buttons fall back to a normal form submit
that reloads the Audio page (returning you to the top), which remains fully
functional.

## Persistence and firmware updates

Non-default settings are stored in:

```text
/etc/radio/equalizer.ini -> /data/radio/equalizer.ini
```

`/etc/radio` is a compatibility link to the shared ext4 data partition
(`/dev/mmcblk0p4`). Normal `.swu` updates replace only inactive root slot `p2`
or `p3`, so EQ settings survive upgrades, trial boots and automatic rollback.
They are also included in a Maintenance-page persistent-data backup.

**Reset flat** removes `equalizer.ini`; a missing file intentionally means the
built-in disabled, flat defaults. Flashing a complete `sdcard.img` rewrites the
data partition, so export a Maintenance backup before reflashing.

The live parameter cache the plugin re-reads is a separate, **non-persistent**
runtime file:

```text
/run/radio/equalizer.rt
```

It lives on tmpfs and is regenerated from `equalizer.ini` at boot (by the
root-owned helper, before playback starts) and on every Apply, so it is
intentionally rebuilt after any reboot, update or rollback. The persistent
source of truth is always `equalizer.ini` on the data partition. The
`/run/radio` directory is created `0755` (by inittab and by the writer) so the
plugin can read the file inside the unprivileged `mpd` process; otherwise live
updates would silently not reach internet radio.

## Troubleshooting and on-device checks

If Apply succeeds but audio is absent, first bypass the EQ and apply again. Then
inspect the active route and services:

```sh
cat /etc/radio/equalizer.ini
grep -A8 'pcm.radio_equalizer' /etc/asound.conf
aplay -L
ps | grep -E 'radio.py|mpd|shairport|librespot|bluealsa|alsaloop'
mpc status
```

With EQ enabled, `/etc/asound.conf` must contain `type ladspa`, `channels 2` and
the `radio_equalizer` label. The project plugin must exist at:

```text
/usr/lib/ladspa/radio_equalizer.so
```

The built-in headphone profile uses the stable raw PCM
`hw:CARD=Headphones,DEV=0` while EQ is enabled. This is intentional: ALSA 1.2.15
cannot negotiate the LADSPA FLOAT/non-interleaved stream through the nested
`sysdefault` plug. Software-volume I2S profiles retain this order:

```text
application -> ALSA default -> LADSPA EQ -> plug -> Radio Volume softvol
            -> dmix -> stable I2S hardware
```

## Developer architecture

The feature is split into small components:

| Component | Responsibility |
| --- | --- |
| `buildroot/external/package/radio-equalizer/` | Builds the MIT-licensed ARMv7 LADSPA shared object that also re-reads live parameters from `/run/radio/equalizer.rt`. |
| `radio_web/equalizer_store.py` | Defaults, strict validation, INI parsing, atomic persistence, one-generation backup, and the atomic runtime-file (`.rt`) writer. |
| `radio_web/audio_hardware_apply.py` | Serializes controls, writes the live runtime file, and inserts/bypasses the EQ stage in the selected sound-card route only when the stage presence changes. |
| `radio_web/templates.py` | Server-rendered accessible controls and SVG container. |
| `radio_web/static/app.js` | Biquad response preview, dragging and immediate visual flat reset. |
| `radio_web/helper.py` | Seeds the runtime file at boot, applies live parameter pushes, and restarts every long-lived PCM consumer only on a structural (stage on/off) change. |

The LADSPA plugin is mono and uses ALSA's `policy duplicate` to create one
instance per stereo channel. It exposes 51 control inputs: preamp plus enable,
type, frequency, gain and Q for each of ten bands. The ALSA LADSPA PCM is fixed
to two channels and requires native FLOAT/non-interleaved samples; surrounding
`plug` stages convert normal application formats and the selected output format.

### Live updates without a stream restart

ALSA's `ladspa` PCM reads its `input.controls` only once, when the PCM is
opened, and exposes no runtime control elements — so historically every audio
consumer had to be restarted to apply new values. To make ordinary changes
live, the project plugin additionally memory-maps a small fixed-size runtime
file (`/run/radio/equalizer.rt`, overridable with `RADIO_EQUALIZER_RT`). The
file holds a `magic`, a `generation` counter and the 51 control floats in the
same order as the ports. `apply_equalizer` writes it atomically and bumps the
generation; the plugin cheaply checks the generation once per `run()` call and
recomputes coefficients when it changes, never allocating in the audio path.
When the file is absent or malformed the plugin transparently falls back to the
values ALSA connected to its control ports. Because inserting or removing the
`ladspa` stage itself is still an open-time change on alsa-lib 1.2.15, toggling
the whole EQ on or off remains the one case that rewrites `asound.conf` and
restarts the consumers.

All browser values are treated as untrusted. The server accepts exactly ten
bands and known filter names, rejects NaN/infinity, and enforces the documented
ranges before writing. The root helper receives only the fixed
`apply_equalizer` action; it never accepts ALSA text or a command from HTTP.

### Changing the implementation

Keep the following synchronized when adding a filter type or changing a range:

1. `FILTER_TYPES`, validation and defaults in `equalizer_store.py`.
2. Numeric filter IDs and biquad equations in `radio_equalizer.c`.
3. Select options and browser response equations in `templates.py` / `app.js`.
4. ALSA control serialization and the runtime-file value order in
   `audio_hardware_apply.py` / `equalizer_store.py`. The `.rt` layout
   (`RUNTIME_MAGIC`, count) must match `RT_MAGIC` / `CONTROL_VALUES` in
   `radio_equalizer.c`.
5. This user guide and the tests.

The C plugin must remain allocation-free in its audio `run()` callback. Test
host-side logic with:

```sh
pytest -q tests/test_equalizer.py tests/test_buildroot_equalizer.py \
  tests/test_web_audio_hardware.py tests/test_web_routes.py tests/test_web_helper.py
ruff check .
mypy
```

The host tests validate settings, persistence, route generation and helper
dispatch, but they do not execute ARM DSP audio. After building an image, test
on the target with EQ enabled and disabled for every configured source. Confirm
that MPD remains alive, the radio status snapshot stays fresh, the physical
volume knob still controls hardware or `Radio Volume`, and boosted filters do
not clip at the chosen preamp setting.

## Related documentation

- [Web administration interface](web-interface.md) — login and all Audio-page actions.
- [Supported sound devices](sound-devices.md) — stable cards, mixers and I2S profiles.
- [Persistent data](persistent-data.md) — update and backup guarantees.
- [Buildroot appliance](buildroot.md) — package selection and target image.
- [Developing and contributing](development.md) — complete local validation workflow.