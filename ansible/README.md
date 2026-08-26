# Ansible implementation

This directory contains the provisioning and operating implementation. It must remain
independent of Rundeck and other operator interfaces. Operator interfaces select and run
playbooks; they do not define Ansible behavior.

## Start with the relevant guide

| Task | Read |
| --- | --- |
| Add or change an application | [`playbooks/apps/README.md`](playbooks/apps/README.md) |
| Change configuration shape or a `homelabinfra_*` variable | [`vars/CONTRACT.md`](vars/CONTRACT.md) |
| Change module boundaries or execution flow | [`../docs/architecture.md`](../docs/architecture.md) |
| Change configuration loading or dictionary updates | [`../docs/specs/config-layering.md`](../docs/specs/config-layering.md) and [`../docs/specs/namespace-merge-discipline.md`](../docs/specs/namespace-merge-discipline.md) |
| Change platform wiring | [`../docs/specs/provider-noop-wiring.md`](../docs/specs/provider-noop-wiring.md) |
| Change secret handling | [`../docs/specs/secrets-handling.md`](../docs/specs/secrets-handling.md) |
| Select and run verification | [`../gate/README.md`](../gate/README.md) |

## Areas

- `playbooks/` contains orchestration entry points for deployment, removal, maintenance,
  provisioning, stacks, and bootstrap.
- `tasks/` contains reusable flows and integration seams shared by playbooks and roles.
- `roles/` configures applications and platform components. The `_template-*` roles are
  the starting points for new applications.
- `vars/` contains Git-managed defaults and the authoritative configuration contract.
- `inventory/` resolves the managed Proxmox estate into Ansible hosts and groups.
- `scripts/` contains runner entry points and committed helpers used by jobs and tasks.
- `callback_plugins/` defines the common job and terminal output.
- `files/` contains controller-side helper programs installed or called by tasks.

Search these areas for the exact implementation seam. Do not maintain a complete file
inventory in documentation.

## Local conventions

- Keep playbooks focused on orchestration. Put reusable behavior in a task file or role.
- Preserve sibling keys when updating shared `homelabinfra_*` mappings. Follow
  `vars/CONTRACT.md` for merge and namespace rules.
- Treat `config/.generated/facts.yml` as generated topology, not a secret store.
- Keep application deployment and day-2 playbooks safe to run again.
- Run verification from the repository root as documented in `gate/README.md`.
