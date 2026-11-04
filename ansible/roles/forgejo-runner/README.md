# forgejo-runner

Deploys a Forgejo Actions runner registered against a named Forgejo instance: Docker with a
Docker-in-Docker sidecar (`tasks/main.yml`), Kubernetes (`tasks/kubernetes.yml`), or
natively (`tasks/native.yml`). All three share `tasks/registration.yml`; the runner keeps no
state worth a backup beyond its registration, which a later Deploy repairs.

## Native

Selecting `hosting.method: native` with an LXC or VM guest installs the release
binary from the upstream installation guide, pinned by version and sha256 in
`app.native_version` and `app.native_sha256`; the image tag carries that same version. systemd
runs the same registration entrypoint as the other methods under an unprivileged account.
The guest has no Docker engine, so the runner registers `app.native_labels` (`docker:host`)
and jobs run directly on the guest.
