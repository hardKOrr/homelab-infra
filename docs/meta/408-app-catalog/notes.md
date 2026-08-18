# 408 — notes

## 2026-08-17 — catalog entered

The operator asked two things at once: what `servarr` represents, and whether the platform
covers roughly forty-five applications they run or want to run. The first answer is now in
the slice README because it is the rule the whole catalog follows; the second is the
catalog itself.

**Scope was set by the operator: catalog only.** No roles, no playbooks, no `app-defaults`
files were written this session. The batches exist so implementation order is decided once,
here, rather than re-argued each time somebody picks a row.

### What entering the list surfaced

Three findings that were not visible before the list was written down next to the repo:

1. **bazarr is wired but not deployable.** `ansible/tasks/app-wiring/bazarr-arr.yml` exists,
   and `ansible/vars/media-wiring.yml` declares a `bazarr` kind. There is no role, no
   playbook, no `app-defaults` file. 504 has wiring code for an app that cannot be
   installed, so that code has never executed. It is first in Batch A for that reason.
2. **`media-wiring.yml` already declares four kinds with no deploy path** — `bazarr`,
   `readarr`, `deemix`, `slskd`. The table was written ahead of the roles. `readarr` is
   nearly free: the `servarr` role already handles the v1 root-folder profile requirement.
3. **Two rows need a contract this platform does not have.** unpackerr and maintainerr are
   configured *from* the media registry rather than from their own file — they need every
   *arr's API key at deploy time. Every app so far reads its own config and writes one
   registry entry; these read the whole registry. Whoever implements the first of them is
   building a mechanism, not an app.

### Deliberate omissions

Ports, images and tags are not in the catalog. Writing them here would create a second home
for a fact `ansible/vars/app-defaults/<app>.yml` already owns, and the catalog's copy would
be the one nobody updates. The upstream project name is enough to start a row.

Nothing in the catalog is verified against upstream. Row notes state the shape each app is
expected to take (which backend, whether it has a UI, whether it needs GPU or passthrough);
each is a starting hypothesis for the implementer to confirm, not a measurement.

### Left open on purpose

Four decisions are listed in the README rather than resolved: the MariaDB backend, the
nextcloud/owncloud overlap, the GPU contract, and SMTP. Each affects several rows, so
deciding them inside a single app's implementation is how the lab would end up with four
MariaDBs and no SMTP story. `hermes agent` could not be identified at all and is recorded
as Unknown rather than guessed at.

`opnsense` was requested as an addition but is entered as a non-row: the platform already
wires to it as the DNS provider (slice 304), and it owns the network the platform runs on.
