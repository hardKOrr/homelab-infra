# Configuration examples

This directory documents the user-owned files that live under the repository-root
`config/` directory on the runner. The examples contain no live values and are safe to
track. The resulting `config/` directory is runtime state and must remain untracked.

Rundeck bootstrap writes the initial configuration. Use these examples when reviewing the
available settings or when preparing configuration manually.

Initial outbound mail is optional (`LAB_MAIL_PROVIDER=none` by default). For `smtp`,
bootstrap writes only the nonsecret relay fields shown in `infrastructure.yml`. Supply
`LAB_MAIL_PASSWORD` through a hidden prompt or a root-private environment file; it goes
only to `secrets.d/mail.env` outside the checkout. Existing answers and the sink survive
reruns. After cutover use **Store Secret**, item `homelab-infra/mail`, field `password`.
The [bootstrap input guide](../rundeck/README.md#what-it-asks) describes unattended loading.
Synthetic owner addresses do not require working mail.

## Files

- `proxmox.yml` documents the Proxmox connection, placement, storage, and network shape.
- `infrastructure.yml` documents platform services, providers, domains, and shared policy.
- `apps/<app>.example.yml` documents optional overrides for one application instance.
- `apps/_template.example.yml` is the starting point for an application that does not yet
  have an example.

The authoritative schema and merge behavior are in
[`../ansible/vars/CONTRACT.md`](../ansible/vars/CONTRACT.md). Application defaults live in
`ansible/vars/app-defaults/`. An instance file should contain only values that differ from
those defaults.

## Safety

- Do not put credentials or generated secret values in an example file.
- Do not commit files from the repository-root `config/` or `artifacts/` directories.
- Store post-cutover secrets through the Rundeck **Store Secret** job.
- Use an application's **Configure** job for normal instance overrides. Use these examples
  as the field reference, not as a second configuration source.
