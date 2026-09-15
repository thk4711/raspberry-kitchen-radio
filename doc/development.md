# Developing and contributing

This is the home for working on the **Python application** in this repository:
setting up a local environment, running the tests, and passing the linters and
type checks. None of this needs a Raspberry Pi — the hardware/system-only
libraries are stubbed, so the whole developer workflow runs on your workstation
(macOS, Linux) and in CI.

> Building the appliance **image** is a separate topic; see
> [`build-from-scratch.md`](build-from-scratch.md) and [`buildroot.md`](buildroot.md).
> To add a new playback backend, see
> [`adding-a-music-source.md`](adding-a-music-source.md).
> To change the parametric-EQ DSP, ALSA route or graph, see the developer section
> of [`equalizer.md`](equalizer.md#developer-architecture).

## Local setup

The tests and tooling depend only on pure-Python packages, listed in
[`../requirements-dev.txt`](../requirements-dev.txt). Work inside a virtualenv:

```bash
cd raspberry-kitchen-radio
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

`requirements-dev.txt` installs the test/lint/type tooling (`pytest`,
`pytest-cov`, `ruff`, `mypy`) plus only the pure-Python runtime packages the
tested modules import directly (`pydantic`, `requests`, `numpy`, `Pillow`). It
deliberately does **not** pull in the Pi-only runtime pins from
[`../requirements.txt`](../requirements.txt).

The hardware/system-only libraries — `RPi.GPIO`, `alsaaudio`, `dbus`, `spidev`,
`gpiozero`, `smbus2`, and the vendored ADS1115 driver — are stubbed in
[`../tests/conftest.py`](../tests/conftest.py). That is why the suite runs on
any machine **without a Raspberry Pi attached**.

## Running the tests

The suite under [`../tests/`](../tests/) is [pytest](https://docs.pytest.org/)
covering the pure logic: the `MusicSource` contract and its runtime
return-type enforcement, the `radio.conf` INI parser, the AirPlay metadata
parsing/decoding, the ALSA volume mapping, the ADS1115 ADC calibration math,
the display compositor, parametric-EQ validation/routing, and Buildroot LADSPA
package integration.

```bash
pytest
```

Test discovery and the import path are configured in
[`../pyproject.toml`](../pyproject.toml) (`tests/` is the test root and `lib/`
is added to `pythonpath`, so `from music_source import ...` resolves exactly as
it does at runtime, where `radio.py` does `sys.path.insert(0, .../lib)`).

To also see coverage:

```bash
pytest --cov=lib --cov=radio_web --cov=radio --cov-report=term-missing
```

CI runs the suite with a **coverage floor** (`--cov-fail-under=65`), so a change
that drops overall coverage below that threshold fails the build.

## Linting, formatting and type checks

The same `requirements-dev.txt` installs [`ruff`](https://docs.astral.sh/ruff/)
(lint + format) and [`mypy`](https://mypy-lang.org/) (static types):

```bash
ruff check .          # lint
ruff format .         # (optional) auto-format
mypy                  # type-check the modules listed in pyproject.toml
python3 scripts/check-release-consistency.py  # release identity and tag policy
```

You can also install the [`pre-commit`](https://pre-commit.com/) hooks so these
run automatically on every commit (see
[`../.pre-commit-config.yaml`](../.pre-commit-config.yaml)):

```bash
pip install pre-commit
pre-commit install
```

- **ruff** targets Python 3.9 (`target-version = "py39"` in `pyproject.toml`),
  the oldest supported interpreter, so lint/format never suggest syntax the
  appliance image or the CI floor cannot run. The vendored `lib/ADS1x15` driver
  is excluded.
- **mypy** is being introduced incrementally: it currently gates the
  fully-typed, self-contained modules (its `files` list lives in
  `pyproject.toml`) and is widened as older modules gain type coverage. Its
  checker target is 3.10 while the runtime floor stays 3.9.

## Continuous integration

Every push and pull request runs [`../.github/workflows/ci.yml`](../.github/workflows/ci.yml)
across a Python version matrix (3.9, 3.11, 3.13). The same checks you can run
locally are enforced, plus repository-wide shell and hygiene checks:

```mermaid
flowchart LR
    A["Checkout + setup-python<br/>(3.9 · 3.11 · 3.13)"] --> B["pip install<br/>requirements-dev.txt"]
    B --> C["ruff check ."]
    C --> D["mypy"]
    D --> E["pytest -q<br/>(--cov-fail-under=65)"]
    E --> F["release consistency<br/>versions · metadata · tag"]
    F --> G["compileall<br/>lib radio.py tests"]
    G --> H["sh -n<br/>(buildroot/*.sh)"]
    H --> I["shellcheck<br/>(buildroot/*.sh)"]
    I --> J["scripts/<br/>check-repository.sh"]
```

Run the Python-level checks locally before pushing to catch failures early:

```bash
ruff check . && mypy && pytest -q
```

## The color-coded pinout HTML page

[`sound-devices.md`](sound-devices.md) is the Markdown source of truth for the
per-profile 40-pin maps. A color-coded HTML rendering is generated from it and
published to GitHub Pages at
<https://thk4711.github.io/raspberry-kitchen-radio/sound-devices.html>, where
each pin description is tinted by owning subsystem (SPI display, ADC, sound
card, amp GPIO, UART, free).

[`../scripts/generate-pinout-html.py`](../scripts/generate-pinout-html.py) reads
the `<!-- device:TAG -->` annotations in the Markdown and writes
[`sound-devices.html`](sound-devices.html):

```bash
python3 scripts/generate-pinout-html.py          # rebuild doc/sound-devices.html
python3 scripts/generate-pinout-html.py --check   # verify the HTML is up to date
```

After editing `sound-devices.md`, regenerate the HTML and commit both files.
[`../.github/workflows/pages.yml`](../.github/workflows/pages.yml) runs the
generator on every push to `main` and deploys the result to GitHub Pages.

## Developing the parametric equalizer

The EQ spans target C, generated ALSA configuration, persistent Python settings,
server-rendered HTML and browser response math. Start with
[`equalizer.md`](equalizer.md#developer-architecture), which documents the
component boundaries, invariants and synchronized changes required for filter
types and parameter ranges.

The focused host checks are:

```bash
pytest -q tests/test_equalizer.py tests/test_buildroot_equalizer.py \
  tests/test_web_audio_hardware.py tests/test_web_routes.py tests/test_web_helper.py
```

These do not replace on-device qualification. A host compiler can validate that
`radio_equalizer.c` is valid C, but the shipped module is cross-compiled for
ARMv7 by Buildroot and must be exercised through ALSA on the Pi. Verify all
music sources, EQ bypass, flat reset, the physical volume path, service recovery
after Apply, and positive-gain headroom before release.
