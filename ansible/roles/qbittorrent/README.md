# qBittorrent

`tasks/main.yml` is Deploy's entry; its phases live in `tasks/main/`: `resolve.yml` (kind,
mounts, endpoint), `storage.yml` (directories, download path), `credentials.yml` (WebUI
credential), `start.yml` (seeded config, peer port settings, Compose) and `verify.yml` (login and password repair, identity,
refused session-less call). `main.yml` then stores the credential and exports the endpoint.

## Native

`hosting.method: native` runs `main/native-install.yml` (the pinned `qbittorrent-nox`
package, the service account, the PBS client) before `storage.yml`, then `main/native.yml`
(seeded config, peer port settings, systemd unit) instead of `start.yml`. The unit sets
`XDG_CONFIG_HOME` and `XDG_DATA_HOME` to `config_path`, so the config layout is the Docker
image's and one backup and restore serves both. `app.native_version` and the image tag
name the same upstream release.

## Kubernetes

`hosting.method: kubernetes` runs `tasks/kubernetes.yml` instead of `storage.yml` and
`start.yml`; the playbook's prepare play first runs `resolve-kubernetes-media.yml`. The
config sits on a default-StorageClass PVC. Every `media_storage` mount is mounted from its
existing NFS share at the same path as on Docker. The pod's init container seeds
`qBittorrent.conf` once and enforces the WebUI credential (from a Secret) and the peer
port range before every start. The BitTorrent listen port is not exposed outside the
cluster, no media registry entry is written and the *arr apps are not wired to it.
`restore.yml` dispatches to `restore-kubernetes.yml` (shared Job) or `restore-guest.yml`.

## Native recovery

qBittorrent native recovery stops only the selected service and archives its config,
preferences and torrent resume metadata as one PBS `data.pxar` in `host/<instance>`.
Logs and downloads are excluded; payloads remain on the external media mount. Backup
keeps the two newest snapshots alongside daily retention. Restore previews until
`overwrite=true`, validates the backup group and staged configuration, and retains the
original config on the same filesystem until the canonical WebUI credential works.
Failure puts the original state back and starts it. Deploy preserves
recovered preferences and torrents and registers only this client in deployed torrent
consumers. Remove withdraws only entries that address the owned client.
