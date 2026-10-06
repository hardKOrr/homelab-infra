# Ansible implementation

This directory contains the provisioning and operating implementation. It must remain
independent of Rundeck and other operator interfaces. Operator interfaces select and run
playbooks; they do not define Ansible behavior.

## How a run executes

A Rundeck job calls `lab-run`, the installed entry point for
`ansible/scripts/lab-run.sh`. The wrapper reads its runner environment from
`/etc/homelab-infra/lab-run.env` (or `LAB_ENV_FILE`). With `LAB_REFRESH=1`, it
refreshes the checkout to `origin/$LAB_BRANCH` and re-executes the refreshed wrapper;
refresh defaults on for a configured runner and off for a local checkout. `LAB_DOCTOR`
defaults to `1`, validating configuration before the selected playbook runs; set it to
`0` to disable that check. The playbook orchestrates reusable roles and task files.

Before cutover, explicitly allowed Seed-mode runs (`LAB_SEED_MODE=1`) consume the
bootstrap secrets. After the vault-mode marker exists, ordinary runs must unlock
Vaultwarden, and Seed mode cannot bypass that guard. The explicit vault recovery
playbook has its own guarded recovery path.

Platform defaults merge recursively with `config/proxmox.yml` and
`config/infrastructure.yml`; application defaults merge with `config/apps/<instance>.yml`.
Generated topology and the in-memory secret overlay complete the runtime view. See
[`vars/CONTRACT.md`](vars/CONTRACT.md) for the namespaces and exact precedence.

Proxmox guests are owned by the `_+lab` tag. Provisioning and maintenance must preserve
that ownership boundary and leave untagged guests alone.

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

## Local conventions

- Keep playbooks focused on orchestration. Put reusable behavior in a task file or role.
- Preserve sibling keys when updating shared `homelabinfra_*` mappings. Follow
  `vars/CONTRACT.md` for merge and namespace rules.
- Treat `config/.generated/facts.yml` as generated topology, not a secret store.
- Keep application deployment and day-2 playbooks safe to run again.
