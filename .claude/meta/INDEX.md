# Meta Index

The work queue. This file stays a table — prose belongs in [LESSONS.md](LESSONS.md),
per-session narrative in a slice's own `notes.md`, slice shape in [README.md](README.md).

**27 live · 21 archived in [done/](done/).** Statuses: `open` = not started, abandoned, or
reopened by evidence. `built` = code written, both gates green, acceptance not yet observed
live. Archived slices are finished; do not reopen without a new slice.

Gates (both green): `wsl bash -lc 'bash .claude/gate/lint.sh'` and `.claude/gate/test.sh`.

## Start here

| Order | Do this | Slices |
|---|---|---|
| 1 | Run a deploy against the lab's own Uptime Kuma — .0.14 still holds zero monitors. One deploy registers one; a DOWN/UP closes both slices. | 303, 404 |
| 2 | Make deploys assert usability, not liveness. One check per app role that only an initialized app can pass. | all app slices |
| 3 | Decide 016's collection scoping — grant per-collection, or amend the criterion to the decision already made. | 016 |
| 4 | Fix the IP allocator before the next guest. Six addresses are already live under the flat model. | 011 |
| 5 | The browser legs — Vaultwarden vault CRUD, Authentik sign-in, one app behind `forward_auth`. Needs a human at a browser. | 400, 403, 405, 306 |
| 6 | A media app role. 504 wires the media stack but nothing deploys it. | 504 |

## By subject

One subject spans several slices, because slices are cut on the code axis (app role /
wiring task / bootstrap play). Look up the subject, then read only those slices.

| Subject | Slices |
|---|---|
| Uptime Kuma | app **404**, wiring **303** |
| Vaultwarden | app **400**, secret store **014**, token capture **013**, identities **016** |
| Caddy / TLS | wiring **300**, DNS-01 **407**, wildcard bootstrap **015** |
| Authentik / identity | app **403**, wiring **302**, forward_auth **306**, modes **009** |
| Config model | provenance **010**, onboarding **012**, estates **008** |
| Networking | IP allocation **011** |
| Media | wiring **504** |
| Runners / UI | Rundeck **601**, Semaphore **600** |
| Day-2 ops | watchtower **201**, remove **501**, rollback **502** |
| No live target | nginx **301**, pihole **305**, opnsense **304** |

## Open (3)

Design defects in shipped code, or gaps between the documented model and the implemented
one. None are new features.

| # | Slice | What is unresolved |
|---|---|---|
| 011 | [IP allocation model](011-ip-allocation-model/README.md) | `generate-ip.yml` is a flat +1 walk with one global offset; the lab addresses by function across three bands in a single /20. Six addresses (.10–.15) are already allocated under the flat model. |
| 015 | [Caddy-first wildcard HTTPS](015-wildcard-dns-default/README.md) | Certificate model observed live 2026-08-06. Open on internal mode, the no-API-provider handoff, the resume item, and migrating an already-serving estate without interrupting HTTPS. |
| 016 | [Vaultwarden identities + keyring](016-vaultwarden-identity-bootstrap/README.md) | One decision: `users_collections` is empty, so the automation account reads the org as an Admin with `allowAdminAccessToAllCollectionItems` — org-scoped, not collection-scoped. |

## Built — awaiting live acceptance (24)

Code-complete and gate-verified. Each row names the unobserved leg.

| # | Slice | What live acceptance needs |
|---|---|---|
| 008 | [Estate / multi-domain contract](008-estate-contract/README.md) | a second-domain deploy |
| 009 | [Identity modes (routing.identity)](009-identity-modes/README.md) | one app deployed per mode |
| 010 | [Config provenance](010-config-provenance/README.md) | the bootstrap script run on a node — the Config job group is verified on the workstation; nothing the script does is |
| 012 | [Runner onboarding](012-runner-onboarding/README.md) | Configure App / Get Config from the UI, `LAB_REFRESH=0`, a no-op script re-run, the root README |
| 013 | [Vaultwarden token self-capture](013-vaultwarden-token-capture/README.md) | sink write/readback proved live; HTTPS, identities and item CRUD belong to 015/016/014 |
| 014 | [Vaultwarden secret store](014-vaultwarden-secret-store/README.md) | only the runner rebuild from Key Storage — seed-file recreation and seed re-entry were both injected and refused, 2026-08-06 |
| 201 | [configure-watchtower](201-configure-watchtower/README.md) | a container update actually reported |
| 300 | [Caddy wire/unwire](300-wiring-caddy/README.md) | wiring runs every bootstrap; unwire needs a removal run |
| 301 | [Nginx wire/unwire](301-wiring-nginx/README.md) | an nginx lab — none exists |
| 302 | [Authentik wire/unwire](302-wiring-authentik/README.md) | second-deploy lookup fixed; browser sign-in leg open |
| 303 | [Uptime Kuma wire/unwire](303-wiring-uptime-kuma/README.md) | reworked onto socket.io 2026-08-08 and exercised against a throwaway 2.5.0 — create, re-wire, drift, unwire, re-unwire and three degradation paths all checked in Kuma's database. Only a real DOWN/UP reaching Ntfy is unobserved |
| 304 | [OPNsense wire/unwire](304-wiring-opnsense/README.md) | OPNsense API credentials |
| 305 | [Pihole wire/unwire](305-wiring-pihole/README.md) | a Pihole — user runs OPNsense; low priority |
| 306 | [Reverse-proxy forward_auth](306-wiring-forward-auth/README.md) | Caddy path verified live 2026-07-25; browser sign-in leg + nginx path open |
| 400 | [Vaultwarden](400-app-vaultwarden/README.md) | serving, converging and driving every vault write; browser vault CRUD unobserved |
| 403 | [Authentik](403-app-authentik/README.md) | one app deployed with `routing.identity: forward_auth`, observed end to end |
| 404 | [Uptime Kuma](404-app-uptime-kuma/README.md) | initialized, admin account and API key all scripted. Its Ntfy channel was proved on a throwaway instance; unobserved on the lab's own |
| 405 | [Grafana + Prometheus](405-app-grafana/README.md) | five of six observed 2026-08-08; only admin sign-in remains |
| 407 | [Caddy per-estate DNS-01](407-caddy-dns-challenge/README.md) | running live on the real domain; a second estate would close it |
| 501 | [App remove playbook](501-app-remove-playbook/README.md) | a removal run against a stopped Caddy or Authentik, to confirm the degradation fix |
| 502 | [Rollback container](502-rollback-container/README.md) | roll a Docker app back a tag |
| 504 | [Wire media stack](504-wire-media-stack/README.md) | wiring verified live read-only; needs the full play chain + Ntfy |
| 600 | [Semaphore project.json](600-semaphore-project-json/README.md) | a restore into a fresh Semaphore |
| 601 | [Rundeck jobs](601-rundeck-jobs/README.md) | 22/22 imported and API-driven. Bootstrap Platform, Lab Status, Remove App and the Vaultwarden jobs have run; the per-app Deploy jobs, Restart, Tail Log, Rollback, Check Updates, Wire Media Stack and the whole Config group have not |

## Carried caveats

- **301/305 have no live target.** The lab runs Caddy + OPNsense. These stay `built`
  indefinitely unless a second lab appears; that is expected, not a stall.
- **500's one staged import is `apps/nginx.yml`**, which does not exist — 301 shipped the
  wiring pair only, no app playbook.
- **600's backup schema is reconstructed, not exported** from a running Semaphore. If the
  restore rejects it, dump `GET /api/project/<id>/backup` and commit the server's output.
- **010/012's bootstrap script has never run against a real node.** `pveum` role creation,
  config authoring, project creation, Key Storage staging and job import are all
  unexercised. Treat the first run as an experiment.
