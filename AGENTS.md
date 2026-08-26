# homelab-infra

This repository provides Ansible automation for deploying and operating a homelab on
Proxmox. Ansible is the implementation surface. Rundeck is the supported operator UI.
Semaphore files are retained as a reference and are not maintained at feature parity.

## Working in This Repository

- Preserve unrelated work and live lab state.
- Do not expose or commit credentials, tokens, private keys, generated secrets, or files
  under `/config/`.
- Treat `config.example/` as documentation and `/config/` as user-owned runtime state.
- Inspect the relevant implementation and its nearest `README.md`, when one exists, before
  changing it.
- Keep `ansible/` independent of Rundeck and other operator interfaces.
- Change only resources owned by homelab-infra. Existing untagged Proxmox resources are
  outside project authority.
- Use `combine(recursive=True)` when updating `homelabinfra_config`,
  `homelabinfra_instance`, or `homelabinfra_infra`. Do not replace one of these mappings
  with a partial mapping.
- Keep reusable behavior in roles or task files. Keep playbooks focused on orchestration.
- Make deployment and day-2 operations safe to run again.
- Before a destructive or disruptive action, identify the exact target and verify the
  applicable maintenance and recovery behavior.

## Read Documentation by Area

Start with the nearest `README.md` for the area being changed. Load deeper contracts only
when the task reaches their subject.

| Area | Read |
| --- | --- |
| Repository use and operator entry points | `README.md` |
| Application playbooks and hosting decisions | `ansible/playbooks/apps/README.md` |
| Configuration schema and `homelabinfra_*` variables | `ansible/vars/CONTRACT.md` |
| Application catalog | `catalog/README.md` |
| Architecture and documentation map | `docs/README.md`, then `docs/architecture.md` when needed |
| Reviewable implementation contracts | `docs/specs/README.md`, then the applicable specification |
| Verification commands and test selection | `gate/README.md` |
| Rundeck jobs, rendering, and bootstrap behavior | `rundeck/README.md` |
| Semaphore reference files | `semaphore/README.md` |

Some subdirectories have a more specific `README.md`. Read it before changing that
subsystem.

`docs/meta/` records work state and historical context. It is not an implementation
contract. Verify historical statements against the current code and normative documents.

## Repository Areas

- `ansible/` contains provisioning, configuration, application roles, maintenance flows,
  and shared automation.
- `catalog/` declares applications exposed through the automation platform.
- `config.example/` documents user configuration without containing live values.
- `docs/` contains architecture, review contracts, and historical work records.
- `gate/` contains local static checks and focused regression tests.
- `rundeck/` contains the supported operator jobs and their renderer.
- `semaphore/` contains an unmaintained reference integration.

Search the repository when exact files or seams are needed. Do not duplicate directory
trees, generated job inventories, bootstrap order, or other implementation-derived lists
in this file.

## Verification

Run the checks selected by `gate/README.md` from the repository root. The standard
commands are:

```text
wsl bash -lc 'bash gate/lint.sh'
wsl bash -lc 'bash gate/test.sh'
```

Use WSL for these commands. Do not start a second gate while an earlier gate process is
still running.
