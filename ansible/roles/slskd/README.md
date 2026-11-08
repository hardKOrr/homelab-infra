# slskd

`tasks/main.yml` resolves the Soulseek login, API key and web password, then runs one hosting
method: `storage.yml` (directories and the lab's mounts) with `docker.yml` (Compose on the
stack host) or `native.yml`, or `kubernetes.yml`; `verify.yml` proves the key, the Soulseek
login and the refused anonymous call for all of them.

## Native

With `hosting.method: native`, `native-install.yml` installs the pinned release zip from
slskd's GitHub releases (binary install in its README), a service account and the PBS client;
`storage.yml` follows. `native.yml` writes `slskd.env` (the same `SLSKD_*` settings as the
Compose file, mode 0600) and the systemd unit. `config_path` is the app directory, the
Docker image's `/app` layout; Docker and native share their backup and restore tasks.
The defaults pin `app.native_version` and the image tag to one upstream release.

## Kubernetes

`hosting.method: kubernetes` runs after the playbook's prepare play ran
`resolve-kubernetes-media.yml`. `/app` lives on a default-StorageClass PVC; the download and
shared directories are the lab's NFS exports, mounted at `/downloads/complete`,
`/downloads/incomplete` and `/shared/<n>` as on Docker. The credentials reach the pod as a
Secret. Kubernetes writes no media registry entry, and the inbound Soulseek peer port is not
published. `restore.yml` replaces the PVC through the shared recovery Job, or the app directory
through the shared swap (`restore-guest.yml`) on Docker and native.
