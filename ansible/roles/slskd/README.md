# slskd

`tasks/main.yml` resolves the Soulseek login, API key and web password, then runs one hosting
method: `docker.yml` (Compose on the stack host) or `kubernetes.yml`; `verify.yml` proves the
key, the Soulseek login and the refused anonymous call for both.

## Kubernetes

`hosting.method: kubernetes` runs after the playbook's prepare play ran
`resolve-kubernetes-media.yml`. `/app` lives on a default-StorageClass PVC; the download and
shared directories are the lab's NFS exports, mounted at `/downloads/complete`,
`/downloads/incomplete` and `/shared/<n>` as on Docker. The credentials reach the pod as a
Secret. Kubernetes writes no media registry entry, and the inbound Soulseek peer port is not
published. `restore.yml` replaces the PVC through the shared recovery Job; the Docker method
has no application recovery.
