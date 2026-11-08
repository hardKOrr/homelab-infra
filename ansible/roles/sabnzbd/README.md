# SABnzbd

Deploy's `tasks/main.yml` imports `tasks/main/resolve.yml` (kind, download directories),
`storage.yml` (mounts, owned download directories, host whitelist), `start.yml` (API key
continuity, seeded `sabnzbd.ini`, Compose) and `verify.yml` (key probe and repair, identity,
refused anonymous queue); the vault write and registry export follow in `main.yml`.

## Kubernetes

`hosting.method: kubernetes` swaps `storage.yml` and `start.yml` for `tasks/kubernetes.yml`,
after the playbook's prepare play ran `resolve-kubernetes-media.yml`. Settings live on a
PVC from the cluster's default StorageClass, and the pod sees each declared media mount
through its NFS export under the container path Docker uses. An init container seeds
`sabnzbd.ini` once and, before every start, sets the API key held in a Secret. Kubernetes
writes no media registry entry and wires no *arr to the client. `restore.yml` hands off to
`restore-kubernetes.yml` (the shared Job) or `restore-guest.yml`.

## Client wiring

SABnzbd Deploy registers only that client in each deployed Usenet consumer and creates
missing consumer categories. Existing categories and other client entries are preserved.
Consumer failures are recorded while the remaining connections are attempted; the play's
final degradation check fails the run with the collected problems. Wire Media Stack still
reconciles all compatible download clients and the other media connections across the stack.
Remove withdraws only its named entries after checking that they address the owned client.

## Native recovery

SABnzbd native recovery stops only the selected service and captures its configuration
and history as one PBS `data.pxar` in `host/<instance>`. Logs and download directories
are excluded; external media mounts stay outside the archive. Besides daily retention,
Backup retains the latest two snapshots. Without `overwrite=true` Restore only previews; it
checks the source group and keeps the original directory beside the config path until
the archived API key passes an authenticated queue check and is published to the canonical
credential item. A failure puts the original directory back and starts it. Deploy keeps the
recovered INI, API key, categories and application settings.
