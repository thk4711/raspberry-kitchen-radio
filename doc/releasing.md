# Releasing (automated with `scripts/release.py`)

This is the end-to-end guide to cutting a Raspberry Kitchen Radio release with
the [`scripts/release.py`](../scripts/release.py) orchestrator. The script
automates the mechanical, repeatable tail of the release checklist in
[`CHANGELOG.md`](../CHANGELOG.md): it builds both firmware artifacts, stages them
under their published names, writes a `SHA256SUMS` file, and creates and uploads
the GitHub release with the [`gh`](https://cli.github.com/) CLI.

It deliberately does **not** replace the human judgement steps: bumping the
version, writing the changelog, creating the annotated Git tag, and validating a
trial firmware on real Pi 3A+ hardware all stay manual. Think of the script as
the "build, package, publish" step that runs *after* the release commit and tag
already exist.

## Contents

- [What the script automates](#what-the-script-automates)
- [Prerequisites](#prerequisites)
- [The one-line command](#the-one-line-command)
- [Command-line options](#command-line-options)
- [What each run does, step by step](#what-each-run-does-step-by-step)
- [Preparing the release-notes file](#preparing-the-release-notes-file)
- [Artifacts and naming](#artifacts-and-naming)
- [How it maps to the CHANGELOG checklist](#how-it-maps-to-the-changelog-checklist)
- [Draft then publish](#draft-then-publish)
- [Retrying safely](#retrying-safely)
- [Full worked walkthrough](#full-worked-walkthrough)
- [Troubleshooting](#troubleshooting)

## What the script automates

Given a release number `X.Y.Z` and a markdown notes file, `scripts/release.py`:

1. **Validates the working tree** describes exactly that release — the
   `__version__` in [`lib/_version.py`](../lib/_version.py), the `version` in
   [`pyproject.toml`](../pyproject.toml), and the top `## [X.Y.Z] - DATE`
   heading in [`CHANGELOG.md`](../CHANGELOG.md) must all equal `X.Y.Z`.
2. **Confirms tooling** — `git` and `gh` are installed and `gh` is authenticated
   for `github.com`.
3. **Requires the annotated tag** `vX.Y.Z` to exist (or creates it with
   `--create-tag`).
4. **Runs the consistency gate**
   [`scripts/check-release-consistency.py`](../scripts/check-release-consistency.py),
   which cross-checks versions, changelog date, artifact naming, generated
   firmware metadata, public-doc examples, and the Git tag type/date.
5. **Builds the SD card image and the `.swu`** through
   [`scripts/build_image.py`](../scripts/build_image.py) using the current
   [`scripts/build-image.ini`](../scripts/build-image.ini) (unless
   `--skip-build`).
6. **Stages the published assets** — copies the timestamped build outputs to
   their clean, version-named forms and writes `SHA256SUMS`.
7. **Creates and uploads the GitHub release** with `gh`, attaching the SD card
   image, the `.swu`, and `SHA256SUMS`.

What stays manual (human gates):

- Bumping `lib/_version.py` and `pyproject.toml`, and moving the `Unreleased`
  notes under a dated `## [X.Y.Z]` heading.
- Creating the annotated, correctly dated tag (the script only *verifies* it by
  default).
- The on-hardware A/B install / rollback test on a disposable Pi 3A+ card
  (checklist step 10).
- The final decision to publish (the script defaults to a **draft**).

## Prerequisites

- **A prepared release commit.** Before running the script, complete checklist
  steps 1–4 in [`CHANGELOG.md`](../CHANGELOG.md): bump the version in both
  places, move the `Unreleased` notes under a dated `## [X.Y.Z] - DATE` heading,
  record the pinned Buildroot revision, commit, and create the annotated tag:

  ```console
  $ git tag -a vX.Y.Z -m "vX.Y.Z"
  ```

- **`gh` installed and authenticated.** Install the GitHub CLI and log in once:

  ```console
  $ gh --version
  $ gh auth status --hostname github.com   # must show "Logged in"
  ```

  The account needs push/release rights on the repository
  (`thk4711/raspberry-kitchen-radio`).

- **`git` on `PATH`.**

- **A working `scripts/build-image.ini`.** The script does not build directly;
  it calls `scripts/build_image.py`, which reads this INI. Copy the example and
  fill in your local or remote build settings:

  ```console
  $ cp scripts/build-image.example.ini scripts/build-image.ini
  ```

  Local and remote (SSH) execution both work — see
  [`build-from-scratch.md`](build-from-scratch.md) and
  [`buildroot.md`](buildroot.md) for the build itself. The release script simply
  forwards `--config scripts/build-image.ini` and a few passthrough flags.

- **A release-notes markdown file.** You always pass this explicitly with
  `--notes` (see [Preparing the release-notes file](#preparing-the-release-notes-file)).

## The one-line command

From the repository root, with the release commit checked out and the annotated
tag created:

```console
$ python3 scripts/release.py X.Y.Z --notes path/to/notes.md
```

This validates, builds, stages, checksums, and creates a **draft** GitHub
release `vX.Y.Z` with the three assets attached. Review the draft, run the
on-hardware test, then publish (see [Draft then publish](#draft-then-publish)).

To see exactly what would happen without changing anything:

```console
$ python3 scripts/release.py X.Y.Z --notes path/to/notes.md --dry-run
```


## Command-line options

```
python3 scripts/release.py <version> --notes FILE.md [options]
```

| Option | Default | Purpose |
| --- | --- | --- |
| `version` (positional) | — | The release number `X.Y.Z`. Must match the version already recorded in `lib/_version.py`, `pyproject.toml`, and the top `CHANGELOG.md` heading. |
| `--notes FILE.md` | **required** | Markdown file whose contents become the GitHub release body. Must exist and be non-empty. |
| `--config CONFIG` | `scripts/build-image.ini` | INI forwarded to `build_image.py`. |
| `--artifacts-dir DIR` | `<repo>/artifacts` | Where build outputs live and where clean assets + `SHA256SUMS` are staged. |
| `--draft` | on | Create a draft release (safe default). |
| `--publish` | off | Publish immediately instead of a draft. Assumes the on-hardware test passed. |
| `--skip-build` | off | Reuse already-staged clean assets instead of running `build_image.py`. |
| `--create-tag` | off | Create the annotated tag `vX.Y.Z` (dated today) if it is missing. By default the tag must already exist. |
| `--force` | off | Overwrite existing staged assets and update an existing release/draft. |
| `--dry-run` | off | Print every action without building, tagging, staging, or publishing. |
| `--clean` | off | Forward `--clean` to `build_image.py` (clean rebuild). |
| `--fast` | off | Forward `--fast` to `build_image.py` (app-only rebuild). |
| `--jobs N` | auto | Forward `--jobs N` to `build_image.py`. |
| `--zip` / `--no-zip` | INI default | Force zipping (or not) the SD card image via `build_image.py`. |

## What each run does, step by step

1. **Argument and path resolution.** Validates that `version` is `X.Y.Z`, that
   the repository looks like a Raspberry Kitchen Radio checkout (has
   `buildroot/build.sh`), and that the `--notes` file exists and is non-empty.

2. **Version coherence.** Reads `lib/_version.py`, `pyproject.toml`, and the top
   `CHANGELOG.md` release heading and fails unless all three equal `version`.
   This catches "forgot to bump" and "released the wrong number" mistakes early:

   ```text
   release.py: ERROR: lib/_version.py is 0.3.0, not the requested 0.4.0; bump the
   version and update the changelog before releasing
   ```

3. **Tooling and auth.** Confirms `git` and `gh` are installed, then runs
   `gh auth status --hostname github.com`.

4. **Tag.** Verifies the annotated tag `vX.Y.Z` exists. With `--create-tag` it
   creates one (`git tag -a vX.Y.Z -m "vX.Y.Z"`) if missing; otherwise a missing
   tag is an error that points you back to the checklist.

5. **Consistency gate.** Runs `scripts/check-release-consistency.py`. This is the
   same check CI enforces, covering versions, changelog date, canonical artifact
   name, generated firmware metadata, public-doc examples, and the tag
   type/date.

6. **Build.** Unless `--skip-build`, runs
   `python3 scripts/build_image.py --config <config>` plus any passthrough flags.
   `build_image.py` produces timestamped artifacts in `--artifacts-dir`.

7. **Locate and stage assets.** Picks the newest timestamped SD card image and
   `.swu` for `version`, and copies each to its clean, published name (verifying
   the copy's SHA-256). Refuses to overwrite an existing, differing clean asset
   unless `--force`.

8. **Checksums.** Writes `SHA256SUMS` next to the assets, in the standard
   `<hex>␠␠<name>` format.

9. **Publish.** If no release exists for the tag, runs `gh release create` (with
   `--draft` unless `--publish`) and attaches the SD card image, the `.swu`, and
   `SHA256SUMS`. If a release/draft already exists, it updates the title/notes
   and re-uploads the assets with `--clobber` — but only under `--force`.

10. **Summary.** Prints the tag, each asset's name, size, and SHA-256, and the
    release URL. When publishing (not a draft) it prints a reminder that the
    on-hardware A/B test must already have passed.


## Preparing the release-notes file

The `--notes` file is required and is used verbatim as the GitHub release body.
There is no default location — you always pass an explicit path, which keeps the
notes under your control and reviewable in the pull request.

A simple, effective convention is a one-line summary followed by the relevant
`CHANGELOG.md` section for this release. For example:

```markdown
Raspberry Kitchen Radio vX.Y.Z for Raspberry Pi 3A+.

### Added
- ...

### Changed
- ...

### Fixed
- ...
```

Tips:

- Keep it self-contained; the release page is read outside the repository.
- Reference firmware packages generically as `kitchen-radio-<version>.swu` in
  prose, matching the rest of the documentation.
- The file must be non-empty or the script aborts before building.

## Artifacts and naming

`build_image.py` writes **timestamped** artifacts so repeated builds never
clobber each other, e.g.:

```text
kitchen-radio-X.Y.Z-<shorthash>-<YYYYMMDD>-<HHMMSS>-sdcard.img.zip
kitchen-radio-X.Y.Z-<shorthash>-<YYYYMMDD>-<HHMMSS>.swu
```

`release.py` then stages the newest matching pair to the **published** names and
generates the checksum file that ships with the release:

| Published asset | What it is |
| --- | --- |
| `kitchen-radio-<version>-sdcard.img.zip` | Zipped raw SD card image for fresh flashing. |
| `kitchen-radio-<version>.swu` | SWUpdate firmware package for A/B updates. |
| `SHA256SUMS` | SHA-256 of both assets, one `<hex>␠␠<name>` line each. |

These three files are attached to the GitHub release.

## How it maps to the CHANGELOG checklist

The release checklist lives as a comment at the bottom of
[`CHANGELOG.md`](../CHANGELOG.md). The script covers the mechanical middle;
the rest stays manual.

| Checklist step | Handled by |
| --- | --- |
| 1. Bump `lib/_version.py` and `pyproject.toml` | **Manual** (verified by the script) |
| 2. Move `Unreleased` notes under `## [X.Y.Z] - DATE` | **Manual** (verified by the script) |
| 3. Record pinned Buildroot revision + source hashes | **Manual** |
| 4. Commit and create annotated tag `vX.Y.Z` | **Manual** (verified; `--create-tag` optional) |
| 5. Run `check-release-consistency.py` | **`release.py`** |
| 6. Clean supported-host Buildroot build | **`release.py`** (via `build_image.py`; use `--clean`) |
| 7. Confirm one fresh image + matching `.swu` | **`release.py`** (newest pair; fails if ambiguous) |
| 8. SWUpdate check-mode acceptance for both slots | **Manual** (on hardware / in tests) |
| 9. Record and publish size + SHA-256 of both artifacts | **`release.py`** (`SHA256SUMS` + summary) |
| 10. On-hardware A/B, rollback, retained data test | **Manual** (cannot be automated) |
| 11. Push the release commit and tag | **Manual** (`git push origin main vX.Y.Z`) |

## Draft then publish

The default flow produces a **draft** so you can complete step 10 before making
the release public:

1. Run `python3 scripts/release.py X.Y.Z --notes notes.md` — creates the draft
   and uploads assets.
2. Open the draft on GitHub, verify the notes render and the three assets are
   attached.
3. Flash the `-sdcard.img.zip` to a disposable Pi 3A+ card and exercise the A/B
   install, healthy trial acceptance, automatic rollback, manual switch, and
   retained persistent data (checklist step 10).
4. Publish — either from the GitHub UI, or by re-running with `--publish`:

   ```console
   $ python3 scripts/release.py X.Y.Z --notes notes.md --skip-build --publish --force
   ```

   `--skip-build` reuses the already-staged assets; `--force` lets the script
   update the existing draft. When publishing, the script prints a reminder that
   the on-hardware test must have passed.

## Retrying safely

Re-runs are safe:

- `--skip-build` reuses the staged `kitchen-radio-<version>-sdcard.img.zip` and
  `kitchen-radio-<version>.swu` instead of rebuilding — handy when only the
  upload failed.
- `--force` overwrites staged assets and updates an existing release/draft
  (re-uploading with `--clobber`).
- `--dry-run` prints the exact `gh`, `git`, and build commands without side
  effects, so you can preview any run.

Without `--force`, the script refuses to overwrite a differing staged asset or a
release that already exists, so an accidental re-run cannot silently replace
published artifacts.


## Full worked walkthrough

Cutting release `X.Y.Z` from a clean working tree:

```console
# 1. Prepare the release commit (checklist steps 1–3).
$ sed -i '' 's/^__version__ = .*/__version__ = "X.Y.Z"/' lib/_version.py
$ $EDITOR pyproject.toml        # set version = "X.Y.Z"
$ $EDITOR CHANGELOG.md          # move Unreleased notes under "## [X.Y.Z] - DATE"
$ git add -A && git commit -m "Release X.Y.Z"

# 2. Create the annotated tag dated on the changelog date (checklist step 4).
$ git tag -a vX.Y.Z -m "vX.Y.Z"

# 3. Write the release notes.
$ $EDITOR /tmp/notes-X.Y.Z.md

# 4. Preview the whole release without side effects.
$ python3 scripts/release.py X.Y.Z --notes /tmp/notes-X.Y.Z.md --dry-run

# 5. Build, stage, checksum, and create the draft release.
$ python3 scripts/release.py X.Y.Z --notes /tmp/notes-X.Y.Z.md --clean

# 6. Test the draft's image on real Pi 3A+ hardware (checklist step 10).

# 7. Publish, reusing the staged assets.
$ python3 scripts/release.py X.Y.Z --notes /tmp/notes-X.Y.Z.md \
      --skip-build --publish --force

# 8. Push the release commit and tag (checklist step 11).
$ git push origin main vX.Y.Z
```

The successful run ends with a summary like:

```text
Release summary
  Tag:   vX.Y.Z (draft)
  Asset: kitchen-radio-X.Y.Z-sdcard.img.zip  176.9 MiB  <sha256>
  Asset: kitchen-radio-X.Y.Z.swu  86.6 MiB  <sha256>
https://github.com/thk4711/raspberry-kitchen-radio/releases/tag/vX.Y.Z
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `lib/_version.py is A.B.C, not the requested X.Y.Z` | The tree is not on the release you asked for. Bump `lib/_version.py`, `pyproject.toml`, and the changelog to `X.Y.Z` first. |
| `pyproject.toml version … does not match` / `latest CHANGELOG.md release … does not match` | The three version sources disagree. Make all three equal `X.Y.Z`. |
| `annotated tag vX.Y.Z does not exist` | Create it per the checklist (`git tag -a vX.Y.Z -m "vX.Y.Z"`), or pass `--create-tag` to let the script create it. |
| `gh is not authenticated for github.com` | Run `gh auth login` (or `gh auth refresh`) and retry. |
| `check-release-consistency.py: ERROR: …` | Fix the reported inconsistency (often a tag date not matching the changelog date, or a fixed-version `.swu` example in docs). |
| `no built SD card image / firmware .swu for X.Y.Z` | The build produced nothing matching the version, or you used `--skip-build` without staged assets. Re-run without `--skip-build`, or check the `build_image.py` output. |
| `refusing to overwrite existing …; pass --force` | A differing clean asset is already staged. Pass `--force` to replace it. |
| `a release for vX.Y.Z already exists; pass --force to update it` | A draft/release already exists (e.g. a hand-made draft). Pass `--force` to update it, or delete it first. |
| `checksum mismatch after staging …` | The copy did not match the source (rare I/O issue). Re-run; investigate the artifact if it persists. |

For the build itself (Buildroot, SSH remote builds, artifact contents), see
[`buildroot.md`](buildroot.md) and the `build_image.py` header. For the update
mechanism the `.swu` drives, see
[`firmware-updates.md`](firmware-updates.md) and
[`firmware-update-architecture.md`](firmware-update-architecture.md).

