# Security Policy

## Supported versions

This is a hobbyist appliance project. Security fixes are made on the `main`
branch; there is no long-term support for older tags. Always build from the
latest `main` for the newest fixes.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Instead, report privately using GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" under the repository's **Security** tab). Include:

- a description of the issue and its impact,
- steps to reproduce (a proof of concept if possible),
- the affected component (app, `radio_web`, Buildroot image, a media backend),
- any suggested remediation.

We will acknowledge the report, investigate, and coordinate a fix and
disclosure timeline with you.

## Threat model & deliberate design trade-offs

This device is designed for a **trusted home LAN**. Several choices are
intentional and are *not* considered vulnerabilities on their own — but reports
that show a way to exploit them beyond the documented scope are welcome:

- **Web administration UI (port 8080)** is served over **plain HTTP** on the
  LAN and protected by a single admin password (PBKDF2-HMAC, sessions +
  CSRF-protected mutating routes; the dashboard is public read-only). It is not
  intended to be exposed to the public internet. See
  [`doc/web-interface.md`](doc/web-interface.md).
- **Bluetooth A2DP** is discoverable and **auto-accepts pairing with no PIN**
  whenever nothing is connected — a deliberate kitchen-appliance convenience.
  See [`doc/bluetooth.md`](doc/bluetooth.md).
- **SSH (dropbear)** may be enabled with root login for development images.
- **Firmware update packages are unsigned.** Their embedded and displayed
  SHA-256 values detect accidental corruption but do not authenticate the
  publisher: an attacker could replace both a package and its checksum. Obtain
  `.swu` files through a trusted channel and upload them only over the trusted
  LAN described above. Authentication to the plain-HTTP web UI does not make an
  untrusted firmware package authentic. See
  [`doc/firmware-updates.md`](doc/firmware-updates.md).

### Why no HTTPS or firmware signing?

The plain-HTTP web UI and the unsigned firmware packages above are **deliberate
choices, not oversights**. Both HTTPS and a meaningful firmware signature
require a *cryptographic chain of trust* that this project cannot establish:

- **HTTPS needs a certificate a browser already trusts**, bound to a stable
  hostname or IP. This appliance is built by unknown people and deployed onto
  private home LANs with unknown, frequently changing IP addresses and
  hostnames, and it is never reachable from the public internet, so no
  certificate authority can issue a valid certificate for it. A self-signed
  certificate would only add browser warnings and key handling without adding
  real assurance on a network the operator already controls.
- **A useful firmware signature needs a signing key that verifiers already
  trust**, kept offline, with defined rotation and revocation. There is no
  shared trust anchor between the (unknown) publisher of a `.swu` and the
  (unknown) operator installing it, so a signature the device generated or
  accepted by default would prove nothing. The embedded SHA-256 therefore
  guards against accidental corruption only.

Because no shared trust anchor exists for this deployment model, serving the
admin UI over plain HTTP and shipping SHA-256-verified but unsigned packages
**on a trusted LAN** is the honest trade-off rather than security theatre.
Anyone who controls their own fleet — a single stable network with a private CA
and a protected signing key — can add real authenticity by enabling HTTPS in
front of the UI, turning on SWUpdate's `CONFIG_SIGNED_IMAGES`, and adopting
verified boot for the stable loader (see
[`doc/firmware-update-architecture.md`](doc/firmware-update-architecture.md)).

Issues we *do* want to hear about include: privilege-escalation past the
root-owned helper's fixed action whitelist, injection through the web form
validators, path traversal, secrets leaking into logs/diagnostics bundles, or
remote code execution.
