# 505 — build notes

## Why one role for five apps

`AGENTS.md` describes `roles/<app>/` — one role per deployable app — and this slice ships
`roles/servarr/` instead. The reasoning, on record so it is not re-litigated:

- The five apps differ in exactly three facts (API version, Prowlarr implementation name,
  download-client category field) and all three are **already** a table:
  `ansible/vars/media-wiring.yml`, written by slice 504. Five roles would mean a second
  description of the same five apps, kept in sync by hand.
- Every Servarr behaviour the role handles — the config.xml round-trip, the auth-method
  trap, the unauthenticated-API assertion — is identical across the five. Copied five times,
  a fix lands in one and rots in four.
- The precedent already exists: `roles/observability` deploys two applications under one
  role because neither is useful alone.
- The user-facing rule is untouched. Each app has its own playbook, its own app-defaults
  file, its own Rundeck job and its own Semaphore template. One click per app.

`roles/_template-docker` remains the pattern for a genuinely new app. A sixth Servarr app is
a `vars/app-defaults/` file, a playbook and two job definitions.

## Facts the implementation depends on

- **The API key is declared, not discovered.** Servarr reads config overrides from
  `<APPNAME>__SECTION__KEY`, so the compose file sets the key before first start and the
  credential exists before the container that uses it. Both `<APP>__APIKEY` and
  `<APP>__AUTH__APIKEY` are set: the key moved sections across the v3→v4 line. **Neither is
  trusted.** The role reads `config.xml` back and exports whatever is actually there, so an
  ignored override costs nothing and a lab that already had the app keeps its existing key —
  which matters, because that key is what every Prowlarr Application and Bazarr connection
  in the lab was wired with.
- **A Servarr container opens its port before it has written config.xml.** The port is not a
  readiness signal; the file is. Same shape as the Uptime Kuma failure recorded in
  LESSONS.md, where four green deploys passed over an app sitting on its setup screen.
- **A v4 app with no authentication method serves its first-run setup page on every UI
  path** while answering normally from outside. Asserting `AuthenticationMethod` is present
  and not `None` is what distinguishes that state from a configured app.
- **`AuthenticationRequired: Enabled` is deliberate.** Under
  `DisabledForLocalAddresses` a call from the stack host succeeds without a key, so the
  unauthenticated-refusal assertion would pass on an app that is wide open to the LAN. The
  assertion is only meaningful under `Enabled`.
- **Prowlarr gets no media mount.** It manages indexers and never touches files.
- **Readarr pins `develop`.** There is no current stable Readarr tag.

## Migration leaves peer references stale — the duplication half is closed

Found by auditing the live estate on 2026-08-09, after the role was written. A migrated
database carries every peer connection the source had, and those are addresses. Read off the
running apps:

- **Prowlarr** holds seven Applications, one per *arr, each an IP:
  `Radarr-1015-1080p` → `http://192.168.1.15:7878`, and six more.
- **Each Radarr** holds six download clients (`SABnzbd` → `192.168.1.72:7777`,
  `qBittorrent-1032`–`1035`, `css-qBittorrent-download`), one `PlexServer` notification
  (`192.168.1.4:32400`) and a `RadarrImport` import list pointing at the 4k instance.
- Remote path mappings are **empty**, and stay irrelevant: the mount design gives every host
  identical paths.

Three distinct problems, in order of severity:

1. **`wire-media-stack.yml` would duplicate rather than repair.** It locates an existing
   record by NAME — `selectattr('name', 'equalto', mw_client_name)` in
   `tasks/app-wiring/arr-download-client.yml` and `prowlarr-application.yml` — and the name it
   looks for is the registry instance name. The live records are named `Radarr-1015-1080p` and
   `SABnzbd`; a new instance named `radarr-1080p` matches neither, so wiring creates an eighth
   Application and leaves seven broken ones behind.
2. **Whole categories have no wiring task at all**: `/notification`, `/importlist`,
   `/indexerproxy`, `/remotepathmapping`. Plex and FlareSolverr are not moving, so leaving
   them alone is correct — but that has to be a decision the code makes, not an omission.
3. **Both instances stay live.** Migration is additive by design, so after it two Radarrs
   manage one library, both talking to the same download clients and both importing. The
   stale URL is not the hazard; the second live writer is.

### What was built, 2026-08-09

Problem 1 is closed, and it closed problem 2's *peer* half with it — no separate remap
playbook was needed, because the wiring that already owns those two categories can repair
them in place:

- **`migrate-servarr.yml` writes `media.<instance>.migrated_from`** — the source's own
  address, read from its `config.xml` rather than assumed, because two instances on one host
  are distinguished by nothing but the port. It survives the later deploy's registry write,
  which merges `recursive=True`.
- **Both wiring task files index existing records by the address they point at** and fall
  back to that index when the name does not match. A record found by address is adopted:
  the payload already carries the canonical name, and drift detection now treats adoption as
  drift, so the PUT renames it. The run summary reports `adopted` plus a `renamed` row.
- The identity is host **and** port, scheme and path stripped. Port matters — two *arr apps
  on one stack host differ by nothing else.

Probed offline against a fixture shaped like the live estate: a hand-named
`Radarr-1015-1080p` pointing at `192.168.1.15:7878` is adopted by a migrated `radarr-1080p`,
a `sonarr` matching by name is not an adoption, a genuinely new app matches nothing, and
`SABnzbd` at `192.168.1.72:7777` is adopted by a migrated client. Syntax-check does not
execute Jinja and this is selection logic, so the gates alone would have proved nothing.

### What is still open

- **The categories nothing wires** — `/notification`, `/importlist`, `/indexerproxy`,
  `/remotepathmapping` — are now left alone **by decision**, stated in the playbook header
  and in its closing report rather than by omission. Correct for the Plex notification and
  the FlareSolverr proxy, which are not moving; wrong for `RadarrImport`, the import list
  naming the 4k instance, if that instance also migrates. That one is repointed by hand.
- **The cutover.** Migration is additive: the source keeps running on the same library, so
  two instances write it until the operator retires one. Nothing here automates that, and
  the playbook now says so plainly.
- **No adoption has been observed live.** The probe covers the selection, not the PUT.

## The live run, 2026-08-09 — both vendor facts measured

Deploy Prowlarr, executions 46–48 on the lab. The `media_stack` host was created at
192.168.0.100, Docker installed, the image pulled and the container started. Then:

**The env override IS honoured, and config.xml never says so.** Measured on
`lscr.io/linuxserver/prowlarr:latest` from inside the stack host:

```
keyed:   200      # X-Api-Key = the key this deploy declared
unkeyed: 401
uiroot:  200
config.xml: no <ApiKey>, no <AuthenticationMethod>  (393 bytes, defaults only)
```

Both facts the role refused to trust are now settled: the `<APP>__AUTH__APIKEY` spelling
works, and an unkeyed API call really is refused. What was wrong was the oracle. Servarr
treats `<APP>__AUTH__*` as runtime configuration and persists only what its own setup
writes, so a file with no `<ApiKey>` describes an app whose API key is fully in force. The
role failed a working app on that reading.

The verification therefore asks the app, not the file: the key in force must authenticate,
an unkeyed call must be refused, and `/api/<v>/config/host` must report an
`authenticationMethod` the app actually chose — the setup-page state that config.xml used to
be asked about, asked of the running app. config.xml keeps its one real job, key continuity
for a migrated or hand-configured app.

This is the same lesson as 011's, from the other direction: there the probe was too narrow,
here the assertion was pointed at the wrong artifact. Neither gate can see either.

**Where it stopped.** Execution 48 got through every app check (82 tasks green on the stack
host) and failed at `Vault | Grant the account explicit access to the canonical collection`
in `tasks/bitwarden/upsert-item.yml`. The collection read and the member lookup both
succeeded, so the session is valid and the account is a member — only the `bw edit
org-collection` write failed. That is 016's open question arriving on its own: whether the
automation account, at Manager, can rewrite a collection's grant list. The error itself is
still unseen, because the task is `no_log`.

## Unverified

Nothing here has run against a live Servarr. Both gates are green, which covers syntax and
lint and nothing about the vendor. The two vendor facts that matter are the env-var spelling
and the 401 behaviour, and both were written so that a wrong guess **fails the deploy
loudly** rather than shipping a wrong key or an open API — the config.xml readback replaces
a guessed key with the real one, and the unauthenticated call is an assert, not a debug.

Deploying Prowlarr and one *arr onto the lab's media_stack, then running Wire Media Stack,
exercises this slice and closes 504's last two open items at the same time.

## Session 2026-08-09b — the first *arr that needed the library

Prowlarr (execution 55) proved the servarr path, but it declares no `library_subpath`, so it
asks for no mounts and never touched storage. **Radarr was the first app in the platform's
history to need a library mount**, and it found three defects in one code path — none of
which either gate can see, all of which are now fixed and pushed.

| Execution | Died at | Cause | Commit |
|---|---|---|---|
| 56 | `Attach the missing mountpoints` | `pct set -mpN` hotplugs into a RUNNING container and fails on apparmor | `f0a39fd` |
| 57 | `Configure the root folder` | the guest cannot write a library owned by an unmapped host uid | `2eaf7b0` |
| 58 | `Resolve the identity passthrough` | a `vars:` entry is a string even after `| int` | `ef5b655` |

**The storage identity, decided this session.** An unprivileged stack host maps its whole id
range into the node's `100000+` block, so a library owned by anything outside that block
reads as `nobody:nogroup` inside the guest and every write is refused — Radarr says
`Folder '/mnt/data/media/movies' is not writable by user 'abc'`. `media_storage.owner` now
names one account, **`homelab-infra`, uid/gid 1313**, and the platform makes it true on both
sides: created on the node, granted to root in `/etc/subuid` and `/etc/subgid`, given the
mount roots, and handed to the guest as an `lxc.idmap` passthrough. `app.puid`/`app.pgid`
follow it.

The id is the platform's own deliberately. It defaulted to 1000, which on a Debian node is
whoever was created there first — here that was `civicfs`, the file-server account of the
CIVIC domain **being decommissioned**, which had silently become the owner of the media
library. Chosen by the user, 2026-08-09. There is no registry of local uids: `/etc/login.defs`
hands `UID_MIN`–`UID_MAX` (1000–60000) to useradd's allocator, and creating the account is
what claims 1313 against it.

**Mount roots only, never `chown -R`.** The platform takes the directories it was handed, not
everything inside them — a recursive walk across an NFS export of 2,589 films is not something
a deploy starts. An app's own `library_subpath` it now creates and owns itself, which reverses
this slice's earlier "asserted, never created" rule for that one directory and only that one.

**A `no_log` task cannot report itself.** Execution 57 failed with nothing but `censored`, and
reading the actual reason cost a hand-run curl against the container. `Configure the root
folder` now re-raises from the RESPONSE alone, which carries no credential. Treat every
`no_log` task that can fail on a *server* answer the same way.

### Where it stopped

Node state is applied and verified on pve-host-3: `homelab-infra` uid/gid 1313 exists,
`root:1313:1` is in both `/etc/subuid` and `/etc/subgid`, and the library plus its two empty
subdirectories are chowned to 1313. The guest's `lxc.idmap` is **not yet written** — execution
58 died immediately before that task, so guest 168000100 is untouched and running, and the
next run continues from there idempotently.

**Next click: `Deploy Radarr`.** It exercises the node account, the subuid grants, the idmap,
the stop/start path and the root folder in one run. Then Sonarr, Readarr, Lidarr, then
`Wire Media Stack`, then the day-2 jobs. Sitting 1 is not finished.

Two caveats for whoever runs it. The idmap write stops and restarts the guest, which briefly
takes Prowlarr down with it — expected, and the only disruption in the change. And Prowlarr's
own container keeps its `puid: 1000` files until its next deploy; container uid 1000 still
maps to host 101000 under the new ranges, so nothing breaks, but Prowlarr and Radarr run as
different ids until Prowlarr is redeployed.

## Session 2026-08-09c — four *arrs deployed, and the registry that recorded none of them

Executions 59–72. Radarr, Sonarr and Lidarr are deployed on `media_stack`, Prowlarr is
redeployed, and `Wire Media Stack` resolved all four and created three Prowlarr
Applications. Readarr is blocked on a vendor fact, below. Four more defects, all found by
running, none visible to either gate.

| Execution | Died at | Cause | Commit |
|---|---|---|---|
| 60/61 | `pct start` | the idmap write was neither idempotent nor detectable; the guest got two copies and would not boot | `8a42b43` |
| 63 | `docker compose pull` | `readarr:develop` no longer exists for linux/amd64 | `3e13965` |
| 64 | `Configure the root folder` | Lidarr's v1 API validates `name` + two profile ids that Radarr and Sonarr do not | `3e13965` |
| 67 | nothing — it exited 0 | the media registry was keyed by the literal string `{{ instance }}` | `86de49e` |

**`.split('\n')` in a YAML folded scalar does not split.** The escape reaches Jinja as a
literal backslash-n, so the guest config came back as ONE line, the passthrough always
looked missing, and blockinfile appended a second copy of the map. LXC then refused the
guest with `container uid 0 is also mapped by entry`. Measured on the runner, not guessed:
the same expression returned `nlines: 1` against the real file. Use `.splitlines()`.

**PVE owns the guest config file, so a comment marker cannot anchor anything in it.**
`pct set` — which this platform calls itself, to stamp tags and the apps table — rewrites
the file and hoists every `#` line into the description block at the top. The blockinfile
markers survived as an empty pair ten lines above the content they were supposed to
bracket. The write now replaces the whole `lxc.idmap:` set instead of appending to it,
which is idempotent under anything PVE does and repairs a guest already carrying
duplicates. Proven both ways: execution 62 wrote the map, 63 and later left it alone.

**A guest the platform broke, the platform could not fix.** `find-or-create-host` starts
the stack host before `attach-host-mounts` ever runs, so an unbootable guest fails the
deploy at the start and never reaches the task that would repair its config. Execution 61
died there and the config had to be stripped by hand. Not fixed — noted. The ordering is
only reachable when the platform has already written a bad config, which is the bug that
was just closed.

**Lidarr and Readarr validate a root folder harder than Radarr and Sonarr.** `POST
/api/v1/rootfolder` answers 400 without `name`, `defaultQualityProfileId` and
`defaultMetadataProfileId`. `media-wiring.yml` now carries `rootfolder_needs_profiles` per
kind, the role reads the ids from the app itself, and a Radarr or Sonarr request still
sends the bare path. The generic failure message blamed storage permissions; the 400 body,
which the earlier `no_log` fix made visible, gave the real answer immediately.

**Readarr is retired and the platform cannot publish it.** Upstream archived the project
and LinuxServer stopped publishing `develop` and `latest`, so the pull dies with `no
matching manifest` for linux/amd64. The image is pinned to the last multi-arch build,
`nightly-0.1.0.1225-ls85` — and that build predates the platform's auth contract: it
ignores `READARR__AUTH__*` entirely, writes `<AuthenticationMethod>None</AuthenticationMethod>`
and reports `authenticationMethod: none`, which the role refuses because a reverse proxy in
front of it would publish a setup wizard. The refusal is correct. The container is left
running on the stack host, unauthenticated and unpublished, pending a decision to drop the
app or accept it as-is. **Deploy Readarr is the one per-app job that cannot go green.**

### Readarr is dropped, 2026-08-09

Decided by the user rather than worked around. `playbooks/apps/readarr.yml`,
`vars/app-defaults/readarr.yml`, `rundeck/jobs/deploy-readarr.yaml` and the Semaphore
template are deleted, the live Rundeck job with them, and the container and `/opt/readarr`
are removed from the stack host. The `readarr` kind stays in `media-wiring.yml` — a lab that
runs its own Readarr can declare it in `config/apps/` and be wired in like any other app, and
the `rootfolder_needs_profiles` fix applies to that path too. This role ships four apps now.

## Acceptance caught up with the lab, 2026-08-12

No code changed. Five of the seven `Remaining` boxes were already true — four live deploys
(59–72), the two untrusted image facts (the role asserts both, so a green deploy *is* the
confirmation), the registry-fed Wire Media Stack leg (72, re-confirmed `changed=0` at 90),
and the mountpoint plus idmap on guest 168000100. The Readarr box is void; the app was
dropped the same day it was written.

Two remain: a `changed=0` re-deploy (one click, `Deploy Prowlarr`), and the migration —
which is the same run 504 needs for its adoption criterion.

## The migration was attempted, 2026-08-12 — and its guard is what stopped it

Executions 107, 113, 120. Target: `sonarr` from the operator's `css-sonarr-anime-1`
(LXC 1012, 192.168.1.12, 61 MB config). Two defects and one correct refusal.

- **107** — the Rundeck job died on line 4: `${option.instance}` in a script step. Fixed in
  `rundeck/jobs/migrate-servarr.yaml`; live after `Reimport Jobs` (108).
- **113** — `root@192.168.1.12: Permission denied`. A hand-built guest carries no platform
  key, so **SSH access to the source is a prerequisite of migrating a foreign app** and
  nothing says so. The runner's public half (`/var/lib/rundeck/.ssh/homelab-infra.pub`; the
  private half is in Rundeck Key Storage, not on disk) was added to root's authorized_keys
  on 1012 and 1014, with the operator's approval.
- **120** — copied the source config, stopped and restarted the source cleanly, then
  **refused**: the migrated database expects its library at `/mnt/data/media/tv/anime`,
  which does not exist on the stack host. That is the guard doing exactly its job, and it
  named the fix.

### Why it cannot exist, and why that is not a bug

| | Host path | Backing | In-container |
|---|---|---|---|
| `css-sonarr-anime-1` (LXC 1012) | `/mnt/media` | `disk-pool/media`, 54 T, **40 T used** | `/mnt/data/media` |
| `media_stack` (168000100) | `/friends-pool/homelab-media` | 114 G, **192 K used** | `/mnt/data/media` |

Same path inside the container, different storage. **This is deliberate** — confirmed by the
operator 2026-08-12: the platform's media stack is a separate, empty library, and one of the
open slices is a full teardown and rebuild. The old *arrs exist as migration test material,
not as a cutover target. So a real migration is not wanted yet, and the platform pointing at
`friends-pool` is correct, not a misconfiguration.

**What this run therefore proves:** the job runs, the source is stopped and restarted safely,
the copy lands, and the path guard fires before an operator gets a library reporting every
item missing. What it does not prove is the adoption PUT — 504's last box — which still needs
a migration that completes.

The platform's `sonarr` config was restored from `/opt/.pre-migrate-backups/` (taken before
the run), the container restarted, and it answers HTTP 200 on its original database.
