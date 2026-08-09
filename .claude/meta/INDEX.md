# Meta Index

The work queue. This file stays a table — prose belongs in [LESSONS.md](LESSONS.md),
per-session narrative in a slice's own `notes.md`, slice shape in [README.md](README.md).

**26 live · 23 archived in [done/](done/).** Statuses: `open` = not started, abandoned, or
reopened by evidence. `built` = code written, both gates green, acceptance not yet observed
live. Archived slices are finished; do not reopen without a new slice.

Gates (both green): `wsl bash -lc 'bash .claude/gate/lint.sh'` and `.claude/gate/test.sh`.

## Start here

| Order | Do this | Slices |
|---|---|---|
The plumbing comes first on purpose: Ntfy, Authentik authenticating everywhere and real
metrics are things the lab does not have today, and every app that follows rides on them.

| Order | Do this | Slices |
|---|---|---|
| 1 | The browser legs, in one sitting at a browser: Vaultwarden vault CRUD, Grafana and Authentik sign-in, one app behind `forward_auth`. | 400, 403, 405, 306 |
| 2 | Resume the network work the Vaultwarden detour interrupted: migrate the lab's `config/proxmox.yml` onto declared pools, deploy one guest from a pool and one from a pin, then wire OPNsense + Unbound. | 011, 304 |
| 3 | Deploy the media stack: Prowlarr plus one *arr onto `media_stack`, migrate one existing Radarr onto it, then Wire Media Stack. Closes 505's live legs and 504's last two. | 505, 504 |

**Parked, deliberately.** 016's Admin → Manager demotion is hardening on a path that already
works. It is researched and shipped; the browser leg waits until the estate is stable.

## By subject

One subject spans several slices, because slices are cut on the code axis (app role /
wiring task / bootstrap play). Look up the subject, then read only those slices.

| Subject | Slices |
|---|---|
| Vaultwarden | app **400**, secret store **014**, token capture **013**, identities **016** |
| Caddy / TLS | wiring **300**, DNS-01 **407**, wildcard bootstrap **015** |
| Authentik / identity | app **403**, wiring **302**, forward_auth **306**, modes **009** |
| Config model | provenance **010**, onboarding **012**, estates **008** |
| Networking | IP allocation **011** |
| Media | apps **505**, wiring **504** |
| Runners / UI | Rundeck **601**, Semaphore **600** |
| Day-2 ops | watchtower **201**, remove **501**, rollback **502** |
| No live target | nginx **301**, pihole **305** |

## Open (1)

Design defects in shipped code, or gaps between the documented model and the implemented
one. None are new features.

| # | Slice | What is unresolved |
|---|---|---|
| 015 | [Caddy-first wildcard HTTPS](015-wildcard-dns-default/README.md) | Certificate model observed live 2026-08-06. Open on internal mode, the no-API-provider handoff, the resume item, and migrating an already-serving estate without interrupting HTTPS. |

## Built — awaiting live acceptance (25)

Code-complete and gate-verified. Each row names the unobserved leg.

| # | Slice | What live acceptance needs |
|---|---|---|
| 008 | [Estate / multi-domain contract](008-estate-contract/README.md) | a second-domain deploy |
| 009 | [Identity modes (routing.identity)](009-identity-modes/README.md) | one app deployed per mode |
| 010 | [Config provenance](010-config-provenance/README.md) | the bootstrap script run on a node — the Config job group is verified on the workstation; nothing the script does is |
| 011 | [IP allocation model](011-ip-allocation-model/README.md) | one deploy allocating from a pool and one from a pin, plus the lab's `config/proxmox.yml` migrated off the invented `ip_offset: 2305` fence — a human edit on the runner, not a repo change |
| 012 | [Runner onboarding](012-runner-onboarding/README.md) | Configure App / Get Config from the UI, `LAB_REFRESH=0`, a no-op script re-run, the root README |
| 013 | [Vaultwarden token self-capture](013-vaultwarden-token-capture/README.md) | sink write/readback proved live; HTTPS, identities and item CRUD belong to 015/016/014 |
| 014 | [Vaultwarden secret store](014-vaultwarden-secret-store/README.md) | only the runner rebuild from Key Storage — seed-file recreation and seed re-entry were both injected and refused, 2026-08-06 |
| 016 | [Vaultwarden identities + keyring](016-vaultwarden-identity-bootstrap/README.md) | the automation account lowered from Admin to **Manager** in the web vault's Members list, then one Deploy job succeeding on the explicit collection grant. Vaultwarden hardcodes `allowAdminAccessToAllCollectionItems`, so there is no flag to revoke — and Manager is the floor, since org collection/member listing refuses a plain User |
| 201 | [configure-watchtower](201-configure-watchtower/README.md) | a container update actually reported |
| 300 | [Caddy wire/unwire](300-wiring-caddy/README.md) | wiring runs every bootstrap; unwire needs a removal run |
| 301 | [Nginx wire/unwire](301-wiring-nginx/README.md) | an nginx lab — none exists |
| 302 | [Authentik wire/unwire](302-wiring-authentik/README.md) | second-deploy lookup fixed; browser sign-in leg open |
| 304 | [OPNsense wire/unwire](304-wiring-opnsense/README.md) | OPNsense API credentials |
| 305 | [Pihole wire/unwire](305-wiring-pihole/README.md) | a Pihole — user runs OPNsense; low priority |
| 306 | [Reverse-proxy forward_auth](306-wiring-forward-auth/README.md) | Caddy path verified live 2026-07-25; browser sign-in leg + nginx path open |
| 400 | [Vaultwarden](400-app-vaultwarden/README.md) | serving, converging, asserting its own web vault and API layer live (exec 42, 2026-08-09); browser vault CRUD and the three `lab-*` commands unobserved |
| 403 | [Authentik](403-app-authentik/README.md) | one app deployed with `routing.identity: forward_auth`, observed end to end |
| 405 | [Grafana + Prometheus](405-app-grafana/README.md) | five of six observed 2026-08-08; the scrape-set and datasource-health assertions ran green 2026-08-09 (exec 43); only browser admin sign-in remains |
| 407 | [Caddy per-estate DNS-01](407-caddy-dns-challenge/README.md) | running live on the real domain; a second estate would close it |
| 501 | [App remove playbook](501-app-remove-playbook/README.md) | a removal run against a stopped Caddy or Authentik, to confirm the degradation fix |
| 502 | [Rollback container](502-rollback-container/README.md) | roll a Docker app back a tag |
| 504 | [Wire media stack](504-wire-media-stack/README.md) | wiring verified live read-only; needs the full play chain + Ntfy. 505 now supplies the deployed apps it had none of |
| 505 | [Servarr app roles](505-app-servarr/README.md) | one deploy observed live, and with it the two vendor facts the role deliberately does not trust — the `<APP>__AUTH__APIKEY` spelling the current image honours, and that an unkeyed API call really is refused. Both are asserted, so a wrong guess fails the deploy rather than shipping a wrong key or an open API. Also unobserved: a mountpoint attached to a stack host, and one `migrate-servarr` run against the lab's existing Radarrs |
| 600 | [Semaphore project.json](600-semaphore-project-json/README.md) | a restore into a fresh Semaphore |
| 601 | [Rundeck jobs](601-rundeck-jobs/README.md) | 22/22 imported and API-driven. Bootstrap Platform, Lab Status, Remove App, the Vaultwarden jobs and the Deploy jobs for Uptime Kuma / Ntfy / Observability have run; the remaining per-app Deploy jobs, Restart, Tail Log, Rollback, Check Updates, Wire Media Stack and the whole Config group have not |

## Carried caveats

- **301/305 have no live target, and never will here.** The lab runs Caddy, so nginx is
  settled; DNS is OPNsense + Unbound, so Pihole is settled. Both stay `built` indefinitely
  unless a second lab appears; that is expected, not a stall. **304 is not in this
  category** — OPNsense is the lab's router and 304 is queued work, blocked only on API
  credentials.
- **500's one staged import is `apps/nginx.yml`**, which does not exist — 301 shipped the
  wiring pair only, no app playbook.
- **600's backup schema is reconstructed, not exported** from a running Semaphore. If the
  restore rejects it, dump `GET /api/project/<id>/backup` and commit the server's output.
- **PBS's new token check ran against an inherited token, not a created one.** Exec 44
  (2026-08-09) verified the effective token after the ACL grant and passed, but the recorded
  token still authenticated, so `Remove the unusable API token`, `Create the API token` and
  `Grant the token Admin on the datastore tree` all skipped. The rotation path the check was
  written for — create, store, grant, then verify — is still unexercised live. Deleting the
  token on the PBS side would force it, and is the way to close this.
- **010/012's bootstrap script is the proven path, not an experiment.** It has run from a
  full wipe — old runner and predecessor destroyed, then exit 0 on three consecutive runs,
  2026-08-01 — and the current lab was built by it. `pveum` role creation, config authoring,
  project creation, Key Storage staging, tagging and job import are all exercised. What is
  still unobserved is narrow: `config-doctor` on the live runner, Lab Status green with the
  token supplied only from Key Storage, and the runner's vmid inside the PBS backup job.
