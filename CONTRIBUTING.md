# Contributing to Raspberry Kitchen Radio

Thanks for your interest in improving the project! This guide covers the
**Python application** workflow. Building the Buildroot appliance image is a
separate topic — see [`doc/build-from-scratch.md`](doc/build-from-scratch.md)
and [`doc/buildroot.md`](doc/buildroot.md).

The developer reference lives in [`doc/development.md`](doc/development.md);
this file is the short version plus the contribution etiquette.

## Getting started

None of this needs a Raspberry Pi — the hardware/system-only libraries are
stubbed in [`tests/conftest.py`](tests/conftest.py), so the whole workflow runs
on macOS or Linux.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Before you open a pull request

Run the same checks CI enforces (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

```bash
ruff check .             # lint
ruff format --check .    # verify the enforced Python formatting baseline
mypy                     # static type check (all first-party production Python)
pytest -q                # the full test suite
```

Install the pre-commit hooks so all of the above (plus the buildroot shellcheck
and repository-hygiene gates) run automatically and locally, matching CI:

```bash
pip install pre-commit
pre-commit install                       # fast gates on every commit
pre-commit install --hook-type pre-push  # + full pytest/coverage before a push
```

The hooks are deliberately kept in lockstep with CI so a problem is caught on
your machine rather than in the pipeline:

- **`ruff` lint** includes **`FA102`**, which flags PEP 604 unions (`X | None`)
  used without `from __future__ import annotations` — that syntax is evaluated
  at import time and breaks on the **Python 3.9** appliance floor, even though
  it runs fine on a newer local interpreter.
- **mypy** checks all first-party production Python in `radio.py`, `lib/`,
  `radio_web/`, and `scripts/`. The vendored `lib/ADS1x15/` driver is excluded;
  hardware-only imports without host type stubs are treated as external.
- **shellcheck** runs the **same version CI uses (0.9.0)** via the pinned
  `koalaman/shellcheck:v0.9.0` Docker image, so it does not drift from the
  pipeline (a newer local shellcheck can miss findings CI still reports). This
  hook needs Docker; if you don't have it, CI still enforces the check.
- **Ruff format** runs on normal commits and CI verifies the result with
  `ruff format --check .`. Run `ruff format .` to fix formatting locally.
  Generic whitespace fixers remain opt-in (`stages: [manual]`) because they are
  not CI gates.

### Guidelines

- **Match the existing style.** `ruff` targets Python 3.9 (the appliance
  floor); do not introduce syntax that will not run there. Line length is 100.
- **Keep the standard-library-only constraint** in `radio_web/` — no web
  framework, database or Node.js.
- **Add or update tests** for any behaviour change. Coverage is enforced in CI
  with a floor, so new untested code can fail the build.
- **Update the docs.** User-facing behaviour changes belong in the relevant
  `doc/*.md` file; add an entry to the `## [Unreleased]` section of
  [`CHANGELOG.md`](CHANGELOG.md) (Keep a Changelog format).
- **Keep secrets and device-specific state out of the repo.**
  `scripts/check-repository.sh` (run in CI) fails the build if tracked build
  artifacts, credentials or CRLF line endings sneak in.
- **Write focused commits** with clear messages. The history uses
  Conventional-Commit-style prefixes (`feat:`, `fix:`, `tests:`, `docs:`) —
  please follow suit.

## Reporting bugs & requesting features

Open a GitHub issue using one of the templates in
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE). For anything that could be
a security issue, follow [`SECURITY.md`](SECURITY.md) instead.

## Code of conduct

Participation in this project is governed by the
[Contributor Covenant](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
