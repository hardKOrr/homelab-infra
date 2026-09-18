# 134 — Batch C: deploy Emby

**Status:** built
**Subject:** Emby as an independent media server on `media_stack`
**Related:** [closed catalog issue #408](https://github.com/hardKOrr/homelab-infra/issues/408), [observation issue #253](https://github.com/hardKOrr/homelab-infra/issues/253)

## Goal

Deploy Emby as a third independent option on the existing `media` Docker stack. Emby
creates and retains its own user database behind a catalog-only Authentik identity, reads
only the media mounts already granted to Jellyfin/Plex, and owns a quiesced `/config`
backup/restore path. Removal acts on the named Compose project and its app-owned state only;
shared libraries are not removed.

## Remaining

- [x] Repository implementation covers defaults, its dedicated API-configured role and
      playbook, example, catalog identity, Rundeck deploy job, provider wiring, native
      backup/restore, and generic project-scoped removal.
- [x] Synthetic contract verification renders Compose against named fixture paths and
      checks read-only binds, app-owned auth/API setup, config-only archive/restore scope,
      and generic named-project removal. The provider-free recovery acceptance fixture also
      runs Emby through its new/existing synthetic recovery protocol; neither fixture claims
      an actual Emby container or live PBS restore.
- [ ] Live evidence is deferred to the self-contained [observation issue #253](https://github.com/hardKOrr/homelab-infra/issues/253). Proposed
      disposable target: instance `emby-acceptance` on stack host `stack-media-acceptance`.
      Before execution #253 must authorize and record the exact existing fixture library
      mount paths and `media_storage.library` root; no real media path is approved or
      inferred here. On that target only, deploy, confirm the named fixture library is
      readable but not writable from the Emby container, rerun, take and restore a PBS
      point, then remove only project `emby-acceptance` with data deletion disabled. Verify
      the fixture mount and contents remain untouched. This is deferred evidence, not a
      production cutover or cleanup request.

## Links

- `ansible/vars/app-defaults/emby.yml` — stack, catalog identity and recovery contract
- `ansible/roles/emby/` — Emby setup/auth, read-only libraries and native config recovery
- `ansible/playbooks/apps/emby.yml` — Proxmox, Docker and provider orchestration
- `config.example/apps/emby.example.yml` — non-secret instance example
- `catalog/applications.yml`, `catalog/recovery.yml`, `rundeck/jobs/deploy-emby.yaml` — operator surfaces
- `gate/test-emby-contract.py`, `gate/recovery_acceptance.py` — synthetic contract and provider-free restore checks
