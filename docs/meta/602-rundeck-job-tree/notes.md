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
Deploy/Apps        Mixpost                       ← 408 subdivides here
Operate            Lab Status, Tail App Log, Restart App, Check Native App Updates,
                   Wire Media Stack, Backup App
Operate/Config     Config Doctor, Get Config, Configure App, Store Secret
Recover            Rollback Container, Restore App, Reclaim Volume, Remove App,
                   Vaultwarden Recovery
Setup              Bootstrap Platform, Vaultwarden Enrollment, Vaultwarden Cutover,
                   Reimport Jobs
```

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

## Open — operator's call

| Question | Chosen | The case against |
|---|---|---|
| Where does *Remove App* go? | `Recover` — its `delete_data` option destroys volumes, the same blast radius as the rest of that group | Removal is the inverse of deployment and operators look for it beside *Deploy*; `Deploy/Lifecycle` is defensible |
| Where does *Wire Media Stack* go? | `Operate` — idempotent and safe to re-run, which is what that group means | It only ever concerns media apps, so `Deploy/Media` keeps the whole media workflow in one place |
| Does `Setup` earn a fourth root? | Yes — these are things done *to the lab*, not to an app in it | It is four jobs, three of which run about once; `Operate/Setup` would give three roots |

## Dead end — this was first written as a published artifact

The proposal was initially delivered as an Artifact rather than a slice. That was wrong:
this repository records intent, scope and open decisions in `docs/meta/`, and a second
home for the same facts is exactly what "one fact, one home" in `docs/meta/README.md`
forbids. The artifact is superseded by this slice and carries no information this file
does not.
