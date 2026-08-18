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

## 2026-08-17 — the four open decisions answered, and two non-rows became rows

The operator answered every item under "Open decisions" in the same session the catalog was
entered, so nothing in this slice is waiting on them any more. The answers are in the README
under "Decisions — resolved 2026-08-17"; what they changed is here.

**Databases are apps, not a platform singleton.** The question was framed as "one shared
backend or per-app containers", and the answer refused the frame: the operator wants to be
able to *pull a backend up*, sometimes one for everything and sometimes four for four apps.
That is exactly what the instance model already does, so no new mechanism is needed — a
backend is a row with an `app-defaults` file, and `config/apps/postgresql-immich.yml` is how
a lab gets a dedicated one. MariaDB and Redis were added on the same footing. The rule that
survives is narrower than the original decision: not "share one Postgres", but "no database
appears as a side effect of an app's compose file".

**Overlapping rows are the point.** nextcloud vs owncloud was written as a
pick-one decision. The operator read it the other way — jellyfin and plex already overlap,
and emby should be added as a third. So the catalog ships options and the lab chooses. The
defect the original wording was reaching for still stands, but it is about defaults, not
rows: nothing may deploy two equivalent apps on its own.

**GPU has two modes and the cheap one is the default.** ollama and comfyui get a dedicated
adapter. immich and frigate run on a shared iGPU, which only works if their guests are LXCs
— so frigate moved from Docker on VM to Docker on LXC. Its Coral is a USB device bind, the
same kind of passthrough. The operator's reasoning is worth keeping: taking a whole GPU into
a VM removes it from every other guest on the node, so it needs a real justification, not a
preference.

**SMTP is wanted and unscoped.** "we'll probably want some mail provider config... but IDK
about setup" — so the decision recorded is that mail is a platform-level contract rather
than four private app configs, and the provider and mechanism are explicitly left to the
first row that needs mail. That is a smaller commitment than the other three, and it is
recorded as such rather than dressed up as a design.

**opnsense is now a row.** It was entered as a non-row on the reasoning that the platform
wires to the lab's firewall and does not own it. That reasoning holds for *that* OPNsense
and is unchanged — slice 304 still wires to a firewall this platform did not create. What
the operator wants in addition is the ability to bring up an OPNsense VM, which is an
ordinary deploy of a new guest. Both facts are true at once, and the row says so, because
the failure mode to avoid is a future deploy adopting the running firewall.

**hermes agent is identified.** The operator supplied the URL; it resolves to
NousResearch/hermes-agent, MIT, a multi-channel agent platform with persistent memory. It is
a Batch C row on `ai_stack`. Its secret surface is the interesting part — a Nous portal key
plus a token per chat platform it bridges — and its own execution backends are its config,
not something this platform models.

The "Not application rows" section is gone: both of its entries left it, and an empty
section that exists to hold exclusions invites new ones.
