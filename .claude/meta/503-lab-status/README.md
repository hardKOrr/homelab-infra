# 503 — Lab status playbook

**Status:** built — ran green on the live lab 2026-07-26 via Rundeck, but against **zero
tagged guests**, so every section rendered its empty-state line. The play chain is proven;
the report content is not. Decisions and deviations from the approach below are in notes.md.
**Depends on:** none (read-only across whatever exists)
**Blocks:** nothing critical

## Problem

`playbooks/maintenance/status.yml` is a debug stub. CLAUDE.md promises a read-only status report.

## Files

- `ansible/playbooks/maintenance/status.yml` — implement

## Approach

Three info-gathering plays, one print play. All read-only. No Ntfy notification.

**Play 1 — Proxmox guest states (on localhost):**
- Query the Proxmox API via `community.proxmox.proxmox_vm_info` and `proxmox_lxc_info` for all guests with tag `homelab-infra`
- Status, uptime, IP, hostname

**Play 2 — Docker container states (on `tag_*_stack` hosts):**
- `community.docker.docker_container_info` for all containers, gather state + image + created date

**Play 3 — Uptime Kuma + PBS (on localhost):**
- Query Uptime Kuma API for current monitor statuses
- Query PBS API for last backup per guest

**Play 4 — Render (on localhost):**
- `ansible.builtin.debug` with a multi-section formatted message OR `template` to a file in `/tmp/` for downloading from Semaphore output
- Sections: Guests, Stacks, Monitors, Backups, Native App Updates Pending (from last check-native-updates run, if any cached)

Best-effort throughout — if Kuma or PBS is down, that section reports "unreachable" and continues.

## Acceptance

- [ ] `ansible-playbook playbooks/maintenance/status.yml` produces a useful overview
      without modifying anything — ran clean (Rundeck execution 2, 2026-07-26) and changed
      nothing, but with 0 tagged guests every section was an empty-state line. Re-observe
      once something is deployed with the tag.
- [x] No notification fires — confirmed, no notify task in the run
- [x] Tolerates any one provider being unavailable — Uptime Kuma and PBS were both absent
      (no `config/.generated/facts.yml` at all); both sections reported unavailable and the
      run continued to a green recap
