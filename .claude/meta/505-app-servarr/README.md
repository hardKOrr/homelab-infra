# 505 — Servarr app roles (Sonarr, Radarr, Lidarr, Readarr, Prowlarr)

**Status:** built
**Subject:** Media
**Related:** 504 (wire media stack) — this slice is what gives 504 something to wire

## Goal

Deploy a media app. Slice 504 shipped the whole app-to-app wiring layer and then had
nothing to run against: every media app in the lab was hand-installed, and the only way
into the registry was the discovery path (`app.media_kind` in an instance file). 504's own
closing note names the fix — "a `sonarr` role writing `media.<instance>` on deploy closes
the loop."

Five apps, one role. Sonarr, Radarr, Lidarr, Readarr and Prowlarr are the same program with
a different media type: same image family, same `/config` layout, same `config.xml`, same
`/api/<v>/system/status` contract. Everything that differs between them is already a row in
`vars/media-wiring.yml`, which 504 reads. Each app keeps its own playbook, its own
`app-defaults` file and its own one-click job — which is what "one click per app" is about —
while the mechanism lives in one place.

## What a deploy does

1. Finds or creates the `media_stack` host, attaches the lab's existing storage to it as
   Proxmox mountpoints, installs Docker, writes a compose file.
2. Declares the API key rather than discovering it afterwards: the key goes in as a Servarr
   configuration override, and the role then reads `config.xml` back and treats whatever is
   there as authoritative. That is version-proof — the override moved from `<APP>__APIKEY`
   to `<APP>__AUTH__APIKEY` across the v3→v4 line and an unrecognised one is ignored, so the
   role never trusts that its own setting took.
3. Asserts usability three ways, per the repo's rule that a deploy proves an app is usable
   and not merely alive: `config.xml` names an authentication method (not the first-run
   "choose one" state that serves a setup page on every UI path), the status endpoint
   answers with the app's own name under the key, and the same call **without** the key is
   refused.
4. Stores the key in `homelab-infra/media/<instance>` — the exact item name
   `wire-media-stack.yml` reads.
5. Writes `media.<instance>` (app, host, config_path — topology only) to the generated facts
   before wiring, then wires proxy / SSO / uptime / DNS as every app playbook does.

Run Wire Media Stack afterwards. App-to-app wiring is deliberately not done per-deploy:
these apps' peers are each other, so wiring pairwise from each deploy would have every app
re-wiring the whole stack.

## Storage: attached, never provisioned

The lab already has its library and downloads mounted on the Proxmox node. The platform's job
is to make them reachable, not to manage them: `config/infrastructure.yml` gains a
`media_storage` block naming the node paths and where guests should see them, and
`tasks/proxmox/attach-host-mounts.yml` attaches exactly those as mountpoints on the stack
host — additive, idempotent, matched on the (host path, guest path) pair, never removing or
rewriting a mountpoint that belongs to something else. Nothing is created, exported or
formatted, and a missing path fails with the path named rather than being conjured.

Host path and guest path are kept identical, and the container is given the same path again.
That is what lets a database migrated off another machine keep resolving its files with no
rewriting, and what keeps library and downloads in one mount namespace so imports are
hardlinks rather than copies.

An app contributes one line to this: `app.library_subpath`, the part of the library it owns.
The deploy configures that as the app's root folder over the API — a Servarr app without one
accepts nothing. The directory is asserted, never created; it is the lab's storage. An app
with no subpath (Prowlarr) gets no mounts and no root folder at all.

## Migration from an existing install

`playbooks/apps/migrate-servarr.yml` brings an existing Sonarr/Radarr/Lidarr/Readarr/Prowlarr
onto a lab instance. A Servarr app keeps history, the library with its file paths, quality
profiles, custom formats, indexers, download clients and its API key in one directory, so
migration is a directory copy — there is no separate "history only" export to do, because the
history is in the same database as everything else.

Non-destructive by construction: the source is read, never rewritten; its service is stopped
only for the copy (a SQLite database copied out from under a running writer is how you get a
corrupt one) and started again in an `always` block even when the copy fails; the old
instance stays running as a fallback. `MediaCover`, logs, `Backups` and Sentry are excluded —
regenerated or irrelevant, and the difference between a small transfer and a multi-gigabyte
one.

The check that makes it trustworthy is the last one: the source's root folder paths are read
before anything is stopped, and asserted to exist on the target. A database that expects its
library at a path the new host does not have produces an app that starts perfectly and
reports every item missing.

**Not ready for a live cutover.** The copy is sound; what follows it is not written. A
migrated database keeps its peer connections as addresses, `wire-media-stack.yml` locates
records by name and would duplicate rather than repair them, and the source keeps running —
two instances writing one library. The full audit, with the live evidence and the two pieces
that close it, is in [notes.md](notes.md). Safe against a throwaway instance; not as a
cutover.

The migrated `config.xml` carries the source's API key, and the role prefers a key it finds
over one it would generate — so the migrated instance keeps the key every existing Prowlarr
Application and Bazarr connection already points at.

## Security posture on record

The role configures `AuthenticationMethod: External` — the app runs no login of its own and
trusts the reverse proxy to have identified the user. That is why the default
`routing.identity` is **`forward_auth`**, not the platform-wide `catalog` default: with
External and no proxy-enforced identity, the UI would be open to anyone who can reach the
route. The two settings are coupled and the app-defaults files say so.

`AuthenticationRequired` is `Enabled`, not `DisabledForLocalAddresses`, so the API demands
its key from every caller including one on the stack host. The unauthenticated-call
assertion only means anything under that setting.

## Remaining

- [ ] One deploy observed live — the stack host created, the container up, all three
      usability assertions passing
- [ ] The two facts the role deliberately does not trust, confirmed against a running
      image: which `<APP>__AUTH__APIKEY` spelling the current LinuxServer tag honours, and
      that `AuthenticationRequired: Enabled` really does make an unkeyed `/api/v3/system/status`
      return 401. The role reads `config.xml` back rather than assuming either, so a wrong
      guess fails loudly at deploy time instead of shipping a wrong key or an open API
- [ ] A re-run reaching `changed=0`, with the API key unchanged
- [ ] Wire Media Stack run straight after a deploy, wiring the app from the registry entry
      rather than from a hand-written instance file — the leg 504 has never had
- [ ] A mountpoint attached to a stack host, and the guest restart that makes it visible
- [ ] One migration run end to end. The lab's own candidates are the three Radarrs on
      192.168.1.14/.15/.16 (2,589 and 2,556 films) and the Sonarrs on .12/.13, whose root
      folders sit under `/mnt/data/media` — an NFS export from 192.168.13.247 already
      mounted on pve-host-2 and passed into each LXC as `mp0`. Reproducing those mountpoints
      on the media stack host is what makes the migration a copy rather than a re-scan
- [ ] `readarr` pins the `develop` tag because Readarr has no current stable release; check
      before treating a Readarr deploy as supported

## Links

- `ansible/roles/servarr/` — the shared role
- `ansible/tasks/proxmox/attach-host-mounts.yml` — attaching existing node storage to a guest
- `ansible/playbooks/apps/migrate-servarr.yml`, `rundeck/jobs/migrate-servarr.yaml`
- `ansible/playbooks/apps/{sonarr,radarr,lidarr,readarr,prowlarr}.yml`
- `ansible/vars/app-defaults/{sonarr,radarr,lidarr,readarr,prowlarr}.yml`
- `rundeck/jobs/deploy-*.yaml`, `semaphore/project.json`
- [504](../504-wire-media-stack/README.md) — the wiring this slice feeds
- [notes.md](notes.md) — decisions and what is unverified
