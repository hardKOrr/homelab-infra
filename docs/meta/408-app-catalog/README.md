# 408 — App catalog

**Status:** open
**Subject:** which applications this platform deploys, and in what order they get built
**Related:** 505 (the servarr role, which four of these apps share), 504 (media wiring —
the registry every media app joins), 500 (bootstrap plays)

## Goal

One place that names every application this platform intends to deploy, its hosting kind,
its stack, and the wiring it needs — so that "do we have X?" is answered by reading a table
rather than by listing `ansible/roles/`. Each row becomes an implementation batch below;
nothing in this slice is code.

The catalog was entered on 2026-08-17 from the operator's own lab inventory. It is a
declaration of intent, not a plan that has shipped.

### What this catalog is NOT

**Ports, images and tags are deliberately absent.** They belong in
`ansible/vars/app-defaults/<app>.yml`, are verified against the upstream project at
implementation time, and would be stale here within a release. A row names the upstream
project so the implementer knows what to go and read; it does not pre-decide the image.

**Stack assignment is a proposal.** It is the default that a row's `app-defaults` file
should carry unless implementation finds a reason against it. Any instance overrides it in
`config/apps/<instance>.yml`, which is what makes multiple instances possible.

## Why sonarr, radarr, lidarr and prowlarr share one role — and sabnzbd does not

Asked 2026-08-17; the answer governs every row below, so it lives here.

`servarr` is not a grouping and not a stack. It is **one program with four media types**:
Sonarr, Radarr, Lidarr and Prowlarr are the same codebase, the same `/config` layout, the
same `config.xml`, the same `/api/<v>/system/status` contract, the same authentication
overrides. What differs between them — API version, Prowlarr implementation name,
download-client category field — is already a table in `ansible/vars/media-wiring.yml`.
Four copies of that role would mean four places to fix every Servarr behaviour and a
second table describing the same four apps.

**Each of them is still uniquely deployable, and already is.** Each has its own
`ansible/playbooks/apps/<app>.yml`, its own `ansible/vars/app-defaults/<app>.yml` and its
own one-click job. Multiple instances of one (`sonarr`, `sonarr-anime`) are two
`config/apps/<instance>.yml` files — the role reads `instance`, and project directory,
container name, config path and vault item are all keyed by it. Sharing the role costs the
operator nothing at the click.

SABnzbd and qBittorrent are outside it because they are **different programs**: a Usenet
downloader and a BitTorrent client, with their own config formats, their own credential
model and their own APIs. They are peers of the *arr apps in the media registry, not
variants of them. `ansible/vars/media-wiring.yml` is where all of these meet.

**The rule for every row below:** share a role only where the apps are the same program.
Same *category* is not the same program.

## Batch A — media stack completion

The stack the operator already runs, minus what is built. Every row here joins the media
registry or feeds something that does, so 504's wiring is the acceptance surface.

Built already: `sonarr`, `radarr`, `lidarr`, `prowlarr` (all via `servarr`), `sabnzbd`,
`qbittorrent`, `jellyfin`.

| App | Hosting | Stack | Upstream | Notes |
|---|---|---|---|---|
| bazarr | Docker | media_stack | morpheus65535/bazarr | **The gap that matters most.** `ansible/tasks/app-wiring/bazarr-arr.yml` and a `bazarr` kind in `media-wiring.yml` already exist and have nothing to wire — 504 wires an app this repo cannot deploy |
| plex | Docker | media_stack | plexinc/pms-docker | Claim token is a per-lab secret → Vaultwarden. `routing.identity: catalog` — Plex owns its own auth. The custom access URL needs the **explicit port** |
| tautulli | Docker | media_stack | Tautulli/Tautulli | Reads Plex; needs Plex's token. No media-registry kind |
| jellyseerr | Docker | media_stack | fallenbagel/jellyseerr | The "seer". Requests front-end over Jellyfin/Plex + Sonarr/Radarr — a real consumer of the media registry's API keys |
| flaresolverr | Docker | media_stack | FlareSolverr/FlareSolverr | No UI, no route, `routing.identity: none`. Consumed by Prowlarr as an indexer proxy — the wiring is a Prowlarr setting, not a reverse-proxy route |
| unpackerr | Docker | media_stack | Unpackerr/unpackerr | No UI. Needs every *arr's API key at deploy time — the first app whose config is assembled from the media registry rather than from its own file |
| kometa | Docker | media_stack | Kometa-Team/Kometa | Scheduled run, not a service. No port, no route, no Uptime Kuma monitor. Config is a YAML the operator owns; the platform places it and schedules the run |
| maintainerr | Docker | media_stack | jorenn92/Maintainerr | Consumes Plex + the *arrs; same registry-driven config as unpackerr |
| deemix | Docker | media_stack | deemix (web UI fork) | `deemix` kind already declared in `media-wiring.yml`. ARL is a per-lab secret → Vaultwarden |
| slskd | Docker | media_stack | slskd/slskd | The operator's "soulseekd". `slskd` kind already declared. Soulseek credentials → Vaultwarden |
| readarr | Docker | media_stack | (servarr role) | Not requested, but its kind is already in `media-wiring.yml` and the role already handles v1 root folders. Cheapest row in this file: an `app-defaults` file and a playbook |

## Batch B — shared backends

Deployed once, wired to by other apps. These come before the application rows that need
them, because "every app brings its own Postgres container" is the shape that makes a lab
un-backupable.

| App | Hosting | Stack | Upstream | Notes |
|---|---|---|---|---|
| postgresql | Native LXC | own guest | PostgreSQL | Standalone backend. Needs a **provisioning contract**: an app asks for a database + role, the backend creates it and hands the credentials back through Vaultwarden. That contract is the actual work; the LXC is trivial |
| influxdb | Native LXC or Docker | own guest | InfluxDB | Same shape as postgresql: standalone, wired to. Decide 2.x vs 3.x at implementation |
| wireguard | Native LXC | own guest | wg-quick / wg-easy | **Inbound** WAN server, not a client. Needs its own port forward and a public endpoint; `routing.proxy` does not apply — this is UDP, and a reverse proxy is not in the path |
| homepage | Docker | services_stack | gethomepage/homepage | The lab dashboard. Natural consumer of `config/.generated/facts.yml` — this platform already knows every app's URL, so its config should be generated, not hand-written |

## Batch C — applications

Ordinary deploys once Batch B exists. Grouped by the stack they should land on.

| App | Hosting | Stack | Upstream | Notes |
|---|---|---|---|---|
| immich | Docker | photos_stack | immich-app/immich | Postgres + Redis + ML. GPU optional. Large storage claim — a media_storage-style mount, not `/opt` |
| frigate | Docker on VM | own guest | blakeblackshear/frigate | Needs a Coral or GPU passthrough and writes to disk continuously; do not put it on a shared stack host |
| home-assistant | Docker on VM | own guest | home-assistant/core | USB/Zigbee passthrough is why this is a VM, not an LXC |
| nextcloud | Docker | services_stack | nextcloud/server | Postgres from Batch B |
| owncloud | Docker | services_stack | owncloud/ocis | Overlaps nextcloud — **decide one at implementation** rather than shipping defaults for both |
| paperless-ngx | Docker | services_stack | paperless-ngx/paperless-ngx | Postgres + Redis; consumes a documents mount |
| navidrome | Docker | media_stack | navidrome/navidrome | Reads the same music library lidarr writes — `library_subpath: music`, read-only |
| audiobookshelf | Docker | media_stack | advplyr/audiobookshelf | Same shape: reads an audiobooks/podcasts subpath |
| mealie | Docker | services_stack | mealie-recipes/mealie | Postgres |
| bookstack | Docker | services_stack | BookStackApp/BookStack | MySQL/MariaDB — the one backend Batch B does not cover. See the open decision below |
| karakeep | Docker | services_stack | karakeep-app/karakeep | Bookmarks and read-later (formerly Hoarder) |
| actual-budget | Docker | services_stack | actualbudget/actual | Single container, own auth → `routing.identity: catalog` |
| searxng | Docker | services_stack | searxng/searxng | Metasearch; usually internal-only |
| n8n | Docker | services_stack | n8n-io/n8n | Postgres. Holds credentials to everything it automates — treat its data as secret-bearing |
| plane | Docker | services_stack | makeplane/plane | Multi-container (Postgres, Redis, object storage). Heaviest row in this table |
| forgejo | Docker | services_stack | forgejo/forgejo | Postgres. Git-over-SSH needs a second published port — a reverse proxy does not carry it |
| forgejo-runner | Docker | services_stack | forgejo/runner | Registers **against** forgejo with a runner token — an app-to-app wiring task, the same shape as `ansible/tasks/app-wiring/` |
| wordpress | Docker | services_stack | WordPress | MariaDB. Same database decision as bookstack |
| mautic | Docker | services_stack | mautic/mautic | MariaDB. Sends mail — needs an SMTP contract this platform does not have |
| mixpost | Docker | services_stack | inovector/mixpost | MySQL + Redis |
| hi-events | Docker | services_stack | HiEventsDev/hi.events | Postgres |
| odoo | Docker | services_stack | odoo/odoo | Postgres, and opinionated about its version pairing — pin both together |
| litellm | Docker | ai_stack | BerriAI/litellm | Proxy in front of the model backends. Holds provider API keys → Vaultwarden |
| ollama | Docker | ai_stack | ollama/ollama | GPU passthrough; model storage is large and belongs on a mount |
| open-webui | Docker | ai_stack | open-webui/open-webui | Fronts ollama and/or litellm — wire it to whichever is deployed |
| comfyui | Docker | ai_stack | comfyanonymous/ComfyUI | GPU. Models mount, same as ollama |

## Not application rows

Recorded here so nobody enters them as deploys later.

| Item | Why not |
|---|---|
| opnsense | Already the lab's DNS provider (`infrastructure.dns.provider: opnsense`, slice 304) and its firewall. This platform **wires to** it and does not deploy it: it is an appliance install with its own installer, and it owns the network this platform runs on. A deployable OPNsense VM would be a separate decision, not a catalog row |
| hermes agent | **Unknown.** No upstream identified at entry time. The operator has to name the project before it can be scoped |

## Open decisions

These change what gets built, and none of them should be settled by whoever happens to
implement the first row that hits them:

- **Database backends.** Batch B has Postgres and Influx. Four Batch C rows want
  MySQL/MariaDB. Either add a MariaDB backend row, or rule that MariaDB apps own their own
  database container. Deciding per-app is how a lab ends up with four MariaDBs.
- **nextcloud vs owncloud.** Both are entered. Shipping defaults for both invites a lab to
  run two of the same thing.
- **GPU passthrough.** Four rows (ollama, comfyui, immich, frigate) need it. This platform
  has no GPU contract; the first of these to be implemented has to create one.
- **SMTP.** mautic, hi.events, paperless-ngx and nextcloud all send mail.
  `infrastructure.yml` has a `notifications` provider for Ntfy, which is not the same thing.

## Remaining

- [ ] Batch A implemented — each row has a role (or reuses `servarr`), a playbook, an
      `app-defaults` file, and passes both gates
- [ ] Batch A accepted — a live media-stack wiring run places every new registry entry
- [ ] Batch B implemented, including the database-provisioning contract postgresql needs
- [ ] Batch C implemented
- [ ] The four open decisions above are resolved and recorded here
- [ ] `hermes agent` is identified or removed from the catalog

## Links
- `ansible/vars/media-wiring.yml` — the media-registry kinds Batch A rows join
- `ansible/roles/_template-docker/`, `ansible/roles/_template-native/` — what a new row copies
- `docs/meta/done/505-app-servarr/notes.md` — the one-role-four-apps decision record
- notes.md — session narrative
