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
and the display compositor.

```bash
pytest
```

Test discovery and the import path are configured in
[`../pyproject.toml`](../pyproject.toml) (`tests/` is the test root and `lib/`
is added to `pythonpath`, so `from music_source import ...` resolves exactly as
it does at runtime, where `radio.py` does `sys.path.insert(0, .../lib)`).

To also see coverage:

```bash
pytest --cov=lib --cov-report=term-missing
```

## Linting, formatting and type checks

The same `requirements-dev.txt` installs [`ruff`](https://docs.astral.sh/ruff/)
(lint + format) and [`mypy`](https://mypy-lang.org/) (static types):

```bash
ruff check .          # lint
ruff format .         # (optional) auto-format
mypy                  # type-check the modules listed in pyproject.toml
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
    D --> E["pytest -q"]
    E --> F["compileall<br/>lib radio.py tests"]
    F --> G["sh -n<br/>(buildroot/*.sh)"]
    G --> H["shellcheck<br/>(buildroot/*.sh)"]
    H --> I["scripts/<br/>check-repository.sh"]
```

Run the Python-level checks locally before pushing to catch failures early:

```bash
ruff check . && mypy && pytest -q
```
