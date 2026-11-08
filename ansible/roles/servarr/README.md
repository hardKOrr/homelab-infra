# Servarr (Prowlarr, Sonarr, Radarr, Lidarr)

Deploy runs `tasks/main.yml`, whose phases sit in `tasks/main/`: `resolve.yml` (kind, API
version, storage), `start.yml` (directories, mounts, API key, Compose), `verify.yml`
(authenticated status, refused anonymous call, chosen auth method) and `root-folder.yml`
(library root folder). Plugins, the vault write and the registry export stay in `main.yml`.
`start.yml` and `native.yml` share `mounts.yml` and `api-key.yml`.

## Native

Each Servarr app can run on its own LXC or VM (`hosting.method: native`, `proxmox.guest_type`).
`native.yml` unpacks the pinned release tarball under `/opt/<instance>/bin`, seeds
`config.xml` with the API key (a restored `config.xml` brings its own) and runs the
app as a systemd unit under the app's `puid`/`pgid` user. Backup and restore use the same
shared operations as Docker with the `native` runtime; the restore hooks skip the
Compose key overrides, which exist only on Docker.

## Native recovery

Prowlarr, Sonarr, Radarr and Lidarr share Servarr native recovery. Backup stops only the
selected instance and captures its config directory (including `<app>.db`, `config.xml`
and the verified API key) as one PBS `data.pxar` in `host/<instance>`. Logs, `logs.db`
and local `Backups` are excluded; media mounts remain outside recovery. Pruning retains
the two newest snapshots in addition to daily retention, so a same-day pre-restore safety
backup keeps the immediately preceding recovery point. Restore previews
without mutation until `overwrite=true`, validates the selected backup group and staged
database/configuration, then adopts the archived API key in Compose and the canonical
`homelab-infra/media/<instance>` item after an authenticated API check. A subsequent
Deploy keeps that key and the recovered application state.
Restore requires both Compose API-key overrides before replacing state, and retains the
original config directory beside the live path until verified recovery succeeds. A failed
restore puts the original directory and Compose API-key overrides back and starts it.

## Kubernetes

Lidarr, Prowlarr and Radarr can run in their own namespace (`hosting.method: kubernetes`); `main.yml` includes
`kubernetes.yml` for the workload and shares every other phase. Config sits on a PVC of the
default StorageClass. Media and download mounts are the NFS exports the node already uses:
`kubernetes-media.yml` resolves them (and creates the library root folder on the node) and
`tasks/kubernetes/prepare-media-volumes.yml` mounts them at the Docker paths. The API key is
taken from `homelab-infra/media/<instance>` (or generated) and reaches the pod as a Secret, so
`config.xml` is never read; the verify, root-folder and plugin phases call the app through
the ingress (`main/endpoint.yml`). A Kubernetes instance has no stack host and records no media
registry entry. Backup and restore are the shared Kubernetes recovery Job over the config PVC.

`browser/prowlarr-recovery.js` checks the existing drill owner login and Prowlarr
canaries through HTTPS. Use `?drill=create`, then `fresh` before Restore and `verify`
afterwards; it compares the tag, disabled Nyaa.si indexer, UI settings and API key
against private runner scratch. Run with `tools/lab-browser` and instance
`prowlarr-drill-owner` ([tool map](../../../tools/README.md)).

`browser/radarr-recovery.js` uses the same three modes with `radarr-drill-owner`.
It checks an unmonitored The Matrix movie, its tag and profile associations, the
media-management settings and the canonical API key against private runner scratch.
Add `&scope=<drill-name>` to isolate the canary label and saved comparison between drills.
