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

## Unverified

Nothing here has run against a live Servarr. Both gates are green, which covers syntax and
lint and nothing about the vendor. The two vendor facts that matter are the env-var spelling
and the 401 behaviour, and both were written so that a wrong guess **fails the deploy
loudly** rather than shipping a wrong key or an open API — the config.xml readback replaces
a guessed key with the real one, and the unauthenticated call is an assert, not a debug.

Deploying Prowlarr and one *arr onto the lab's media_stack, then running Wire Media Stack,
exercises this slice and closes 504's last two open items at the same time.
