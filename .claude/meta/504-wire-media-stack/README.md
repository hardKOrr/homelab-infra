# 504 — Wire media stack playbook

**Status:** built
**Subject:** Media
**Related:** none blocking — the playbook resolves its own registry

## Goal

App-to-app wiring within a stack — Sonarr → Prowlarr, a download client → each *arr, Bazarr
→ Sonarr/Radarr. None existed. **This is the first stack playbook; others clone from it**,
and cloning means changing which task files the loop covers.

Table-driven rather than pair-by-pair: three generic task files cover every connection, and
adding an app kind is an entry in `vars/media-wiring.yml`. It does app-to-app wiring only —
Caddy, Authentik and Kuma wiring stay per-app — and is runnable at any time.

**Direction correction on record.** The original sketch had "registers Prowlarr in Sonarr".
The live API has no such setting: Prowlarr owns the indexer list and pushes outward, so the
connection is a Prowlarr *Application* entry pointing back at Sonarr.

Endpoints come from the `media` registry key (instance-keyed, not role-keyed, because a lab
runs several Sonarrs) or from discovery over `config/apps/*.yml` files declaring
`app.media_kind`. *arr API keys neither source carries are read from the guest's
`config.xml`, since an *arr generates its own key on first start. Three optional `app:` keys
— `media_kind`, `host`, `api_key` — enrol an app in media wiring, so a lab can wire apps
this repo did not deploy.

**Failure policy:** each connection runs in block/rescue. A failure is recorded, the run
continues through every other pair, the Ntfy notification always goes out, and the play then
exits non-zero — a partial run is red in the UI rather than silently incomplete.

Nothing deploys a media app yet; a `sonarr` role writing `media.<instance>` on deploy closes
the loop.

## Remaining

- [ ] End-to-end run of the playbook itself, Plays 1–3 including discovery over SSH — needs
      `config/` populated on the runner; only the wiring plays have been exercised
- [ ] The "Media stack wired: N connections confirmed" Ntfy notification fires — needs a lab
      with Ntfy bootstrapped; `tasks/notify.yml` is the shared, already-built publisher
- [x] Running against an empty stack is a no-op
- [x] Registering an *arr in Prowlarr works
- [x] Re-run is idempotent — 12 live connections all reported `confirmed`, no writes issued

## Links

- `ansible/playbooks/stacks/wire-media-stack.yml`
- `ansible/vars/media-wiring.yml` — the app-kind table
- `ansible/tasks/app-wiring/` — `resolve-media-registry.yml`, `prowlarr-application.yml`,
  `arr-download-client.yml`, `bazarr-arr.yml`
- `ansible/vars/CONTRACT.md` — the `media` key and the three `app.media_*` keys
- `rundeck/jobs/wire-media-stack.yaml`, `semaphore/project.json`
- [notes.md](notes.md) — what the partial live run covered
