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

**Hosting and stack assignment are proposals.** They are the defaults that a row's
`app-defaults` file should carry unless implementation finds a reason against them. A role
must implement and prove every backend it offers; `hosting` is not a free per-instance
switch between unrelated deployment paths.

## Hosting after the Kubernetes backend

Slice 204 added Kubernetes as another hosting backend; it did not make Kubernetes the
destination for every OCI image. Select the backend per application from the workload it
actually has:

- Start Kubernetes with stateless, disposable, scheduled or easily restored workloads.
  Scheduling and restart behavior are useful there, and a lost workload node does not
  strand irreplaceable application data.
- A database-driven application can be stateless at the application layer when it names a
  separately deployed database instance and keeps uploads or other durable files outside
  the pod. This is the main seam between Batch B and the Kubernetes candidates below. The
  database must have enough reserved capacity that sharing it cannot starve its consumers.
- Keep device-bound and host-path-bound workloads on the guest that owns those devices or
  paths. Shared media libraries, download directories, USB devices and GPUs are positive
  reasons to retain the Docker-on-LXC or VM rows.
- Do not describe a database as highly available merely because it runs in Kubernetes.
  Kubernetes can replace a pod; shared storage can make its volume reachable elsewhere;
  database replication, leader election, failover, fencing and application-consistent
  recovery remain database contracts. PostgreSQL, MariaDB and InfluxDB keep their initial
  standalone-LXC recommendation until those contracts are designed and proved.

The current `homelab-local-path` StorageClass is deliberately node-pinned. Its restore path
is proven, but its data does not follow a pod after node loss. Shared Kubernetes storage is
therefore an early platform prerequisite, not a distant optimization: define its Proxmox
failure domains, access modes, capacity ownership, snapshot and restore behavior, and prove
node loss before moving storage-heavy catalog rows or database HA onto it. Shared storage
does not by itself authorize either move.

The tables use **Kubernetes candidate** where the workload shape is promising but the
implementation must still verify upstream persistence, mounts, security context and backup
requirements. **Kubernetes** means the backend decision is already made.

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
| jellyseerr | Kubernetes candidate | — | fallenbagel/jellyseerr | The "seer". Requests front-end over Jellyfin/Plex + Sonarr/Radarr — a real consumer of the media registry's API keys. It has no media-library mount; verify its own persistent state and restore path |
| flaresolverr | Kubernetes candidate | — | FlareSolverr/FlareSolverr | Stateless internal service. No UI, no route, `routing.identity: none`. Consumed by Prowlarr as an indexer proxy — the wiring is a Prowlarr setting, not a reverse-proxy route |
| unpackerr | Docker | media_stack | Unpackerr/unpackerr | No UI. Needs every *arr's API key at deploy time — the first app whose config is assembled from the media registry rather than from its own file |
| kometa | Kubernetes candidate | — | Kometa-Team/Kometa | A Kubernetes CronJob is the natural runtime if the cluster can reach the media library. No port, no route, no Uptime Kuma monitor. Config is a YAML the operator owns |
| maintainerr | Kubernetes candidate | — | jorenn92/Maintainerr | Consumes Plex + the *arrs through APIs; verify its own persistent state and restore path. Same registry-driven config as unpackerr |
| deemix | Docker | media_stack | deemix (web UI fork) | `deemix` kind already declared in `media-wiring.yml`. ARL is a per-lab secret → Vaultwarden |
| slskd | Docker | media_stack | slskd/slskd | The operator's "soulseekd". `slskd` kind already declared. Soulseek credentials → Vaultwarden |
| readarr | Docker | media_stack | (servarr role) | Not requested, but its kind is already in `media-wiring.yml` and the role already handles v1 root folders. Cheapest row in this file: an `app-defaults` file and a playbook |

## Batch B — shared backends and infrastructure guests

Deployed before the application rows that need them.

**A backend is an ordinary one-click app, instanced like any other** (decided 2026-08-17).
`config/apps/<instance>.yml` is what makes `postgresql`, `postgresql-immich` and
`postgresql-forgejo` three separate deployments of one role. The platform does not decide
how many databases a lab runs: an app row names a backend *instance*, and the operator
points several apps at one instance or gives an app its own. One shared Postgres and four
dedicated ones are both correct shapes, and neither is enforced as the default.

What must not happen is a backend arriving as an invisible side-effect of an app's compose
file — that is the shape that makes a lab un-backupable. Every database in the lab is a row
in this table, deployed by its own job, with its own Vaultwarden item and its own PBS
coverage.

| App | Hosting | Stack | Upstream | Notes |
|---|---|---|---|---|
| postgresql | Native LXC initially | own guest | PostgreSQL | Standalone backend. Needs a backend-independent **provisioning contract**: an app asks for a database + role, the backend creates it and hands the credentials back through Vaultwarden. Kubernetes hosting remains a later database-HA decision, not an application prerequisite |
| mariadb | Native LXC initially | own guest | MariaDB | The backend bookstack, wordpress, mautic and mixpost need. Same backend-independent provisioning contract as postgresql — build that contract once, against both |
| mysql | Native LXC initially | own guest | MySQL 8 | Ghost's supported production database; MariaDB is explicitly unsupported. Implements the same backend-independent provisioning contract without becoming a private Ghost sidecar |
| influxdb | Native LXC or Docker | own guest | InfluxDB | Same shape as postgresql: standalone, wired to. Decide 2.x vs 3.x at implementation |
| redis | Native LXC or Docker | own guest | Redis / Valkey | Wanted by immich, paperless-ngx, mixpost and plane. Cache, not a database — an app may still be given its own instance, but little is lost when several share one |
| opnsense | VM | own guest | OPNsense | **Deployable, and separate from the firewall the lab already runs.** `infrastructure.dns.provider: opnsense` (slice 304) wires to an OPNsense this platform did not create and must never adopt. This row builds a *new* VM: an appliance ISO install with its own installer, so the work is media fetch, VM create and console-driven first boot — not a package install |
| wireguard | Native LXC | own guest | wg-quick / wg-easy | **Inbound** WAN server, not a client. Needs its own port forward and a public endpoint; `routing.proxy` does not apply — this is UDP, and a reverse proxy is not in the path |
| homepage | Kubernetes candidate | — | gethomepage/homepage | Stateless lab dashboard and natural consumer of `config/.generated/facts.yml` — this platform already knows every app's URL, so its config should be generated, not hand-written |

## Batch C — applications

Ordinary deploys once Batch B exists. Grouped by the stack they should land on.

| App | Hosting | Stack | Upstream | Notes |
|---|---|---|---|---|
| immich | Docker | photos_stack | immich-app/immich | Postgres + Redis instances from Batch B. **Shared iGPU** for ML — LXC, not VM. Large storage claim — a media_storage-style mount, not `/opt` |
| frigate | Docker on LXC | own guest | blakeblackshear/frigate | **LXC, so its iGPU stays shareable** (revised 2026-08-17 — was Docker on VM). Coral USB passthrough into an LXC is a device bind, like the GPU. Still its own guest: it writes to disk continuously and does not belong on a shared stack host |
| home-assistant | Docker on VM | own guest | home-assistant/core | USB/Zigbee passthrough is why this is a VM, not an LXC |
| nextcloud | Docker | services_stack | nextcloud/server | Postgres instance from Batch B |
| owncloud | Docker | services_stack | owncloud/ocis | Ships alongside nextcloud, not instead of it (decided 2026-08-17). A lab deploys the one it wants; no default picks for it |
| paperless-ngx | Docker | services_stack | paperless-ngx/paperless-ngx | Postgres + Redis; consumes a documents mount |
| emby | Docker | media_stack | MediaBrowser/Emby | Third media server alongside jellyfin and plex — same shape, own auth, `routing.identity: catalog`. All three are options; a lab deploys one, or several |
| navidrome | Docker | media_stack | navidrome/navidrome | Reads the same music library lidarr writes — `library_subpath: music`, read-only |
| audiobookshelf | Docker | media_stack | advplyr/audiobookshelf | Same shape: reads an audiobooks/podcasts subpath |
| mealie | Docker | services_stack | mealie-recipes/mealie | Postgres |
| bookstack | Docker | services_stack | BookStackApp/BookStack | MySQL/MariaDB — the one backend Batch B does not cover. See the open decision below |
| karakeep | Docker | services_stack | karakeep-app/karakeep | Bookmarks and read-later (formerly Hoarder) |
| actual-budget | Docker | services_stack | actualbudget/actual | Single container, own auth → `routing.identity: catalog` |
| searxng | Kubernetes candidate | — | searxng/searxng | Mostly stateless metasearch workload; usually internal-only. Keep any cache disposable or external |
| n8n | Docker | services_stack | n8n-io/n8n | Postgres. Holds credentials to everything it automates — treat its data as secret-bearing |
| plane | Docker | services_stack | makeplane/plane | Multi-container (Postgres, Redis, object storage). Heaviest row in this table |
| forgejo | Docker | services_stack | forgejo/forgejo | Postgres. Git-over-SSH needs a second published port — a reverse proxy does not carry it |
| forgejo-runner | Docker | services_stack | forgejo/runner | Registers **against** forgejo with a runner token — an app-to-app wiring task, the same shape as `ansible/tasks/app-wiring/` |
| wordpress | Docker | services_stack | WordPress | MariaDB. Same database decision as bookstack |
| ghost | Native LXC candidate | own guest | Ghost | Publication-first option with memberships and newsletters. Requires MySQL 8, transactional mail and optional Mailgun bulk delivery; verify Ghost 6's supported production installation path before fixing the hosting kind |
| silex | Docker | services_stack | Silex | Private drag-and-drop builder whose static exports publish into separate `static-site` instances. Protect the builder and its optional Claude MCP surface; public sites must not depend on builder availability |
| static-site | Native LXC | own guest | Publii-compatible static output | One instance per public site. Restricted SFTP publication into an atomic staging path; Publii runs on the editor's computer and is the first accepted client, not a server dependency |
| mautic | Docker | services_stack | mautic/mautic | MariaDB. Sends mail — needs an SMTP contract this platform does not have |
| mixpost | Kubernetes | — | inovector/mixpost | Slice 204's proven pilot. Its namespaced MySQL was an acceptance workload; the Batch B provisioning contract may later point it at a standalone MariaDB instance |
| hi-events | Kubernetes | — | HiEventsDev/hi.events | The recorded second Kubernetes consumer: Postgres-backed, and the first application that must prove the public access class and SMTP contract |
| odoo | Docker | services_stack | odoo/odoo | Broad CRM-plus-business-suite option. PostgreSQL, with an application filestore that must be recovered with it. Start from Community and state any material Enterprise-only limit |
| suitecrm | Native LXC candidate | own guest | salesagility/SuiteCRM-Core | Traditional full-suite CRM. MariaDB; SuiteCRM 8 also needs scheduler cron and an asynchronous worker. Verify an upstream-supported production install before fixing the hosting kind |
| twenty | Docker | services_stack | twentyhq/twenty | Modern workflow-oriented CRM. PostgreSQL + Redis, separate server and worker processes, and persistent file storage. Keep logic execution disabled unless explicitly configured |
| espocrm | Docker | services_stack | espocrm/espocrm | Focused conventional CRM using the official image. MariaDB plus persistent app/customization data; run its daemon and add the WebSocket process only when configured |
| litellm | Kubernetes candidate | — | BerriAI/litellm | Stateless proxy when durable state is external. Holds provider API keys → Vaultwarden |
| ollama | Docker | ai_stack | ollama/ollama | **Dedicated GPU.** Model storage is large and belongs on a mount |
| open-webui | Docker | ai_stack | open-webui/open-webui | Fronts ollama and/or litellm — wire it to whichever is deployed |
| comfyui | Docker | ai_stack | comfyanonymous/ComfyUI | **Dedicated GPU.** Models mount, same as ollama |
| hermes-agent | Docker | ai_stack | NousResearch/hermes-agent | Identified 2026-08-17 (entered as Unknown): MIT-licensed agent platform reachable from Telegram, Discord, Slack, WhatsApp, Signal, email and a CLI, with persistent memory and scheduling. Holds a Nous portal key plus a token for every chat platform it bridges → Vaultwarden. Its own execution backends (local, Docker, SSH, Singularity, Modal) are its config, not this platform's concern |

## Decisions — resolved 2026-08-17

Answered by the operator. Each one changes rows above, and each is recorded here so that no
single row's implementer re-decides it.

- **Database backends are instanced apps, not a platform singleton.** Each backend is a
  one-click row, and an app names the *instance* it uses. One shared Postgres and four
  dedicated ones are equally valid. MariaDB and Redis join Batch B. See the Batch B
  preamble. The provisioning interface must not depend on whether a later implementation
  hosts that database on an LXC or on Kubernetes.
- **Kubernetes starts with stateless applications; shared storage comes early.** External
  database instances let many database-driven web applications join that first group. The
  current node-local StorageClass remains correct for the proved pilot, but it is not the
  final storage contract for storage-heavy applications or database HA. Shared storage is
  designed and failure-tested before those rows move.
- **nextcloud and owncloud both ship.** So do jellyfin, plex and the newly added emby. The
  catalog offers options and the lab picks. Overlap between rows is not a defect; a default
  that silently deploys two of the same thing would be.
- **GPU access has two modes, and shared is preferred.**
  - *Shared iGPU on LXC* — immich, frigate. The device is bound into the container, so
    several guests use one adapter. Frigate moves from VM to LXC for this reason.
  - *Dedicated GPU on VM, PCIe passthrough* — ollama, comfyui. A whole adapter leaves the
    node for one guest.

  Prefer LXC and a shared device. Take a whole GPU only where the workload requires it,
  because passthrough removes that adapter from every other guest on the node. Whoever
  implements the first GPU row builds the contract for both modes, not only the mode that
  row needs.
- **SMTP is a platform contract; its shape is not yet decided.** mautic, hi.events,
  paperless-ngx and nextcloud all send mail, and `notifications` (Ntfy) is not the same
  thing. The lab wants a mail provider configured once and handed to apps. Provider,
  relay and setup are open. The first row that needs mail scopes it as an
  `infrastructure.yml` block plus Vaultwarden credentials, injected into each app's own
  mail settings — not as a private SMTP config inside one app.

## Remaining

- [ ] Batch A implemented — each row has a role (or reuses `servarr`), a playbook, an
      `app-defaults` file, and passes both gates
- [ ] Batch A accepted — a live media-stack wiring run places every new registry entry
- [ ] Batch B implemented, including the database-provisioning contract postgresql needs
- [ ] Shared Kubernetes storage scoped and implemented before storage-heavy applications
      or database HA move onto the cluster; acceptance includes capacity ownership,
      snapshots, application-consistent restore and one-node loss
- [ ] Batch C implemented
- [x] The open decisions are resolved and recorded above (2026-08-17)
- [x] `hermes agent` identified — NousResearch/hermes-agent, now a Batch C row (2026-08-17)
- [ ] The SMTP contract is scoped by the first row that needs mail
- [ ] The GPU contract covers both shared-iGPU-on-LXC and dedicated-passthrough-on-VM

## Links
- `ansible/vars/media-wiring.yml` — the media-registry kinds Batch A rows join
- `ansible/roles/_template-docker/`, `ansible/roles/_template-native/` — what a new row copies
- `docs/meta/done/505-app-servarr/notes.md` — the one-role-four-apps decision record
- notes.md — session narrative
