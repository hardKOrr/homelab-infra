# homelab-infra

Ansible deploys and operates a Proxmox homelab. Rundeck is the operator UI. `ansible/`
never depends on Rundeck.

## Hard rules

- Never commit or print credentials, tokens, private keys, generated secrets, or anything
  under `/config/`. `config.example/` is documentation; `/config/` is user-owned state.
- This repository, its commits and its PRs are public. Write lab addresses, domains, node
  names and VMIDs only as the placeholders in `docs/lab-placeholders.md`.
- homelab-infra changes only resources it owns: Proxmox guests tagged `_+lab` and the
  files, records and accounts its playbooks create. Never modify an untagged guest.
- Update `homelabinfra_config`, `homelabinfra_instance` and `homelabinfra_infra` only with
  `combine(recursive=True)`. Never replace one of them with a partial mapping.
- Reusable behavior lives in roles and task files. Playbooks only orchestrate.
- A role uses the shared task or template for any operation that has one (see Shared
  operations in `ansible/README.md`); per-app behavior goes in that shared code's hook or
  inputs, never in a copy of it. Shared code that lacks a needed seam gets the seam.
- Every deploy and day-2 operation is safe to run again: a second run keeps configuration,
  credentials and data, and creates no duplicate resources.
- Fix defects in the repository. A manual change on the lab is for diagnosis or for
  clearing a failed run's leftovers, never the fix.
- Never relax a safety guard to clear a failure (Vault-mode guard, ownership and tag
  checks, restore guards, the `update_unsafe` scope in `ansible/tasks/proxmox/vm-clone.yml`).
- Do not add issue trackers, specifications, lesson logs, evidence records or work
  inventories to this repository. Explain behavior in the nearest README or a short code
  comment.

## Where things are

| Need | Read |
| --- | --- |
| Operate the platform | [`README.md`](README.md) |
| Ansible layout, execution and config loading | [`ansible/README.md`](ansible/README.md), [`ansible/vars/CONTRACT.md`](ansible/vars/CONTRACT.md) |
| Rundeck jobs, the runner, `rd` and live runs | [`rundeck/README.md`](rundeck/README.md) |
| Gate commands | [`gate/README.md`](gate/README.md) |
| Add an app | [`ansible/playbooks/apps/README.md`](ansible/playbooks/apps/README.md) |
| Shared task files and playbooks | [`ansible/tasks/README.md`](ansible/tasks/README.md), [`ansible/playbooks/README.md`](ansible/playbooks/README.md) |
| Application catalog | [`catalog/README.md`](catalog/README.md) |
| User configuration examples | [`config.example/README.md`](config.example/README.md) |
| Placeholders for public text | [`docs/lab-placeholders.md`](docs/lab-placeholders.md) |

## Verify before committing

From the repository root run `bash gate/lint.sh` and `bash gate/test.sh`. Both must
pass. Put the commands and results in the PR body. CI runs the same checks on the PR.
On a Windows checkout through WSL, wrap each command in `wsl bash -lc '...'`.

## Keeping the repository small

An agent should find the one place a fact or behavior lives without reading half the tree.
`bash gate/lint.sh` checks rules 1-5 (`gate/check-structure.py`); current exceptions are
recorded in `gate/structure.yml`, may not grow, and are deleted once they meet the rule.

1. **One home per fact.** Other text links to it instead of restating it. Two docs share
   no run of 12 words.
2. **Size budgets, in lines:** playbook, task file, template and Markdown 300; this file 80;
   Python and shell 600. Over budget, split by phase or move per-app behavior into the
   role. Raising a recorded ceiling needs a reason in the PR body.
3. **A README is a map.** Each directory outside `ansible/roles/` has a README, or the
   nearest README above it names it (`name/`). It says what lives there and where to go
   next in a few lines; the code and its header comment explain behavior.
4. **Every README is reachable** by Markdown links from this file.
5. **Predictable paths.** Catalog app `<app>` has `playbooks/apps/<app>.yml`,
   `vars/app-defaults/<app>.yml`, `config.example/apps/<app>.example.yml` and
   `roles/<app>/`, or a `role_aliases` entry. A shared playbook reaches per-app behavior
   through the role's hook file (`roles/<app>/tasks/<operation>.yml`) and never compares an
   app name to a literal.

## Live lab

Live runs may overlap and use the runner checkout as it stands; see
[Live operations](ansible/README.md#live-operations). Test an unmerged branch by pointing
the runner at that branch (see `rundeck/README.md`).
