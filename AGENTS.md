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
| Application catalog | [`catalog/README.md`](catalog/README.md) |
| User configuration examples | [`config.example/README.md`](config.example/README.md) |
| Placeholders for public text | [`docs/lab-placeholders.md`](docs/lab-placeholders.md) |

## Verify before committing

From the repository root run `bash gate/lint.sh` and `bash gate/test.sh`. Both must
pass. Put the commands and results in the PR body. CI runs the same checks on the PR.
On a Windows checkout through WSL, wrap each command in `wsl bash -lc '...'`.

## Live lab

Live runs are serial across the whole lab: every Rundeck job resets the one runner
checkout to `origin/$LAB_BRANCH`, so two concurrent runs corrupt each other. Test an
unmerged branch by pointing the runner at that branch (see `rundeck/README.md`), and never
change `LAB_BRANCH` while a job is running.

## GitHub coordination

GitHub owns work scope, decisions and progress. Follow [GitHub coordination](README.md#github-coordination)
for AO work. Publish significant milestones, blockers, review fixes and final validation
on the PR (the issue before a PR exists), preserving history in comments. Do not use
`ao report`, routine agent messages, acknowledgements or duplicate CI/review relays.
The watcher admits selected issues through assignment, handles approved merges and
completed workers, and resumes a worker only for an explicit operator command.
