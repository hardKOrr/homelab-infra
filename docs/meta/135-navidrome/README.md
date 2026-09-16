# 135 — Batch C: deploy Navidrome

**Status:** built
**Subject:** Navidrome as an independent music server on `media_stack`
**Related:** 408 (application catalog), #198 (deferred isolated real-Proxmox acceptance)

## Goal

Deploy Navidrome as an independently managed Docker application on the existing `media`
stack. It reads only the configured `media_storage.library/music` subpath through a
read-only `/music` bind, while its own `/data` directory holds application state and owns
an application-consistent PBS backup/restore path. Provider wiring covers reverse proxy,
SSO catalog, monitoring, DNS and the Proxmox guest record; removal is limited to the named
Compose project and its application data.

## Remaining

- [x] Repository implementation covers defaults, role, playbook, user example, catalog,
      Rundeck job, provider wiring, native backup/restore and generic named-project removal.
- [x] Synthetic fixture verification renders the Compose file against a named music
      fixture, checks the scoped read-only bind and application-data recovery boundary, and
      runs the provider-free recovery protocol. It does not claim a real Navidrome process
      or live PBS restore.
- [ ] Live evidence is deferred to the existing #198 isolated acceptance lane. Proposed
      disposable target: instance `navidrome-acceptance` on stack host
      `stack-media-acceptance`. Before execution #198 must authorize and record the exact
      existing fixture music mount and `media_storage.library` root; no real music path is
      approved or inferred here. On that target only, deploy, confirm the named music
      subpath is readable but not writable from the Navidrome container, rerun, take and
      restore a PBS point, then remove only project `navidrome-acceptance` with data
      deletion disabled. Verify the fixture mount and contents remain untouched. This is
      deferred evidence, not a production cutover or cleanup request.

## Links

- `ansible/vars/app-defaults/navidrome.yml` — stack, scoped library and recovery contract
- `ansible/roles/navidrome/` — read-only music Compose role and PBS recovery tasks
- `ansible/playbooks/apps/navidrome.yml` — Proxmox, Docker and provider orchestration
- `config.example/apps/navidrome.example.yml` — non-secret instance example
- `catalog/applications.yml`, `catalog/recovery.yml`, `rundeck/jobs/deploy-navidrome.yaml` — operator surfaces
- `gate/test-navidrome-contract.py`, `gate/recovery_acceptance.py` — synthetic contract and recovery checks
