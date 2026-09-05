# Kubernetes smoke-test lane

Proves the shared Kubernetes hosting-backend contract — `tasks/kubernetes/resolve-
cluster.yml`, `ensure-namespace.yml`, `apply-manifest.yml` and `unwiring/kubernetes.yml`,
which every Kubernetes-hosted application role uses — against a real API server: applies,
waits for rollout, is idempotent, answers a real HTTP readiness check, and removes its own
namespace cleanly. Tracks
[hardKOrr/homelab-infra#33](https://github.com/hardKOrr/homelab-infra/issues/33).

## Run it

```bash
bash gate/kind.sh
```

The wrapper installs `kind` and `kubectl` if not already on `PATH`, creates a disposable
single-node Kind cluster, installs a `k3s` shim (see below) so the shared tasks run
unmodified, converges, verifies, and always tears down — cluster deletion, namespace
removal, and the shim's own removal all run from an EXIT trap, including after a failed
converge — via `gate/lib-kind-cleanup.sh`. GitHub Actions runs the same command (see
`.github/workflows/gate.yml`'s `kind` job); the runner's own already-present Docker daemon
hosts the Kind node container, so nothing installs Docker on the runner itself.

`gate/kind.sh` refuses to run at all if `/usr/local/bin/k3s` already exists — that is
exactly where a real k3s install (this platform's own `k3s_cluster` role, or a
developer's own lab node) puts its binary, and overwriting it with the shim would
silently replace a real, working `k3s kubectl` with one bound to a throwaway Kind
kubeconfig, with nothing to restore it from afterward. Run this lane on a machine with no
k3s installed at that path (as GitHub-hosted runners always are).

## The smoke matrix

| Test case | Application class | Runner | Fixture | Asserted behavior |
| --- | --- | --- | --- | --- |
| `gate/container.sh` | Lightweight Docker app (`roles/whoami` fixture standing in for a real Docker-hosted catalog role — see `ansible/molecule/docker-app/README.md`) | Molecule + Docker driver | `traefik/whoami` | Renders valid Compose input, converges, is idempotent, passes a body-level readiness probe, tears down. |
| `gate/kind.sh` | Stateless Kubernetes app (FlareSolverr) | Kind | `flaresolverr/flaresolverr` | Applies the real manifest through `apply-manifest.yml`, waits for rollout, is idempotent (second apply reports no change), answers a real HTTP GET with FlareSolverr's own banner on its NodePort, removes its namespace through `unwiring/kubernetes.yml`, confirms the namespace is gone. |

No persistent (stateful) case is included. Every current Kubernetes-hosted catalog role
with a backup contract (Jellyseerr, Mixpost) authenticates to Vaultwarden or an external
API mid-converge — the same credential dependency `ansible/molecule/docker-app/README.md`
documents for Docker-hosted roles — so there is no credential-free representative yet.
Add one once `#30`-style sanitized fixtures cover a stateful role's dependencies, and state
here exactly which backup/restore invariant it verifies, per the tracker issue's acceptance
criteria.

Both lanes run on every pull request unaffected by path filtering: the repository's
current CI cost (one Compose fixture, one single-node Kind cluster) does not yet justify
scoping either to a changed-paths trigger or a scheduled full run. Revisit if a future
catalog row's addition to this matrix changes that.

## Why FlareSolverr, not another Kubernetes-hosted row

FlareSolverr is the one Kubernetes-hosted catalog role with no credential dependency:
`vars/app-defaults/flaresolverr.yml`'s header records that it has `routing.proxy: none`,
stores no Secret, and calls no external API during its own converge — Jellyseerr and
Mixpost both wire routing (Caddy, Authentik, DNS) and read/write Vaultwarden items mid-run,
which is exactly the "credential-dependent behavior" the tracker issue rules out of scope.
FlareSolverr's own fixed NodePort (`app.node_port`, `30191`) also makes its readiness
probe reachable from outside the cluster with a plain Kind `extraPortMappings` entry,
without needing an Ingress or a MetalLB-backed VIP this smoke lane does not stand up.

## Why a `k3s` shim, not a k3s-based cluster

Every `tasks/kubernetes/*.yml` file shells out to `argv: [k3s, kubectl, ...]` —
`k3s` bundles kubectl inside a single binary, and `resolve-cluster.yml` delegates to a
cluster node reached over SSH. Running an actual k3s node under Kind (or under Molecule,
as `gate/container.sh` does for the Docker role harness) would need a systemd-capable,
privileged container and a real multi-node bring-up neither this lane's cost budget nor
its Kubernetes-API-focused scope calls for. `gate/kind-app/k3s-shim.sh` restores the one
subcommand those tasks call — `k3s kubectl <args>` becomes `kubectl <args>` against the
Kind cluster's own kubeconfig — while every task still runs its real, unmodified
`argv`/`delegate_to`/`become` shape against a real API server. See the shim script's own
header for why the kubeconfig path is baked in at install time rather than read from the
caller's environment.

`converge.yml`/`teardown.yml` delegate to `localhost` over `connection: local` rather than
to an SSH-reached node — there is no separate node to reach; the Kind control-plane
container is addressed entirely through `kubectl`/the shimmed `k3s`, the same way
`resolve-cluster.yml`'s real SSH delegation is transparent to every task that calls it.

## Non-goals

- No k3s install, no multi-node cluster, and no claim of node-level cluster-lifecycle
  behavior (`k3s_cluster` role, node join/failure-domain behavior) — that is
  infrastructure this lane does not stand up. Only the application-hosting contract
  above it is under test.
- No catalog application image with a Vaultwarden or external-API dependency mid-converge.
- No claim of real Proxmox, MetalLB, Ingress, or production DNS acceptance.
- No global Docker or Kubernetes configuration change on the CI runner itself — the
  runner's own Docker hosts one Kind node container, deleted at the end of every run.

## Pattern for a later Kubernetes-hosted role

1. Confirm the role has no mid-converge credential or external-API dependency (or that
   `#30`/`#32`-style fixtures/mocks can stand in for it).
2. Add a second `hosts: localhost` play (or a second scenario directory, once more than
   one case exists) that sets a synthetic `app_config`/`instance` and runs
   `roles: [<role-name>]` directly, following `converge.yml`'s shape.
3. State in this README's matrix table exactly which behavior the new case asserts —
   especially for a persistent role, exactly which backup/restore invariant, per the
   tracker issue's acceptance criteria.
4. Extend `gate/kind.sh`/`.github/workflows/gate.yml`'s `kind` job to run the new
   converge/verify/teardown files, or parametrize them, once more than one case exists.
