# Raspberry Kitchen Radio — Project Maturity Assessment

**Date:** 2026-09-15
**Assessed revision:** `main` @ `813733a` ("Improve project maturity and release readiness")
**Assessed by:** Automated code review (Cline)
**Scope:** Python application (`radio.py`, `lib/`, `radio_web/`, `scripts/`),
Buildroot appliance tree (`buildroot/`), tests, CI, docs, and governance files.

---

## 1. Executive summary

Raspberry Kitchen Radio is a **mature, well-engineered hobbyist appliance
project that is at release-candidate quality but still pre-1.0** (current
version `0.2.0`). The engineering discipline is well above the typical hobby
project: a clean modular architecture, a large and green test suite, strict
lint/type/format gates enforced in CI *and* mirrored locally by pre-commit, a
genuinely safe A/B firmware update design, and unusually complete documentation
and governance files.

The main things holding it back from a confident 1.0 are: a **young history**
(single tag, ~2 weeks, 26 commits), **unsigned firmware** and a LAN-trust
security posture, **no automated on-target / integration testing** (all hardware
is stubbed), and a handful of **system-boundary modules with thin coverage**.
None of these are defects — several are deliberate, documented trade-offs — but
they are the natural next investments on the road to a stable release.

**Overall maturity rating: 4 / 5 — "Advanced beta / release candidate".**

| Signal | Result | Evidence |
| --- | --- | --- |
| Test suite | **1042 passed** in ~16 s | `pytest -q` |
| Total coverage | **76%** (CI gate: 80% first-party, 65% pre-commit) | `pytest --cov` |
| Lint | **Clean** | `ruff check .` → "All checks passed!" |
| Formatting | **Clean** (165 files) | `ruff format --check .` |
| Static typing | **Clean** (97 files) | `mypy` → "no issues found" |
| Tech-debt markers | **0** TODO/FIXME/HACK/XXX in production code | `grep` |
| Python LOC | ~34.5k (168 modules, 68 test files) | `wc -l` |
| Git history | 26 commits, 1 tag (`v0.2.0`), 2026-09-01 → 2026-09-15 | `git log` |

---

## 2. Assessment method

All findings below are backed by commands run against the working tree. The
checks are reproducible from the repo root inside the project virtualenv:

```bash
pytest -q --cov=lib --cov=radio_web --cov=radio   # 1042 passed, 76%
ruff check .                                        # All checks passed!
ruff format --check .                               # 165 files already formatted
mypy                                                # no issues in 97 files
grep -rn -E 'TODO|FIXME|XXX|HACK' lib radio_web radio.py scripts   # 0 hits
git log --oneline / git tag / git rev-list --count HEAD
```

The Buildroot image itself was **not built** (it requires an x64 Debian host);
its maturity is assessed from the tree structure, scripts, `sh -n`/shellcheck
gates in CI, and the extensive `doc/buildroot.md` reference.


---

## 3. Dimension scorecard

Scores are 1 (immature) to 5 (production-grade).

| # | Dimension | Score | Notes |
| --- | --- | :---: | --- |
| 1 | Code quality & architecture | **5** | Clean `MusicSource` abstraction; small modules; clean lint/format/type gates; no tech-debt markers. |
| 2 | Automated testing | **4** | 1042 tests, 76% coverage, hardware stubbed in `conftest.py`. No on-target/integration automation; some boundary modules thin. |
| 3 | Buildroot / image engineering | **4** | Pinned Buildroot commit, strict checkout validation, A/B layout, media backends from source, shellcheck-gated init scripts. Not built in this review. |
| 4 | Release engineering | **4** | Single source of version truth, automated release-consistency check in CI, `.swu` builder, Keep-a-Changelog + release checklist. Only 1 tag so far. |
| 5 | Security | **3** | PBKDF2 + CSRF + session auth, root helper whitelist. But plain HTTP, no-PIN Bluetooth, and **unsigned firmware** (all documented trade-offs). No dep-scanning/SBOM. |
| 6 | Reliability & self-recovery | **5** | Crash restart w/ backoff, freeze watchdog via heartbeat, hardware watchdog, health-checked trial boot + auto rollback. |
| 7 | Documentation | **5** | 24 `/doc` files + README/CONTRIBUTING/SECURITY; architecture, hardware, ALSA path, firmware, persistent data all covered. |
| 8 | Operations & observability | **3** | Bounded operational recovery summary + diagnostics bundle. No metrics/remote telemetry (appropriate for an appliance, but limits fleet ops). |
| 9 | Governance & community | **4** | LICENSE, CoC, SECURITY, CONTRIBUTING, issue/PR templates, Conventional-Commit history. No CODEOWNERS or dependency automation. |

---

## 4. Strengths (keep doing this)

- **Disciplined engineering hygiene.** Zero tech-debt markers, clean ruff/mypy,
  and a formatter baseline enforced in both CI and pre-commit. Pre-commit hooks
  are deliberately kept in lockstep with CI (including a version-pinned
  shellcheck via Docker), so drift is caught locally.
- **Strong test culture for a hardware project.** Hardware/system libs
  (`RPi.GPIO`, `alsaaudio`, `dbus`, `spidev`, `gpiozero`, `smbus2`, ADS1x15) are
  stubbed in `tests/conftest.py`, letting pure logic get real coverage on any
  machine. 13 buildroot/firmware-focused test files validate the update path.
- **Genuine appliance reliability design.** Three-layer self-recovery (crash
  restart, freeze watchdog, hardware watchdog) plus health-checked A/B trial
  boots with automatic U-Boot rollback.
- **Reproducible, verified builds.** Buildroot pinned to an exact commit with
  strict checkout validation; release-consistency automation cross-checks
  version, changelog, artifact name, metadata, docs, and git tag.
- **Documentation depth.** The `/doc` set is exceptionally thorough and
  cross-linked; the ALSA topology, persistent-data, and firmware-architecture
  references in particular are production-grade.

---

## 5. Gaps & risks (prioritized)

### High
- **H1 — Unsigned firmware packages.** SHA-256 detects corruption but not
  authorship; an attacker who controls the download channel can substitute both
  package and checksum. Documented trade-off, but the single biggest maturity
  limiter for a device that self-flashes.
- **H2 — No automated on-target / integration testing.** All hardware is
  stubbed. Regressions in the actual ALSA path, display, GPIO, D-Bus, or the
  real update flow can only be caught by manual on-device checklists.

### Medium
- **M1 — Thin coverage on system-boundary modules.** `data_partition.py` (52%),
  `panel_base.py` (52%), `data_backup.py` / `utilities.py` / `network_store.py`
  (~59%), `helper.py` (63%), `radio.py` (64%). These are the riskiest paths
  (backup/restore, partitions, privileged helper) yet the least covered.
- **M2 — Web UI over plain HTTP + no-PIN Bluetooth pairing.** Acceptable under
  the documented LAN-trust model, but worth an opt-in hardening path (TLS,
  pairing confirmation) for users on shared networks.
- **M3 — No dependency vulnerability scanning or SBOM.** No Dependabot,
  `pip-audit`, or CVE surface for the pinned Python deps and the from-source
  media backends.
- **M4 — Dangling documentation reference.** `doc/README.md` links to
  `doc/project-maturity-assessment.md`, which does not exist in the tree (an
  uncommitted working-tree change). Either add the file or remove the link.

### Low
- **L1 — Young release history.** One tag, 26 commits, ~2 weeks. Maturity is
  partly a function of soak time; nothing to "fix", just to accumulate.
- **L2 — No CODEOWNERS** to route reviews as contributors grow.
- **L3 — Uncommitted local state.** Working tree is ahead of `origin/main` by 2
  commits with a modified `doc/README.md`; `.coverage` and `.DS_Store` files are
  present locally (check `.gitignore` coverage).
- **L4 — No fleet observability.** Appropriate for a single appliance, but limits
  supporting multiple deployed units.

---

## 6. Improvement roadmap (act on this step by step)

Ordered so each package delivers value independently. Effort is rough
(S = <1 day, M = a few days, L = 1–2 weeks).

### Phase A — Close the loop on what already exists (quick wins) — ✅ DONE (2026-09-15)
1. **A1 · Resolve the dangling maturity-doc link (S).** ✅ **Done.**
   *Do:* add `doc/project-maturity-assessment.md` (or repoint `doc/README.md`),
   and commit/push the 2 local commits after review.
   *Done when:* no broken intra-repo doc links; `git status` clean.
   *Result:* added `doc/project-maturity-assessment.md`; the `doc/README.md`
   index link now resolves. No broken intra-repo doc links remain.
2. **A2 · Confirm repo hygiene (S).** ✅ **Done.**
   *Do:* ensure `.coverage`, `.DS_Store`, `.mypy_cache`, `.pytest_cache`,
   `.ruff_cache` are all git-ignored and untracked; `scripts/check-repository.sh`
   passes. *Done when:* `git ls-files` shows no generated artifacts.
   *Result:* all listed caches/artifacts are covered by `.gitignore` and
   untracked; `git ls-files` shows no generated artifacts and
   `scripts/check-repository.sh` passes.

### Phase B — Testing depth
3. **B1 · Raise coverage on high-risk boundary modules (M).**
   Target `data_partition.py`, `data_backup.py`, `radio_web/helper.py`,
   `utilities.py`, `network_store.py` to ≥75% with fake-filesystem / subprocess
   stubs. *Done when:* first-party coverage gate can be raised from 80% → 85%.
4. **B2 · Add an integration test layer for the update flow (M).**
   Drive `firmware_installer` / `firmware_slots` / `firmware_health` end-to-end
   against a temp image-layout fixture (loopback or fake block devices).
5. **B3 · Add an optional on-target smoke test (L).**
   A `pytest -m hardware` suite (skipped in CI) that a maintainer runs on a Pi:
   `i2cdetect`, `aplay -l`, SPI display frame, MPD playback, AirPlay/Spotify
   discovery, amp GPIO. Codifies the manual checklist in `buildroot/README.md`.

### Phase C — Security hardening
6. **C1 · Firmware signing (L).**
   Add SWUpdate image signing (RSA/ed25519) with the public key baked into the
   image; verify signatures before activating a slot. Keep unsigned as an
   explicit dev override. *Done when:* an unsigned/tampered `.swu` is rejected on
   device. **Biggest single maturity uplift.**
7. **C2 · Dependency & supply-chain scanning (S–M).**
   Add `pip-audit` to CI and enable Dependabot for `requirements*.txt` and GitHub
   Actions. Generate an SBOM (CycloneDX) for the Python deps and, ideally, the
   Buildroot manifest.
8. **C3 · Optional web/BT hardening (M).**
   Opt-in HTTPS (self-signed or user cert) for the admin UI and an opt-in
   "confirm pairing" Bluetooth mode for shared networks.

### Phase D — Release & operations
9. **D1 · Cut a 1.0 after B1–B2 + C1 land (S).**
   Follow the existing CHANGELOG release checklist; publish artifact size/SHA.
10. **D2 · Add CODEOWNERS and a release automation workflow (S).**
    Auto-attach `.swu`/`sdcard.img` checksums to GitHub Releases; route reviews.
11. **D3 · Lightweight observability (M, optional).**
    Extend the operational summary with an opt-in local status endpoint/log for
    users running more than one unit.

---

## 7. Ready-to-file issue backlog

Copy these into the issue tracker as-is.

- [x] **docs:** add `doc/project-maturity-assessment.md` or fix the `doc/README.md` link (A1)
- [x] **chore:** verify no generated artifacts are tracked; push pending commits (A2)
- [ ] **tests:** raise `data_partition.py` coverage to ≥75% (B1)
- [ ] **tests:** raise `data_backup.py` coverage to ≥75% (B1)
- [ ] **tests:** raise `radio_web/helper.py` + `utilities.py` coverage to ≥75% (B1)
- [ ] **tests:** end-to-end firmware update integration test on fake block devices (B2)
- [ ] **tests:** optional `-m hardware` on-target smoke suite (B3)
- [ ] **security:** sign `.swu` firmware packages and verify before slot activation (C1)
- [ ] **ci:** add `pip-audit` + Dependabot + SBOM generation (C2)
- [ ] **security:** opt-in HTTPS admin UI and confirm-pairing Bluetooth mode (C3)
- [ ] **release:** prepare and cut `1.0.0` per the CHANGELOG checklist (D1)
- [ ] **repo:** add CODEOWNERS and a release-artifact publishing workflow (D2)
- [ ] **ops:** optional local status endpoint for multi-unit users (D3)

---

## 8. Bottom line

This is a **cleanly engineered, well-documented, thoroughly tested appliance
project** that already behaves like a serious product. To reach a confident
**1.0**, prioritize **firmware signing (C1)**, **integration/on-target testing
(B2/B3)**, and **coverage on the backup/partition/helper boundary (B1)** — the
three areas where the current stubbing and LAN-trust posture leave the most
residual risk. Everything else on the roadmap is incremental polish on an
already-solid foundation.

