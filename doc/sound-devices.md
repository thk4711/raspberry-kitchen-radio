# Supported sound devices

The web interface's **Audio** page (`/audio-hardware`) selects one audio-output
profile. Applying it updates the boot overlay, stable ALSA route, MPD mixer,
volume-knob control and optional application-controlled amplifier GPIO. The
change always requires a reboot.

This guide has a separate pinout for every selectable profile. For a plug-on
HAT, “used by the HAT” means the connection is made by the 40-pin stacking
header—do not add jumper wires. For a generic module, wire the listed signals
with the Pi switched off. GPIO numbers are **BCM** numbers; physical numbers are
the positions on the Pi header. All GPIO uses 3.3 V logic and is not 5 V
tolerant.

The machine-readable source of truth for profile labels, overlays, ALSA card
IDs, mixers and support levels is
`radio_web/audio_hardware_profiles.json`. The pin tables below additionally
document the physical wiring and conflicts that the catalog cannot express.
They are based on the overlays in the project's pinned Raspberry Pi 6.12 kernel;
power and speaker connections must also follow the documentation for the exact
board revision in hand.

## Choose a profile

- **Tested:** exercised by this project on target hardware.
- **Kernel-supported:** its overlay and drivers are present and verified while
  building the image, but the project has not physically tested every revision.
- **Experimental:** included in the image but requires special wiring or final
  on-device mixer verification.

| Profile | Overlay | ALSA card / volume | Level | Detailed pinout |
| --- | --- | --- | --- | --- |
| Built-in headphone jack | — | `Headphones` / `PCM` | Tested | [Pinout](#built-in-headphone-jack) |
| IQaudIO DAC+ / DAC Pro | `iqaudio-dacplus` | `IQaudIODAC` / `Digital` | Tested | [Pinout](#iqaudio-dac--dac-pro) |
| Generic PCM5102A / PCM510x | `hifiberry-dac` | `sndrpihifiberry` / `Radio Volume` | Kernel-supported | [Pinout](#generic-pcm5102a--pcm510x-compatible-dac) |
| Generic MAX98357A amplifier | `max98357a,no-sdmode` | `MAX98357A` / `Radio Volume` | Kernel-supported | [Pinout](#generic-max98357a-compatible-i2s-amplifier) |
| HiFiBerry DAC / DAC Zero / DAC+ Light | `hifiberry-dac` | `sndrpihifiberry` / `Radio Volume` | Kernel-supported | [Pinout](#hifiberry-dac--dac-zero--dac-light) |
| HiFiBerry DAC+ Standard | `hifiberry-dacplus-std` | `sndrpihifiberry` / `Digital` | Kernel-supported | [Pinout](#hifiberry-dac-standard) |
| HiFiBerry DAC+ Pro / DAC2 Pro | `hifiberry-dacplus-pro` | `sndrpihifiberry` / `Digital` | Kernel-supported | [Pinout](#hifiberry-dac-pro--dac2-pro) |
| HiFiBerry Amp2 / Amp4 | `hifiberry-dacplus-std` | `sndrpihifiberry` / `Digital` | Kernel-supported | [Pinout](#hifiberry-amp2--amp4) |
| HiFiBerry Amp / Amp+ (TI TAS5713) | `hifiberry-amp` | `sndrpihifiberryamp` / `Master` | Experimental | [Pinout](#hifiberry-amp--amp-ti-tas5713) |
| Allo BOSS | `allo-boss-dac-pcm512x-audio` | `BossDAC` / `Digital` | Kernel-supported | [Pinout](#allo-boss) |
| Audiophonics I-SABRE Q2M | `i-sabre-q2m` | `ISabreQ2MDAC` / `Digital` | Experimental | [Pinout](#audiophonics-i-sabre-q2m) |
| Allo Katana | `allo-katana-dac-audio` | `AlloKatana` / `Master` | Kernel-supported | [Pinout](#allo-katana) |
| JustBoom DAC / Amp | `justboom-dac` | `sndrpijustboomd` / `Digital` | Kernel-supported | [Pinout](#justboom-dac--amp-boards) |
| MERUS Amp piHAT ZW / InnoMaker Amp Pro | `merus-amp` | `sndrpimerusamp` / `A.Mstr Vol` | Experimental | [Pinout](#merus-amp-pihat-zw--innomaker-amp-pro) |

“Generic I2S” does not mean every I2S codec. The generic profiles cover only
PCM510x-compatible playback DACs and MAX98357A-compatible amplifiers. Other
codecs need a matching overlay and kernel driver.

## Device pinouts

Each table is a complete 40-pin map for that selected sound profile, including
the sound device and the rest of the radio. It is viewed from above the Pi with
the header vertical, USB connector at the bottom and HDMI connector on the left.
Pin 1 is top-left; odd pins run down the left and even pins down the right,
matching the conventional [Raspberry Pi pinout](https://pinout.xyz/).

The markers identify the Pi signal category and always include a text label:
🟥 **5V**, 🟧 **3V3**, ⬛ **ground**, 🟦 **I2C**, 🟪 **SPI**,
🟨 **I2S/PCM**, 🟩 **GPIO**, 🟫 **UART**, and ⬜ **reserved**. They are not
required jumper-wire colors. “Available” means not claimed by this documented
radio configuration; check electrically before adding other hardware.

The description text inside each cell is further color-coded by owning
subsystem in the generated HTML (`doc/sound-devices.html`):
`<!-- device:display -->` **SPI display** (blue) ·
`<!-- device:adc -->` **ADC / ADS1115** (amber) ·
`<!-- device:sound -->` **Sound card I2S/PCM** (green) ·
`<!-- device:amp-gpio -->` **Amp GPIO controls** (gold) ·
`<!-- device:uart -->` **UART** (red) ·
`<!-- device:free -->` **Free / available** (grey).
Run `python3 scripts/generate-pinout-html.py` to rebuild the HTML.



The normal display uses BCM 10/11/8 for SPI and BCM 24/25/12 for RST/DC/BL. The
ADS1115 uses I2C1 on BCM 2/3 at `0x48`. USB Audio does not consume a header pin.


<!-- audio-profile: headphones -->
### Built-in headphone jack

This is the shipped safe default. It uses the Pi's 3.5 mm connector and no
40-pin audio signals.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — <!-- device:free -->ADS1115 VDD; available; module use must match its manual | 🟧 **1** | **2** 🟥 | 5V — <!-- device:free -->available; module use must match its manual | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115 | 🟦 **3** | **4** 🟥 | 5V — <!-- device:free -->available; module use must match its manual | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115 | 🟦 **5** | **6** ⬛ | GND — common radio ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟩 | GPIO — <!-- device:free -->available | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — <!-- device:free -->available; module use must match its manual | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground | — |
| **19** | GPIO — <!-- device:free -->available | 🟩 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟩 | GPIO — <!-- device:free -->available | **20** |
| — | GND — common radio ground | ⬛ **39** | **40** 🟩 | GPIO — <!-- device:free -->available | **21** |

**Outside the 40-pin header:** connect the external amplifier or powered
speakers to the Pi's 3.5 mm headphone jack. This profile has no sound-device
header wiring.

**Radio compatibility:** the ST7789 and ADS1115 wiring remain unchanged. The
headphone output is line/headphone level; do not connect a passive speaker
directly to it. Expected card/mixer: `Headphones` / `PCM`.

<!-- audio-profile: iqaudio_dac -->
### IQaudIO DAC+ / DAC Pro

Fit the HAT to the complete 40-pin header. The overlay controls a PCM5122 at
I2C address `0x4c` and associates BCM 22 with amplifier mute.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with IQaudIO PCM5122 `0x4c` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with IQaudIO PCM5122 `0x4c` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:amp-gpio -->IQaudIO amplifier mute | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:amp-gpio -->radio external-amplifier enable | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Required radio action:** none. Expected card/mixer: `IQaudIODAC` / `Digital`.

<!-- audio-profile: generic_pcm510x -->
### Generic PCM5102A / PCM510x-compatible DAC

This playback-only profile uses the `hifiberry-dac` overlay. Module pin labels
vary; match signals, not connector position or wire color.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — <!-- device:free -->ADS1115 VDD; available; module use must match its manual | 🟧 **1** | **2** 🟥 | 5V — <!-- device:free -->available; module use must match its manual | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115 | 🟦 **3** | **4** 🟥 | 5V — <!-- device:free -->available; module use must match its manual | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115 | 🟦 **5** | **6** ⬛ | GND — common radio ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — <!-- device:free -->available; module use must match its manual | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |

**Module wiring:** connect `BCK`/`BCLK` to physical pin 12, `LCK`/`LRCK`/`WSEL`
to pin 35, `DIN` to pin 40, and module ground to any ground pin. The overlay
does not supply a separate `SCK` system clock; use a module that derives its
clocks from BCLK/LRCK. Connect `VIN` only to the 3.3 V or 5 V rail specified by
the exact module. Mute/enable strapping is module-specific and is not managed by
this profile.

**Radio compatibility:** display and ADS1115 wiring remain unchanged; no I2C
audio device is created. `amp = none`, so BCM 26 remains available but is not
driven. Connect the DAC's analog line output to an amplifier, not directly to a
passive speaker. Expected card/mixer: `sndrpihifiberry` / `Radio Volume`.

<!-- audio-profile: generic_max98357a -->
### Generic MAX98357A-compatible I2S amplifier

This is a mono digital-input power-amplifier profile. The selected overlay uses
`no-sdmode`, so Linux does not drive an enable/shutdown GPIO.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — <!-- device:free -->ADS1115 VDD; available; module use must match its manual | 🟧 **1** | **2** 🟥 | 5V — <!-- device:free -->available; module use must match its manual | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115 | 🟦 **3** | **4** 🟥 | 5V — <!-- device:free -->available; module use must match its manual | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115 | 🟦 **5** | **6** ⬛ | GND — common radio ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — <!-- device:free -->available; module use must match its manual | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |

**Module wiring:** connect `BCLK` to physical pin 12, `LRC`/`LRCLK` to pin 35,
`DIN` to pin 40, and module ground to any ground pin. Connect `VIN` only to the
supply documented for the exact module. The selected `no-sdmode` overlay does
not drive `SD`/`SD_MODE`; strap it so the module is enabled. Connect a suitable
speaker only across the module's bridged `+` and `−` outputs—neither speaker
terminal is ground.

**Radio compatibility:** display and ADS1115 wiring remain unchanged. The
overlay's usual BCM 4 SD_MODE default is deliberately disabled by
`no-sdmode`; BCM 4 is free. `amp = none`. Expected card/mixer:
`MAX98357A` / `Radio Volume`.

<!-- audio-profile: hifiberry_dac -->
### HiFiBerry DAC / DAC Zero / DAC+ Light

Fit the HAT to the 40-pin header. These playback-only PCM5102A-family boards use
the same overlay as the generic PCM510x profile and do not need I2C codec
control.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115 | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115 | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Radio compatibility:** display and ADS1115 wiring remain unchanged; no I2C
audio address is added by the overlay. `amp = none`; use the board's line output
with an external amplifier where required. Expected card/mixer:
`sndrpihifiberry` / `Radio Volume`.

<!-- audio-profile: hifiberry_dacplus_std -->
### HiFiBerry DAC+ Standard

Fit the HAT to the 40-pin header. The overlay controls its PCM5122 on I2C1 at
address `0x4d`.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with PCM5122 `0x4d` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with PCM5122 `0x4d` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Radio compatibility:** display wiring remains unchanged. The ADS1115 at
`0x48` can share I2C1 with the codec at `0x4d`. `amp = none`. Expected
card/mixer: `sndrpihifiberry` / `Digital`.

<!-- audio-profile: hifiberry_dacplus_pro -->
### HiFiBerry DAC+ Pro / DAC2 Pro

Fit the HAT to the 40-pin header. This profile uses the Pro clock overlay and a
PCM5122 on I2C1 at address `0x4d`.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with PCM5122 `0x4d` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with PCM5122 `0x4d` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Radio compatibility:** display wiring remains unchanged and the ADS1115 at
`0x48` can share I2C1. `amp = none`. Expected card/mixer:
`sndrpihifiberry` / `Digital`.

<!-- audio-profile: hifiberry_amp2 -->
### HiFiBerry Amp2 / Amp4

Fit the amplifier HAT to the 40-pin header and follow its manual for the
external supply and speaker load. This selectable profile uses the Standard
PCM5122 overlay at I2C address `0x4d`; the HAT/driver manages the amplifier, not
the radio's separate BCM 26 output.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with PCM5122 `0x4d` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with PCM5122 `0x4d` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Radio compatibility:** display wiring remains unchanged and the ADS1115 at
`0x48` can share I2C1. This profile sets `amp = none`; BCM 26 is not the HAT's
enable control. Expected card/mixer: `sndrpihifiberry` / `Digital`.

<!-- audio-profile: hifiberry_amp -->
### HiFiBerry Amp / Amp+ (TI TAS5713)

Fit the amplifier HAT to the 40-pin header and follow its manual for the
external speaker supply and speaker load. The `hifiberry-amp` overlay
instantiates a Texas Instruments TAS5713 Class-D amplifier on I2C1 at address
`0x1b`, which does not conflict with the ADS1115 at `0x48`. The card is driven
by the Raspberry Pi simple soundcard driver, and the TAS5713 codec provides the
hardware `Master` volume control used for the knob and MPD.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with TAS5713 `0x1b` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with TAS5713 `0x1b` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Radio compatibility:** display wiring remains unchanged and the ADS1115 at
`0x48` can share I2C1 with the TAS5713 at `0x1b`. This profile sets `amp = none`;
BCM 26 is not the HAT's enable control. Expected card/mixer:
`sndrpihifiberryamp` / `Master`.

**Format note:** the profile ships the safe `S16_LE` / 44100 Hz output. The
TAS5713 DAI also advertises `S24_LE`/`S32_LE` and rates up to 48 kHz, but these
have not been verified on target; test them on real hardware before changing the
`output_format` / `output_rate` fields in
`radio_web/audio_hardware_profiles.json`.

<!-- audio-profile: allo_boss -->
### Allo BOSS

Fit the HAT to the 40-pin header. The overlay controls a PCM5122 at I2C address
`0x4d` and claims BCM 6 as active-low mute.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with PCM5122 `0x4d` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with PCM5122 `0x4d` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:amp-gpio -->Allo BOSS mute, active low | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Radio compatibility:** the shipped display does not use BCM 6 and can remain
unchanged. The ADS1115 at `0x48` can share I2C1. `amp = none`. The BOSS output is
line level and needs an amplifier or powered speakers. Expected card/mixer:
`BossDAC` / `Digital`.

<!-- audio-profile: audiophonics_i_sabre_q2m -->
### Audiophonics I-SABRE Q2M

This profile is specifically for the Audiophonics **I-SABRE Q2M** supported by
the pinned kernel, not for every board sold under the I-SABRE name. In
particular, do not use it for an ES9023 I-SABRE V3/V4. The Q2M control interface
occupies I2C address `0x48`, which conflicts with the radio's default ADS1115.
Before fitting or selecting this profile, strap the ADS1115 `ADDR` pin for an
unused address—for example `ADDR` to VDD selects `0x49`—and change
`[adc] i2c_address` in `/opt/raspberry-kitchen-radio/radio.conf` to match.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted board as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted board as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — I-SABRE `0x48`; relocated ADS1115 (for example `0x49`) | 🟦 **3** | **4** 🟥 | 5V — used by fitted board as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — I-SABRE `0x48`; relocated ADS1115 | 🟦 **5** | **6** ⬛ | GND — common radio ground and board ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and board ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and board ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted board as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and board ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and board ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and board ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and board ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and board ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |

**Radio compatibility:** the display wiring remains unchanged, but the ADS1115
address change is mandatory. Confirm both devices with `i2cdetect -y 1` before
starting the player. The profile sets `amp = none`; connect the board's analog
output and power exactly as its revision's manual specifies. The kernel exposes
`Digital Playback Volume` as ALSA simple control `Digital`, with attenuation
from -100 dB to 0 dB, plus `Digital Playback Switch`. Expected card/mixer:
`ISabreQ2MDAC` / `Digital`. This remains experimental until that generated ALSA
card ID and mixer operation are confirmed on the project's target hardware.

<!-- audio-profile: allo_katana -->
### Allo Katana

The Katana is an ESS ES9038Q2M stack and uses I2C address `0x30`. Its overlay
makes the DAC the I2S bit/frame-clock producer and sets shared I2C1 to 50 kHz.
Follow Allo's manual for the selected output stage, stack arrangement and 5 V
power supply; do not infer power wiring solely from this signal map.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted Katana stack as required | 🟧 **1** | **2** 🟥 | 5V — used by Katana stack as documented by Allo | — |
| **2** | <!-- device:adc -->I2C1 SDA at 50 kHz — ADS1115 `0x48`; Katana `0x30` | 🟦 **3** | **4** 🟥 | 5V — used by Katana stack as documented by Allo | — |
| **3** | <!-- device:adc -->I2C1 SCL at 50 kHz — ADS1115 `0x48`; Katana `0x30` | 🟦 **5** | **6** ⬛ | GND — common radio ground and Katana ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and Katana ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — supplied by Katana to Pi | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and Katana ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted Katana stack as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and Katana ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and Katana ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and Katana ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and Katana ground | — |
| **19** | <!-- device:sound -->PCM FS — supplied by Katana to Pi | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and Katana ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to Katana | **21** |

**Radio compatibility:** display wiring remains unchanged and the ADS1115 has
no address conflict, but its controls must be qualified at the overlay's 50 kHz
I2C rate. The profile sets `amp = none`. The kernel exposes stereo
`Master Playback Volume` as ALSA simple control `Master` (-127.5 dB to 0 dB),
plus `Master Playback Switch`. Expected card/mixer: `AlloKatana` / `Master`.

<!-- audio-profile: justboom_dac_amp -->
### JustBoom DAC / Amp boards

Fit the board to the 40-pin header. The selected overlay controls a PCM5122 at
I2C address `0x4d`. Follow the exact DAC or Amp board manual for line output,
external power and speaker wiring.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with PCM5122 `0x4d` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with PCM5122 `0x4d` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟫 | <!-- device:uart -->UART0 TX — no external radio connection | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟫 | <!-- device:uart -->UART0 RX — no external radio connection | **15** |
| **17** | GPIO — <!-- device:free -->available | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:free -->available | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟪 | SPI0 CE0 — <!-- device:display -->display CS | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — <!-- device:free -->available | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Radio compatibility:** display wiring remains unchanged and the ADS1115 at
`0x48` can share I2C1. The profile sets `amp = none`; board amplification is not
switched by BCM 26. Expected card/mixer: `sndrpijustboomd` / `Digital`.

<!-- audio-profile: merus_amp -->
### MERUS Amp piHAT ZW / InnoMaker Amp Pro

This profile is experimental because the overlay has several GPIO assignments
and requires a display wiring change. It controls the amplifier at I2C address
`0x20`.

| BCM | Signal / radio connection | Physical pin (odd) | Physical pin (even) | Signal / radio connection | BCM |
| ---: | --- | ---: | :--- | --- | :--- |
| — | 3V3 — ADS1115 VDD; used by fitted HAT as required | 🟧 **1** | **2** 🟥 | 5V — used by fitted HAT as required | — |
| **2** | <!-- device:adc -->I2C1 SDA — ADS1115; shared with MERUS amplifier `0x20` | 🟦 **3** | **4** 🟥 | 5V — used by fitted HAT as required | — |
| **3** | <!-- device:adc -->I2C1 SCL — ADS1115; shared with MERUS amplifier `0x20` | 🟦 **5** | **6** ⬛ | GND — common radio ground and HAT ground | — |
| **4** | GPIO — <!-- device:free -->available | 🟩 **7** | **8** 🟩 | GPIO — <!-- device:amp-gpio -->MERUS amplifier enable, active high | **14** |
| — | GND — common radio ground and HAT ground | ⬛ **9** | **10** 🟩 | GPIO — <!-- device:amp-gpio -->MERUS amplifier mute, active high | **15** |
| **17** | GPIO — <!-- device:amp-gpio -->MERUS booster control, active high | 🟩 **11** | **12** 🟨 | <!-- device:sound -->PCM CLK — sound-device bit clock | **18** |
| **27** | GPIO — <!-- device:free -->available | 🟩 **13** | **14** ⬛ | GND — common radio ground and HAT ground | — |
| **22** | GPIO — <!-- device:free -->available | 🟩 **15** | **16** 🟩 | GPIO — <!-- device:amp-gpio -->MERUS error input, active high | **23** |
| — | 3V3 — used by fitted HAT as required | 🟧 **17** | **18** 🟩 | GPIO — <!-- device:display -->display RST | **24** |
| **10** | SPI0 MOSI — <!-- device:display -->display DIN / MOSI | 🟪 **19** | **20** ⬛ | GND — common radio ground and HAT ground | — |
| **9** | SPI0 MISO — <!-- device:display -->unused; display is write-only | 🟪 **21** | **22** 🟩 | GPIO — <!-- device:display -->display DC | **25** |
| **11** | SPI0 SCLK — <!-- device:display -->display CLK / SCLK | 🟪 **23** | **24** 🟩 | GPIO — <!-- device:amp-gpio -->MERUS overlay; **not display CS** | **8** |
| — | GND — common radio ground and HAT ground | ⬛ **25** | **26** 🟪 | SPI0 CE1 — display CS; `spi_device = 1` | **7** |
| **0** | ID_SD — reserve for HAT ID EEPROM | ⬜ **27** | **28** ⬜ | ID_SC — reserve for HAT ID EEPROM | **1** |
| **5** | GPIO — <!-- device:free -->available | 🟩 **29** | **30** ⬛ | GND — common radio ground and HAT ground | — |
| **6** | GPIO — <!-- device:free -->available | 🟩 **31** | **32** 🟩 | GPIO — <!-- device:display -->display BL | **12** |
| **13** | GPIO — <!-- device:free -->available | 🟩 **33** | **34** ⬛ | GND — common radio ground and HAT ground | — |
| **19** | <!-- device:sound -->PCM FS — sound-device frame / word clock | 🟨 **35** | **36** 🟩 | GPIO — <!-- device:free -->available | **16** |
| **26** | GPIO — <!-- device:free -->available; not driven by profile | 🟩 **37** | **38** 🟨 | <!-- device:sound -->PCM DIN — reserved with I2S; unused for playback | **20** |
| — | GND — common radio ground and HAT ground | ⬛ **39** | **40** 🟨 | <!-- device:sound -->PCM DOUT — Pi audio data to sound device | **21** |


**Required radio action:** move only display CS from BCM 8 / physical pin 24 to
BCM 7 / physical pin 26 and set `spi_device = 1` under `[display]` in
`lib/display/display.conf`; MOSI and SCLK remain BCM 10/11. See
[`display-test.md`](display-test.md#alternative-chip-select-for-a-merus-amp).
The ADS1115 at `0x48` can share I2C1. The profile sets `amp = none`; the kernel
driver uses the GPIOs above. Expected card/mixer: `sndrpimerusamp` /
`A.Mstr Vol`.

## Stable routing and software volume

Every profile uses a stable ALSA card ID rather than `hw:0`, whose number can
change when HDMI or the USB Audio gadget is present. Boards without a hardware
mixer are wrapped in an ALSA `softvol` PCM named `Radio Volume`, placed over
`dmix` and the stable card. MPD, AirPlay, Spotify, Bluetooth and the USB Audio
bridge all use ALSA `default`, so the physical knob controls every source.

When enabled on the web Audio page, a project-owned LADSPA biquad equalizer wraps
that common `default` route before the existing volume/output stages. Its ten
bands and preamp therefore affect MPD, AirPlay, Spotify, Bluetooth and USB Audio
consistently without changing the selected stable card or mixer control.
See [Parametric equalizer](equalizer.md) for the complete signal chain, controls,
headroom guidance and target troubleshooting.

## On-device qualification

After selecting the profile and rebooting, run:

```sh
cat /proc/asound/cards
aplay -l
aplay -L
amixer -c <card-id> scontrols
dmesg | grep -Ei 'snd|soc|i2s|codec'
```

Confirm the stable card ID and mixer stated in that device's section. For a
softvol profile, start playback once if necessary so ALSA creates the control,
then run `amixer sget 'Radio Volume'`. Test the physical knob and every enabled
source, then verify the display, ADS1115 controls, amplifier mute/enable and a
clean reboot. If the actual card or mixer differs, return to the built-in
headphone profile before changing the catalog; never substitute a numeric card
index.

## Image-time verification

`board/radio/post-image.sh` runs:

```sh
python3 scripts/verify-audio-catalog.py <buildroot-output-directory>
```

The image build fails if the catalog is invalid, an overlay is absent from the
firmware image, or a required kernel symbol is disabled. The positive-only
`linux-i2s-audio.fragment` records those driver requirements. The documentation
test also requires every catalog profile to have one matching marker and one
pinout section in this guide.