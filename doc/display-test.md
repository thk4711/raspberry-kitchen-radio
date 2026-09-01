# Display smoke test

This repository includes a small standalone test for the 1.69" ST7789 SPI display:

```text
/opt/raspberry-kitchen-radio/lib/display_1_inch_69/display_test.py
```

The test initializes only the display, turns the backlight on, and draws a simple
image with color bars, borders, diagonals, and text. It does **not** start MPD,
AirPlay, Spotify, ADC polling, or the main radio controller.

## When to use it

Use this when the radio application boots but the display stays blank, or when
you want to separate a display/wiring/SPI problem from a larger application
startup problem.

## Wiring expected by the current config

Pin numbers in `display.conf` and in the Python code are **BCM** numbers. The
table also includes the Raspberry Pi 40-pin header physical pin numbers so you
can check the jumper wires directly.

| Display signal | Raspberry Pi signal | BCM pin | Physical pin |
| --- | --- | ---: | ---: |
| RST | GPIO output | 24 | 18 |
| DC | GPIO output | 25 | 22 |
| BL | GPIO/PWM output | 22 | 15 |
| DIN / MOSI | SPI0 MOSI | 10 | 19 |
| CLK / SCLK | SPI0 SCLK | 11 | 23 |
| CS / CE0 | SPI0 CE0 | 8 | 24 |
| VCC | 3V3 or module-specified supply | — | 1 or board-specific |
| GND | Ground | — | 6, 9, 14, 20, 25, 30, 34, or 39 |

### Step-by-step wiring

With the Raspberry Pi powered off:

1. Connect display **GND** to a Raspberry Pi ground pin, for example physical
   pin **6**.
2. Connect display **VCC** to the voltage required by your display module.
   Many ST7789 breakout boards are 3.3 V devices, so use physical pin **1**
   (**3V3**) unless your exact board explicitly requires 5 V.
3. Connect display **DIN**, sometimes labelled **SDA**, **MOSI**, or **SDI**, to
   Raspberry Pi physical pin **19** / BCM **10** / SPI0 **MOSI**.
4. Connect display **CLK**, sometimes labelled **SCL**, **SCK**, or **SCLK**, to
   Raspberry Pi physical pin **23** / BCM **11** / SPI0 **SCLK**.
5. Connect display **CS**, sometimes labelled **CE** or **LCD_CS**, to Raspberry
   Pi physical pin **24** / BCM **8** / SPI0 **CE0**.
6. Connect display **DC**, sometimes labelled **A0**, **D/C**, or **RS**, to
   Raspberry Pi physical pin **22** / BCM **25**.
7. Connect display **RST**, sometimes labelled **RES** or **RESET**, to
   Raspberry Pi physical pin **18** / BCM **24**.
8. Connect display **BL**, sometimes labelled **LED**, **BKL**, or **BLK**, to
   Raspberry Pi physical pin **15** / BCM **22**.

The display does not need SPI **MISO** for this driver because the app only
writes pixels to the panel.

### Raspberry Pi 40-pin header reference

This shows the relevant physical pins as viewed from above the Raspberry Pi,
with the USB/power connectors facing away from you. Odd-numbered pins are on
the left, even-numbered pins are on the right.

```text
  3V3   (1) (2)   5V
  SDA   (3) (4)   5V
  SCL   (5) (6)   GND        <- example display GND
 GPIO4  (7) (8)   GPIO14
  GND   (9) (10)  GPIO15
GPIO17 (11) (12)  GPIO18
GPIO27 (13) (14)  GND
GPIO22 (15) (16)  GPIO23     <- display BL
  3V3  (17) (18)  GPIO24     <- display RST
 MOSI  (19) (20)  GND        <- display DIN/MOSI
 MISO  (21) (22)  GPIO25     <- display DC
 SCLK  (23) (24)  CE0        <- display CLK/SCLK, display CS/CE0
  GND  (25) (26)  CE1
```

### Quick wiring checklist

- Confirm you are using **BCM 24/25/22** for `RST`/`DC`/`BL`, not physical pins
  24/25/22 by mistake.
- Confirm display **CS** is on **CE0** physical pin 24 / BCM 8.
- Confirm **MOSI** and **SCLK** are not swapped.
- Confirm the display has a common **GND** with the Raspberry Pi.
- Keep the SPI wires short while testing. If long jumper wires are unavoidable,
  use a lower SPI clock with `--spi-freq 10000000` or `--spi-freq 1000000`.
- If your display module has both `BL` and `VCC`, connect both as described;
  `BL` controls the backlight and is not a substitute for display power.

The script reads these values from:

```text
/opt/raspberry-kitchen-radio/lib/display_1_inch_69/display.conf
```

In the source tree this is:

```text
lib/display_1_inch_69/display.conf
```

`display.conf` also carries an optional **`[ui]` theme section** (colours,
safe-area geometry, font sizes, scrim opacity, OSD/toast/crossfade/idle timings
and an `animations` on/off switch) used by the radio app's now-playing UI — see
the documented keys in that file and the backdrop/contrast knobs in
[`logos.md`](logos.md#customizing-the-backdrop-via-ui).
Every key is optional; with the section absent the shipped defaults apply. To
preview a theme on the panel with the app stopped, run the smoke test's
now-playing mock: `display_test.py --mock-now-playing`.

## Prerequisites on the Buildroot image

The working Buildroot image should already have the required pieces enabled:

- SPI enabled in boot firmware config:

  ```text
  dtparam=spi=on
  ```

- Python modules:
  - `spidev`
  - `PIL` / Pillow
  - `numpy`
  - `gpiozero`
  - `RPi.GPIO`

## Run the test on the Raspberry Pi

Log in to the target as root, then stop the radio service so the main
application is not using the display at the same time:

```sh
/etc/init.d/S90radio stop
cd /opt/raspberry-kitchen-radio
python3 lib/display_1_inch_69/display_test.py
```

Expected result: the backlight turns on and the display shows a test image with
colored bars, white/orange borders, diagonals, and the text `DISPLAY TEST`.

The default display time is 60 seconds. To keep it on for longer:

```sh
python3 lib/display_1_inch_69/display_test.py --seconds 300
```

To draw the image and exit immediately:

```sh
python3 lib/display_1_inch_69/display_test.py --seconds 0
```

## Try a lower SPI clock

The app configuration currently uses a 40 MHz SPI clock. If the display remains
blank, flickers, or shows corrupted output, try a lower SPI clock. This can help
identify wiring/signal-integrity issues:

```sh
python3 lib/display_1_inch_69/display_test.py --spi-freq 10000000
```

You can also try 1 MHz for a very conservative check:

```sh
python3 lib/display_1_inch_69/display_test.py --spi-freq 1000000
```

If a low clock works but 40 MHz does not, the most likely cause is signal
integrity: loose jumper wires, long wires, poor ground, or marginal power.

## Try only the backlight

To test whether the backlight pin can be driven, run with a visible backlight
level:

```sh
python3 lib/display_1_inch_69/display_test.py --backlight 100 --seconds 30
```

To dim it:

```sh
python3 lib/display_1_inch_69/display_test.py --backlight 25 --seconds 30
```

If the backlight never turns on, check the display `BL` connection, power, and
ground before debugging SPI.

## Check SPI device availability

On the target, check that SPI device nodes exist:

```sh
ls -l /dev/spidev*
```

Expected for this app:

```text
/dev/spidev0.0
```

If `/dev/spidev0.0` is missing, check that the boot partition `config.txt`
contains:

```text
dtparam=spi=on
```

Then reboot.

## Interpreting results

### Test image appears correctly

The display hardware path is basically working:

- SPI is enabled.
- The display has power and ground.
- MOSI, SCLK, CE0, RST, DC, and BL are probably wired correctly.
- The Python display driver can initialize the ST7789.

If the main radio app still does not show anything, investigate the radio app
startup path, service logs, metadata rendering, or whether the power switch
logic turns the display backlight off.

Useful commands:

```sh
logread | grep -Ei 'radio|display|spi|gpio|error'
/etc/init.d/S90radio stop
cd /opt/raspberry-kitchen-radio
RADIO_LOG_LEVEL=DEBUG python3 radio.py
```

### Backlight turns on, but no image appears

Likely causes:

- SPI is not enabled or `/dev/spidev0.0` is missing.
- MOSI/SCLK/CE0 wiring is wrong.
- DC or RST wiring is wrong.
- SPI clock is too high for the wiring; retry with `--spi-freq 10000000` or
  `--spi-freq 1000000`.

### Nothing lights up

Likely causes:

- Display power or ground is missing.
- Backlight pin wiring is wrong.
- Display module expects a different backlight polarity or power connection.
- `gpiozero`/GPIO access is failing; run the command manually and check the
  printed error.

## Restart the radio app afterwards

After testing:

```sh
/etc/init.d/S90radio start
```