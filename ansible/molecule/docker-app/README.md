# Container role integration harness

Proves that the shared `community.docker` Compose role pattern — [`roles/docker`](../../roles/docker)
plus the [`roles/_template-docker`](../../roles/_template-docker) contract every Docker-hosted
application role follows — renders valid Compose input, converges a real container to a
healthy and inspectable state, is idempotent, and tears down cleanly. Tracks
[hardKOrr/homelab-infra#31](https://github.com/hardKOrr/homelab-infra/issues/31).

## Run it

```bash
bash gate/container.sh
```

The wrapper bootstraps a dedicated venv (Molecule + its Docker driver), then runs
`molecule create → converge → idempotence → verify`, and always runs `molecule destroy`
on exit — including after a failed converge — via a trap. GitHub Actions runs the same
command (see `.github/workflows/gate.yml`'s `container` job); the runner's own
already-present Docker daemon hosts the scenario's one platform container, so nothing
installs Docker on the runner itself.

## Harness choice

Molecule with its Docker driver, per the tracker issue's stated preference. It already
represents this repo's role contracts: `converge.yml` runs ordinary roles from
`ansible/roles/` through ordinary Ansible, so a scenario is a normal play, not a
Molecule-specific abstraction.

## Why a fixture role, not a catalog application role

Every current Docker-hosted catalog role (`bazarr`, `sabnzbd`, `servarr`, `tautulli`, ...)
ends its convergence by reading or writing a Vaultwarden item through
`tasks/bitwarden/upsert-item.yml`, which requires a live, unlocked `bw` CLI session
against a real Vaultwarden organization (`ansible/tasks/bitwarden/authenticate.yml`
asserts `BW_SESSION`). That is exactly the "credential-dependent behavior" the tracker
issue rules out of scope, and sanitized fixtures for it are tracked separately
(`#30`, not yet available).

The `roles/whoami` fixture under this scenario instead instantiates the
`_template-docker` contract directly — create directories, render Compose from
`app_config`, pull, start, wait, then prove initialization with a real probe — against
[`traefik/whoami`](https://hub.docker.com/r/traefik/whoami), a small, deterministic,
credential-free HTTP echo image. It is not a catalog application and does not live under
`ansible/roles/`.

`roles/whoami/tasks/main.yml` documents one live trap this pattern has to guard against:
whoami answers every path with HTTP 200 (it is a catch-all echo server), so the readiness
probe asserts on the response **body** (the `Hostname:`/`IP:` fields whoami emits) rather
than trusting the status code alone — the same discipline `roles/_template-docker`
documents for every catalog role.

## Pattern for a later Docker-hosted role

Once `#30` lands sanitized fixtures (and, where needed, a way to satisfy or stub the
Vaultwarden preflight), a real catalog role can get its own scenario following this one:

1. Add `ansible/molecule/<role-name>/{molecule.yml,converge.yml,verify.yml}`, copying
   this scenario's `molecule.yml` platform block (the Debian-based, systemd-capable image
   `roles/docker` needs) and provisioner `env` block.
2. `converge.yml` sets `instance`, a synthetic `app_config` (test-only host paths and
   values — never a real catalog value), and runs `roles: [docker, <role-name>]` directly
   from `ansible/roles/`, no fixture copy needed once the role does not require a live
   external dependency mid-run.
3. `verify.yml` asserts on the role's own real readiness probe output, plus whatever
   rendered artifact (Compose file, generated config) is worth inspecting independently.
4. Extend `gate/container.sh` to accept a scenario argument, or add one `bash
   gate/container.sh` invocation per scenario to the `container` CI job, once more than
   one scenario exists.

## Non-goals (also true here)

- No Docker install or global Docker configuration change on the CI runner itself —
  the runner's own Docker hosts one Molecule-managed platform container.
- No catalog application image pull, and no proprietary, device-bound, media-heavy, or
  credential-dependent behavior under test.
- No claim of real LXC, Proxmox, reverse-proxy, DNS, or Vaultwarden acceptance.
