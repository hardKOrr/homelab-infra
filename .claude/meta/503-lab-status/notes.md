# 503 — notes

## 2026-07-25 — implementation

`playbooks/maintenance/status.yml` implemented as three plays (refresh inventory →
gather per-guest state → query platform services and render). Both gates green, and the
render path was exercised end-to-end locally: with no Proxmox, no Kuma and no PBS it
prints every section with its "unavailable/none" line and exits 0.

### Deviations from the README approach

**Four sources, three plays.** The README's Play 1 (Proxmox API via `proxmox_vm_info` /
`proxmox_lxc_info`) is redundant: the dynamic inventory already carries guest state as
`proxmox_*` hostvars after `refresh_inventory`. Querying the API a second time would add
a credential path and a failure mode for data already in hand. Every hostvar read is
`| default('?')` so a plugin field rename degrades a column, never the run.

**Docker state comes from the CLI, not `docker_host_info`.** `community.docker`'s info
modules need the Python docker SDK on each guest; the stack hosts only have the Compose
CLI plugin (which is all `docker_compose_v2` needs). `docker ps --all --format` with the
Go template braces wrapped in `{% raw %}` is the dependency-free read.

**Added a "native app updates" section the README left implicit.** CLAUDE.md promises
status reports "what's behind on updates", so the play calls `lab-update-check` — the
same script `check-native-updates.yml` uses. It displays only; it never notifies. This
makes the play slower (a GitHub call per native app) but keeps the promise.

**Rendered as one debug of a pre-split line list.** Semaphore and Rundeck both render a
`msg` list one line per entry; a single string with embedded newlines shows as one
escaped blob. Note `splitlines()`, not `split("\n")` — the latter does not survive YAML
quoting and silently returns a one-element list (observed, then fixed).

## 2026-08-08 — second live run, against a populated lab

Rundeck execution 28, the first run with guests actually tagged. It confirmed the
riskiest assumption and exposed one defect.

**Confirmed.** The `proxmox_status` / `proxmox_vmid` / `proxmox_node` hostvar names are
right — all eight tagged guests rendered with state, vmid, node and IP. The PBS snapshot
shape is right: six `backup-id` rows with `backup-time` formatted. The run changed
nothing and exited green, and the two absent providers degraded rather than failing.

**Defect: the guest play had no connection user, so no guest was ever reachable.** Every
tagged guest returned `Permission denied (publickey,password)` for `rundeck@…`. The
Proxmox dynamic inventory supplies no `ansible_user`, and unlike the deploy playbooks —
which `add_host` each guest with `homelabinfra_config.ansible.ssh_user` — this play
inherits the account running Ansible, which on a runner is `rundeck`. The platform SSH
key is correctly installed for `root` on every guest; nothing was ever going to accept
`rundeck`. Fixed in `8d31ba4` by setting `ansible_user` from the loaded config on the
guest play.

That is why CONTAINERS said "no Docker host reported a container" while two stack hosts
were running containers, and why NATIVE APP UPDATES claimed everything was current: both
sections are fed by facts that only a reachable guest can set, and an empty result is
indistinguishable from a healthy one in the rendered report. `check-native-updates.yml`
had the identical defect and is fixed the same way — meaning that playbook has never
checked an update on a runner either.

**Worth noting for the report's design:** the guest table does mark unreachable rows, so
the evidence was on screen; the two sections below it silently render their empty-state
line instead. A section whose input hosts were unreachable should say so rather than
report an empty set.

### What live acceptance must confirm

- The `proxmox_status` / `proxmox_vmid` / `proxmox_node` hostvar names against a real
  `community.proxmox` inventory — the highest-risk assumption in this slice, and a
  cosmetic failure if wrong.
- The Uptime Kuma monitor shape (`.name`, `.active`) and the PBS snapshots response
  (`data[].backup-id` / `.backup-time`).
- That an unreachable guest degrades to a marked row rather than failing the play.
