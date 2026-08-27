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

## 2026-08-23 — the operator rejects the verb-first Manage branch

The tree built earlier today shipped and the operator used it. The verdict on
`Manage/Applications`: "we're on a 1-click deploy platform we built and then ask for a
bunch of hyper specific information."

That is exactly right, and it was a defect of the job set, not of the tree. `Backup App`
asked for an instance and an app. `Rollback Container` asked for a stack tag, a container
name and a version. `Remove App` asked for an instance, an app and a delete flag. Every one
of those questions except the version and the flag has an answer the repository already
holds — in `catalog/applications.yml`, in `ansible/vars/app-defaults/<app>.yml`, or in the
instance filename. Grouping by verb made the questions necessary: one job serving sixteen
applications cannot know which one the operator means.

**Decision: the leaf of the tree is the application.** `…/<App>` holds Deploy; `…/<App>/
Maintenance` holds that application's day-2 jobs. Requested shape, verbatim:
`Media > Library > Sonarr > Deploy + Maintenance(Backup, Configure, Tail, Remove, Restart,
Migrate, Update)`.

Two scope questions went back to the operator, both answered 2026-08-23:

- Platform services get the same treatment as catalog applications, not a flat exception.
- `Recover/Applications` disappears: Restore and Rollback move into the application's own
  Maintenance folder. A restore belongs next to the backup that produced the snapshot,
  which is where the operator is already looking. `Recover` keeps Vaultwarden Recovery.

### Generated, not written

102 jobs from 39 source files. Eight day-2 templates in `rundeck/jobs/` carry `%SLUG%`,
`%NAME%`, `%STACK%`, `'%GROUP%'` and `'%UUID%'`; `rundeck/app-actions.yml` says which
hosting kinds implement each; `render-job.py` expands one template into one job per
applicable application, with a `uuid5` identity per (action, application) so execution
history survives every later reimport.

The import loops did not change. A template renders as a multi-job YAML document, which the
Rundeck import endpoint accepts exactly like the single-job ones it already posted.

### What the expansion answers, and what it refuses to

Answered: instance (prefilled with the app's own name), the app an instance belongs to,
the stack tag, and Migrate's `source_config_path` default. Left as questions: an image tag,
a PBS snapshot, a delete flag, a source host — genuinely unknowable.

Refused: an action its hosting kind does not implement. Backup and Restore exist for
Kubernetes workloads only, because they start a CronJob that only a Kubernetes deploy
installs. Rollback exists for Docker apps only. This is why Sonarr's folder has no Backup
job even though the operator's sketch listed one — a button that fails when pressed is
worse than an absent button, and the honest gap is recorded here rather than papered over.

"Update" from the sketch has no job either, and deliberately: re-running Deploy **is** the
update path for a native app, and Watchtower owns it for a Docker app. `Check Native App
Updates` stays one lab-wide scheduled sweep under `Manage/Lab/Updates`.

### The gap the expansion exposed, and closed

`Restart App` and `Tail App Log` ran `/usr/local/bin/lab-restart-app` on a host named after
the instance. Correct for the four native LXC apps that own a guest; wrong for the ten
Docker apps, whose instance name is not a guest at all — the guest is the shared stack host
and the app is a Compose project under `/opt/<instance>`. Grouped by verb this was invisible:
one job, and whoever ran it against a Docker app got an inventory error and assumed they had
typed something wrong. Expanded per application it would have been ten broken buttons.

`ansible/tasks/maintenance/resolve-app-target.yml` now resolves the target and the install
kind once, and both playbooks serve both hosting kinds. `restart-app.yml` also moved off its
hand-rolled `uri` call onto `tasks/notify.yml`, the documented notification seam.

### Multiple instances: a dropdown, not a memory test

Baking the instance in would have broken the second-instance case the Deploy jobs already
support. Instead every generated job's `instance` option keeps the app name as its default —
still one click — and takes its value list from
`/var/lib/rundeck/app-instances/<app>.json`, written by `ansible/scripts/app-instances.py`
before and after every `lab-run`. A Configure job publishes the file it created before it
exits, so every later Radarr form sees it without a reimport.

Rejected: enumerating instances into the job definition at import time. Correct only until
the next Configure job, and a stale list is worse than no list.

The instance → application link is the instance filename; longest slug wins. Single-estate
labs use `<app>[-<variant>]`. Once two or more estates exist, one explicit `default: true`
is mandatory and estate-scoped applications use `<app>-<estate>[-<variant>]` for every
estate, including the default. There is no unnamed primary estate.

### Withdrawal

Import is additive, so the eight retired generic jobs would have survived as clickable
orphans in groups nothing else occupies — the reorganization correct in the repo and wrong
in the UI. `rundeck/retired-jobs.yml` names their UUIDs and **Reimport Jobs** deletes them
after importing. That deletion ships inside the new Reimport definition, so this change
needs **two** Reimport runs: the first imports the definition that can delete, the second
deletes.

### Reversals recorded

- `configure-app.yaml`'s header argued for one generic Configure job on the grounds that
  which app an instance belongs to is decided by the Deploy job. True, and it still left the
  operator at a form with an empty instance box and no sign of which app it was about to
  configure. Reversed.
- `Manage/Applications` and `Recover/Applications`, both introduced this morning, are gone.
  `Manage` now holds only what is scoped to the lab.

## 2026-08-23 — pre-import review corrections

The focused review found four runtime gaps that the structural renderer test did not answer:

- Deploy jobs had no `valuesUrl`; only generated Maintenance jobs used the instance files.
- `lab-run` refreshed those files before Configure wrote a new instance, leaving the next
  form one execution behind.
- Rollback baked in the app default stack instead of reading an instance override, and it
  offered buttons for Authentik and Observability even though each has multiple images and
  the rollback playbook selects only one service.
- Reimport printed a failed retired-job deletion but could still exit zero.

All four were corrected before live import. Rollback now uses `resolve-app-target.yml` and
is excluded for the two multi-service applications. Every application job receives the live
instance provider. `lab-run` refreshes the provider before and after Ansible. Retirement
failures contribute to Reimport's exit status, and the renderer rejects a retired UUID that
is still active.

The same review exposed an identity ambiguity in the existing estate contract: an omitted
`default: true` made the first YAML entry the default, and instance names omitted that estate
while naming every later one. The settled rule is symmetric. A `domains:` map with two or
more entries declares exactly one default, and every estate-scoped instance is
`<app>-<estate>[-<variant>]`, including the default estate. Lab-scoped platform services are
not renamed because one instance serves every estate.

## 2026-08-23 — the estate suffix must not reach the URL

Phase 2's per-application expansion, then `f3da324`'s estate-aware naming, left one thing
unpaid. `routing.subdomain` falls back to the **instance name**, and only Authentik ever
declared a default (`subdomain: auth`). Marking eight applications `scope: estate` therefore
renamed their instances to `<app>-<estate>` and, with them, their published hostnames:
`radarr-personal.personal.example.com`.

**Every routed application now declares its own `routing.subdomain`.** The value is the app
slug, which is what the fall-back already produced for a canonical single instance, so no
existing lab's URL moves. What changes is that the URL no longer follows the filename: the
instance name is free to carry identity — filename, guest hostname, Proxmox tag, dropdown
entry — while the address stays `radarr.personal.example.com`.

Caddy is exempt (`proxy: none` — it is the proxy). The k3s cluster declares one although
nothing wires it today, so the gate's rule needs no exception list.

The gate now asserts it: a routed application with no `routing.subdomain` fails.

### `scope` is required, and the shared set is named

Estates are separate. The earlier framing — "an estate is a domain scope with its own SSO
instance, sharing the rest of the platform" — was rejected by the operator and is gone from
CONTRACT §5. What replaces it: estates are independent, and the shared set is an explicit
named list rather than a residue.

Mechanically that means `scope` has no default. It was `entry.get("scope") or "lab"`, which
put an unclassified application on the shared side silently — the exact implicitness the
decision was meant to remove. It is now rejected at render time, and every `scope: lab`
entry in the catalog carries the reason it earns the exception.

### Defaults are lab-agnostic, and that is now enforced

`ansible/vars/app-defaults/mixpost.yml` declared `routing.estate: foxglove` — one lab's
estate name in a git-managed file this project intends to be cloned. A fresh clone with
different estate names inherits a value its own config-doctor rejects, and the renderer was
prefilling a one-click deploy from it.

The first attempt was to tolerate it: prefer a declared estate, fall back when the lab has
never heard of it. Rejected on the operator's call — "defaults should be agnostic of my lab
for sure". Tolerating a value that must never exist keeps the failure mode alive and adds a
branch that only one file exercises. `routing.estate` in `app-defaults/` is now a hard error
naming the file, `default_instance()` is back to one line, and the gate asserts no
application declares one.

Two more instances of the same leak went with it: `deploy-mixpost.yaml` told every clone the
app is "published on the foxglove estate", and `config.example/apps/mixpost.example.yml`
handed a copying user `estate: foxglove` as a starting value.

**The live consequence, accepted:** Mixpost's estate is now whatever
`config/apps/<instance>.yml` says. That is not a new burden — the multi-estate naming rule
already requires the instance to be named `mixpost-<estate>`, and config-doctor already
rejects a name whose estate disagrees with its `routing.estate`. Authoring the estate is
therefore forced by the rename the estate rules already demand.

**Not a leak, checked:** `ansible/vars/app-defaults/k3s-cluster.yml` carries
`192.168.0.21-23` and a `192.168.0.30` ingress VIP. Those are override-me placeholders on a
subnet this lab does not use, documented as values every lab must set. The distinction that
matters: a placeholder is merely wrong until replaced, while a value that must match a
*named declaration in another config file* fails validation outright.

## 2026-08-26 — accepted

The operator confirmed that the live Rundeck job tree received the organization update and
accepted the slice for closure. Individual generated maintenance actions were not represented as
separately exercised by this acceptance.
