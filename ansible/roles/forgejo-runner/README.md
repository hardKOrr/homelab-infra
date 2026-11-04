# forgejo-runner

Deploys a Forgejo Actions runner registered against a named Forgejo instance: Docker with a
Docker-in-Docker sidecar (`tasks/main.yml`), Kubernetes (`tasks/kubernetes.yml`), or
natively (`tasks/native.yml`). All three share `tasks/registration.yml`; the runner keeps no
state worth a backup beyond its registration, which a later Deploy repairs.

## Native

With `hosting.method: native` (`proxmox.guest_type: lxc` or `vm`) the runner is the release
binary from the upstream installation guide, at the version and sha256 pinned in
`app.native_version` and `app.native_sha256` (the image tag pins the same version). systemd
runs the same registration entrypoint as the other methods under an unprivileged account.
The guest has no Docker engine, so the runner registers `app.native_labels` (`docker:host`)
and jobs run directly on the guest.
