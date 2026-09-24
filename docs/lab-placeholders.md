# Lab placeholders

This repository, its issues, its pull requests, and its review threads are public. Anything
that identifies the operator's lab — addresses, domains, node names, VMIDs, workstation
paths — is written as a placeholder from the table below. Anyone reading an issue can
substitute their own lab's values, and the operator's values never reach GitHub.

Each placeholder is named after the configuration key that holds its value, so filling one
in is a lookup, not a guess.

## Vocabulary

`GUEST` is the guest's name as **Lab Status** lists it (`caddy`, `k3s-2`, `stack-sso`).
`ESTATE` is a key under `domains:` in `config/infrastructure.yml` (`personal`, `foxglove`).
`N` numbers several nodes in one text, starting at 1; use the bare form when one suffices.

| Placeholder | Meaning | Where the value lives |
| --- | --- | --- |
| `<lab-domain>` | The lab's default domain | `config/infrastructure.yml` → `domain`, or the `default: true` estate's `domain` |
| `<ESTATE-domain>` | One estate's domain, e.g. `<foxglove-domain>` | `config/infrastructure.yml` → `domains.ESTATE.domain` |
| `<pve-cluster>` | Proxmox cluster name | `pvesh get /cluster/status` |
| `<pve-node>`, `<pve-node-N>` | Proxmox node name | `config/proxmox.yml` → `proxmox.node`, or a key of `proxmox.nodes` |
| `<pve-node-ip>`, `<pve-node-N-ip>` | Proxmox node address | `config/proxmox.yml` → `proxmox.api_host`, or a value of `proxmox.nodes` |
| `<pve-storage>` | Guest storage pool | `config/proxmox.yml` → `proxmox.storage` |
| `<lab-cidr>` | A network's subnet | `config/proxmox.yml` → `networks.<name>.cidr` |
| `<lab-gateway>` | A network's gateway | `config/proxmox.yml` → `networks.<name>.gateway` |
| `<runner-ip>` | Rundeck runner address | `config/apps/rundeck.yml` → `proxmox.ip` |
| `<runner-vmid>` | Rundeck runner VMID | `config/apps/rundeck.yml` → `proxmox.vmid` |
| `<runner-url>` | Rundeck base URL | `RD_URL` in the operator's credential file |
| `<GUEST-ip>` | A guest's address, e.g. `<caddy-ip>` | **Lab Status**, or `config/.generated/facts.yml` |
| `<GUEST-vmid>` | A guest's VMID, e.g. `<k3s-2-vmid>` | **Lab Status** |
| `<backup-job-id>` | Proxmox backup job ID | `pvesh get /cluster/backup` |
| `<operator-home>` | Operator workstation home directory | the workstation |

A VMID derived from an address encodes that address
(`ansible/tasks/proxmox/ip-to-vmid-guest.yml`), so
a real VMID is as identifying as a real IP and takes a placeholder too.

Leave these as they are: platform-owned names that every install shares
(`homelab-infra@pve`, the `automation` token, the `HomelabInfra` role, the `_+lab` tag),
template VMIDs, Rundeck execution and job IDs, and the documentation address ranges
(`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and `example.*` domains used in
`config.example/` and fixtures.

A value the table does not cover gets a new row here before it appears in public text. Do
not invent a one-off placeholder.

## Where the real values live

The operator keeps the real values outside every repository, keyed by the placeholder names
above:

```text
~/.config/ai/homelab-infra/lab-values.yml
```

Agents read it to reach the lab, and never copy a value out of it into the repository, an
issue, a pull request, a review, or a commit message. Its `retired:` section keeps the
values of decommissioned labs so the leak check still catches them.

## Checking before publishing

`gate/check-lab-leaks.py` fails when a value from that file appears in tracked files. It
reports the placeholder name and location, never the value. Run it on any text before
posting it to GitHub:

```bash
python3 gate/check-lab-leaks.py --stdin < body.md
```

On a machine without the values file, such as CI, the check reports that it was skipped
and passes.
