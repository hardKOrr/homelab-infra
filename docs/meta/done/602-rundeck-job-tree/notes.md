# 602 — notes

## 2026-08-20 — the target tree

Written after auditing all 36 files in `rundeck/jobs/` and the live runner. Nothing in
`rundeck/jobs/` was modified.

Current: `Bootstrap` (3), `Apps` (18), `Config` (5), `Maintenance` (10). The three new
slice-204 jobs — Backup App, Restore App, Reclaim Volume — are counted in Maintenance.

Proposed:

```
Deploy/Platform    Caddy, Vaultwarden, Ntfy, Authentik, Uptime Kuma, Observability, PBS
Deploy/Backend     k3s Cluster
Deploy/Media       Jellyfin, Lidarr, Prowlarr, qBittorrent, Radarr, SABnzbd, Sonarr,
                   Migrate Servarr
Deploy/Web         Mixpost            ← 408 adds Publii, WordPress, Ghost, Silex
                                      ← 408's CRM rows open Deploy/Business
Operate            Lab Status, Tail App Log, Restart App, Check Native App Updates,
                   Wire Media Stack, Backup App
Operate/Config     Config Doctor, Get Config, Configure App, Store Secret
Recover            Rollback Container, Restore App, Reclaim Volume, Remove App,
                   Vaultwarden Recovery
Setup              Bootstrap Platform, Vaultwarden Enrollment, Vaultwarden Cutover,
                   Reimport Jobs
```

Every group is named by what it holds. There is no catch-all, and adding an application
means choosing an existing subject or opening a named one — never dropping a row into a
bucket that means "the rest".

### Why verb at the top and subject underneath

Rundeck gives one hierarchy, so the top level has to answer the question the operator
arrives with: make a thing exist, look at a thing, something is wrong, set the lab up.
Subject belongs at the second level because subject is the axis that grows.

### Why `Deploy/Platform` is ordered by bootstrap chain, not alphabetically

Caddy, Vaultwarden, Ntfy, Authentik, Uptime Kuma, Observability and PBS are what everything
else wires *into*. Listing them in bootstrap order makes the console show the dependency
order the platform actually has.

### Why the k3s cluster leaves `Apps`

`AGENTS.md` already says it is a hosting backend and not an app, and its UI Job Structure
section already shows a `Backend` group. That group does not exist in `rundeck/jobs/`. This
closes the gap in the direction the document already chose.

### Why Vaultwarden's three jobs split differently

Enrollment and Cutover are in `Bootstrap` while Recovery is in `Maintenance`, though all
three drive the same credential set. Enrollment and Cutover belong to standing the lab up;
Recovery belongs to the day something breaks. That is the split that describes when you
reach for each.

## Migration mechanics — verified, not assumed

Checked against the repository and the live runner on 2026-08-20:

- **The edit is one line per file.** Only `group:` changes.
- **Nothing is orphaned or duplicated.** All 36 jobs carry a stable `uuid`, and
  `reimport-jobs.yaml` posts to `/api/47/.../jobs/import` with
  `dupeOption=update&uuidOption=preserve`. A group change on an existing UUID is an
  in-place update, so execution history and the weekly schedule on *Check Native App
  Updates* survive.
- **Nothing references a job by group.** No `jobref` steps exist between jobs, and the only
  `group:` in `/etc/rundeck/admin.aclpolicy` is the *user* group `admin`, not a job-group
  matcher.
- **Rollback is the same click.** Revert the commit, run *Reimport Jobs*.

## Deliberately not proposed

- **Dropping the `Deploy ` name prefix.** Inside a `Deploy/*` group it is redundant and
  costs seven identical characters in an alphabetically sorted column. But job names travel
  into Ntfy notifications, execution history and API responses, where "Deploy Sonarr
  failed" is legible out of context and "Sonarr failed" reads like the application went
  down. Changing `group:` is reversible and observable; changing `name:` degrades every
  notification the lab sends. Not worth it.
- **Collapsing per-app jobs into one parameterised job.** It would shrink the tree to
  almost nothing and directly contradicts the standing rule — one click per app, not a
  stack behind checkboxes. The tree grows because the catalog grows, and that is the
  intended shape.

## Resolved by the tag axis, 2026-08-21

Three placements were open because each job genuinely belongs in two places. A group is one
hierarchy, so those were zero-sum. Tags are a second, orthogonal axis, and they settle all
three without a coin flip:

| Job | Group | Tags |
|---|---|---|
| *Remove App* | `Recover` — its `delete_data` option destroys volumes | `destructive`, `lifecycle` |
| *Wire Media Stack* | `Operate` — idempotent and safe to re-run | `media` |
| *Bootstrap Platform*, *Vaultwarden Enrollment*, *Vaultwarden Cutover*, *Reimport Jobs* | `Setup` keeps its own root — these are done to the lab, not to an app in it | `platform` / `vault` |

### The tag vocabulary

Small and closed on purpose. A tag axis that anyone may extend becomes a second flat list.

- **Subject** — `platform`, `backend`, `media`, `web`, `business`, `vault`
- **Risk** — `destructive` for anything that can lose data or the credential path;
  `read-only` for anything that changes nothing

Nothing else until a concrete job needs it. Zero jobs in `rundeck/jobs/` carry tags today,
so this is additive on top of the `group:` rewrite.

## Dead end — this was first written as a published artifact

The proposal was initially delivered as an Artifact rather than a slice. That was wrong:
this repository records intent, scope and open decisions in `docs/meta/`, and a second
home for the same facts is exactly what "one fact, one home" in `docs/meta/README.md`
forbids. The artifact is superseded by this slice and carries no information this file
does not.

## 2026-08-21 — the correction that produced the tree above

The first version of this slice proposed `Deploy/Apps` alongside `Deploy/Media`. That is
the defect the slice exists to remove, restated one level down: `Media` names a subject,
`Apps` names the remainder. It held one job then and slice 408 would have put seven more
into it, at which point it is the eighteen-row `Apps` group again.

A catch-all group is not a grouping decision, it is a deferred one. Rejected.

The opposite over-correction — one group per application — was considered and is also
wrong. It trades a bucket that means nothing for a hierarchy with no grouping left in it.
Logical subjects at a useful size are the whole answer: an application's group is the
thing it is for, and if no existing subject fits, a new named subject opens.

### Also recorded here

- **The Rundeck CLI.** `rundeck-cli` is installed nowhere — `rundeck/bootstrap-rundeck.sh`
  installs the server package only, and `rd` appears in this repository purely as
  documentation (`rundeck/README.md:99` and every job-file header). Installing it on the
  runner is worth doing so that operator and session runs are a recorded command
  (`rd run -j '<group>/<name>' -f`) rather than a hand-driven REST call. It is **not** a
  replacement for the import path: `render-job.py` injects the secure `storagePath`
  options at import time, so the import must pipe rendered YAML, which the existing `curl`
  loop in `reimport-jobs.yaml` already does.
- **Deriving `group:` and `tags:` instead of typing them.** `render-job.py` already
  rewrites every job on the way in. Giving each job a `category:` and letting the renderer
  compute the group and tags would make a leftover bucket impossible to create by hand.
  Worth doing, but after the tree lands — the tree is the decision, the derivation is
  bookkeeping.
- **Slice 205's new jobs.** `guest-maintenance.yml`, `lab-descent.yml` and
  `verify-ascent.yml` have no files in `rundeck/jobs/` yet. When they arrive,
  *Guest Maintenance* and *Verify Ascent* are `Operate`, and *Lab Descent* is `Recover`
  with the `destructive` tag. The "36 jobs" count throughout this slice is as of
  2026-08-20 and moves when 205 lands.

### Still unverified

How Rundeck 6 surfaces tag filtering in the job list UI has not been checked against the
live runner. If the filter is buried, the tag axis is theoretical and the three placements
above go back to being judgment calls. Check this during the *Reimport Jobs* run rather
than as separate work.

## 2026-08-23 — catalog-first tree supersedes verb-first plus tags

The 2026-08-21 tree was not implemented. A later product check established that the job-tag
axis it depended on is not available in Rundeck Community, and the repository reached 39 jobs
after the three maintenance jobs landed.

More importantly, verb-first was the wrong primary navigation for application selection. An
operator normally arrives with “I want Jellyfin” or “I need a database,” not “I want to deploy
something.” The useful pattern in the Proxmox Community Scripts and AlternativeTo catalogs is
broad human purpose, narrower application type, then the named application. Hosting kind and
stack are execution attributes and do not belong in that path.

The implemented roots are `Applications`, `Platform`, `Manage`, `Recover`, and `Setup`.
Applications currently project from `catalog/applications.yml`; every other job is classified
in `rundeck/job-groups.yml`. `render-job.py --check` makes those two files a complete partition
of `rundeck/jobs/*.yaml` and rejects a stale source group before the import loop starts.

`Recover` also stopped being a proxy for danger. Remove App and Arm Lab Descent now live under
their actual lifecycle and maintenance intents. Their destructive potential is communicated and
enforced by their names, descriptions, confirmation options and ACLs rather than by putting them
in a folder that describes the wrong task.

The boundary between interfaces is now explicit. Rundeck remains the audited engine for
coordinated changes. Authentik is the launch catalog for installed apps; Uptime Kuma and Grafana
are the normal observation surfaces; PBS owns backup inventory; Watchtower and
unattended-upgrades own routine updates. A later Ntfy action may enter Rundeck contextually
without moving the execution implementation out of Rundeck. `Wire Media Stack` remains under
the neutral `Manage/Integrations` group; generalizing that implementation is separate work.

## 2026-08-23 — live import, execution 280

The runner began at revision `083023c` with **Reimport Jobs** still in `Config`. Execution
280 refreshed the checkout to `2522869`, ran the new complete-tree preflight, and imported
all 39 source jobs with zero failures. The REST readback then reported:

- 39 jobs and 39 unique UUIDs;
- roots `Applications`, `Manage`, `Platform`, `Recover`, and `Setup`;
- zero remaining `Apps`, `Bootstrap`, `Config`, or `Maintenance` roots;
- **Reimport Jobs** under `Setup/Automation`;
- **Check Native App Updates** under `Manage/Applications/Updates`, still schedule-enabled
  for Monday at 06:00.

The runner checkout also read back as the full `2522869e43972c0ab11ee544c3b24bb0986c2b59`.
No rollback was needed. This is the live acceptance that closes the slice.
