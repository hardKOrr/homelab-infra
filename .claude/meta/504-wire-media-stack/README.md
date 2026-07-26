# 504 — Wire media stack playbook

**Status:** built (live acceptance partially observed — see notes.md)
**Depends on:** none in practice — the playbook resolves its own registry
**Blocks:** nothing

## Problem

CLAUDE.md describes `wire-<stack>.yml` playbooks for app-to-app wiring within a stack
(Sonarr → Prowlarr, Radarr → downloader, etc.). None existed.

This is the first stack playbook — others can be cloned from it.

## Files

- `ansible/playbooks/stacks/wire-media-stack.yml` — the job
- `ansible/vars/media-wiring.yml` — app-kind table (API versions, implementation names,
  category fields, protocols)
- `ansible/tasks/app-wiring/resolve-media-registry.yml` — registry + discovery
- `ansible/tasks/app-wiring/prowlarr-application.yml` — one *arr → a Prowlarr Application
- `ansible/tasks/app-wiring/arr-download-client.yml` — one download client → one *arr
- `ansible/tasks/app-wiring/bazarr-arr.yml` — Bazarr → one Sonarr / one Radarr
- `ansible/vars/CONTRACT.md` — the `media` registry key and the three `app.media_*` keys
- `semaphore/project.json`, `rundeck/jobs/wire-media-stack.yaml` — the "Wire Media Stack" job

## Approach

Table-driven, not pair-by-pair. Three generic task files cover every connection; adding an
app kind is an entry in `vars/media-wiring.yml`. The playbook is idempotent and runnable at
any time; it only does app-to-app wiring (Caddy/Authentik/Kuma wiring stays per-app).

**Direction correction.** The original sketch had "registers Prowlarr in Sonarr". The live
API has no such setting: Prowlarr owns the indexer list and pushes it outward, so the
connection is a Prowlarr *Application* entry pointing back at Sonarr. Implemented that way.

**Endpoints** come from `homelabinfra_infra.media` (registry) or from discovery over
`config/apps/<instance>.yml` files declaring `app.media_kind`; *arr API keys that neither
source carries are read from the guest's `config.xml`, because an *arr generates its own key
on first start.

**Failure policy.** Each connection runs in a block/rescue: a failure is recorded, the run
continues through every other pair, the Ntfy notification always goes out, and the play then
exits non-zero so a partial run is red in the UI rather than silently incomplete.

## Acceptance

- [x] Running wire-media-stack on an empty stack is a no-op (no failures)
- [x] Registering an *arr in Prowlarr works, and a re-run is idempotent
- [x] Re-run is idempotent — 12 live connections all report `confirmed`, no writes issued
- [ ] Ntfy notification "Media stack wired: N connections confirmed" fires — needs a lab
      with Ntfy bootstrapped; `tasks/notify.yml` is the shared, already-built publisher
- [ ] End-to-end run of the playbook itself (Plays 1–3, including discovery over SSH) —
      needs `config/` populated on the runner; only the wiring plays have been exercised
