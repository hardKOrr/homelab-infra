# Architecture

homelab-infra uses Rundeck as its supported operator interface and Ansible as its
UI-independent implementation. A job invokes one playbook. The playbook resolves user
configuration, changes only project-owned infrastructure, configures the application, and
wires it to the selected platform services.

This file maps component relationships. Read the linked local README or specification for
the contract of an individual area.

## System shape

```text
operator
   |
Rundeck job
   |
runner entry point ---- Vaultwarden runtime secrets
   |                              |
Ansible playbook <---- merged config and generated topology
   |
   +---- Proxmox API and managed guests
   +---- application role or Kubernetes namespace
   +---- reverse proxy, identity, monitoring, and DNS APIs
```

Rundeck owns presentation, secure job inputs, and job import. Ansible owns behavior. The
operator interface must not change configuration layering, target selection, deployment,
or wiring semantics.

## Component ownership

| Component | Responsibility | Local guidance |
| --- | --- | --- |
| `ansible/` | Provisioning and operating implementation | [`ansible/README.md`](../ansible/README.md) |
| `config/` | Untracked user configuration, backups, and topology generated on the runner | [`config.example/README.md`](../config.example/README.md), [`ansible/vars/CONTRACT.md`](../ansible/vars/CONTRACT.md) |
| `catalog/` | Operator-facing application classification | [`catalog/README.md`](../catalog/README.md) |
| `rundeck/` | Supported runner bootstrap, jobs, rendering, and secure inputs | [`rundeck/README.md`](../rundeck/README.md) |
| `semaphore/` | Legacy, unverified reference integration | [`semaphore/README.md`](../semaphore/README.md) |
| `docs/specs/` | Reviewable implementation contracts | [`specs/README.md`](specs/README.md) |
| `gate/` | Static checks and focused regression tests | [`gate/README.md`](../gate/README.md) |
| `docs/meta/` | Work state and historical evidence; not a current contract | [`meta/README.md`](meta/README.md) |

## Core flows

### Application deployment

An application playbook has three responsibilities: provision or select its hosting
target, run the application role or Kubernetes manifest, and wire the result to configured
platform providers. State crosses plays through explicit `add_host` host variables; facts
set on one host are not implicitly available on `localhost` or another host.

Application authoring and hosting selection are documented in
[`ansible/playbooks/apps/README.md`](../ansible/playbooks/apps/README.md). Optional provider
behavior is defined by [`specs/provider-noop-wiring.md`](specs/provider-noop-wiring.md).

### Bootstrap

`ansible/playbooks/bootstrap.yml` composes application playbooks in dependency order. Each
service records topology before a later service reads it. Read the playbook for the current
order; do not maintain a separate application inventory in documentation.

### Day-2 operations

Maintenance actions resolve the application's current hosting backend before acting.
Automated disruption schedules are enforced by timers on the managed guests. Whole-lab
descent is armed and then executed by the Proxmox nodes because the runner is inside the
shutdown boundary. See
[`ansible/playbooks/maintenance/README.md`](../ansible/playbooks/maintenance/README.md).

### Removal

Removal stops the application, unwires each configured provider, withdraws its guest or
namespace record, and preserves instance configuration unless the action explicitly owns
data deletion. Provider absence is a no-op; a configured provider failure is reported as a
degradation and must not produce a successful result.

## Cross-cutting seams

| Seam | Owner |
| --- | --- |
| Configuration namespaces, schema, and precedence | [`ansible/vars/CONTRACT.md`](../ansible/vars/CONTRACT.md) |
| Recursive updates to shared dictionaries | [`specs/namespace-merge-discipline.md`](specs/namespace-merge-discipline.md) |
| Generated topology and in-memory secret overlay | [`specs/secrets-handling.md`](specs/secrets-handling.md) |
| Proxmox ownership, tag grammar, inventory, and provisioning | [`ansible/tasks/proxmox/README.md`](../ansible/tasks/proxmox/README.md) |
| Kubernetes namespaces, publishing, storage, and availability | [`ansible/tasks/kubernetes/README.md`](../ansible/tasks/kubernetes/README.md) |
| Provider wiring and degradation behavior | [`specs/provider-noop-wiring.md`](specs/provider-noop-wiring.md) |
| One-click and rerun behavior | [`specs/one-click-idempotent.md`](specs/one-click-idempotent.md) |
