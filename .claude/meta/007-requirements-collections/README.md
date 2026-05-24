# 007 — requirements.yml missing collections

**Status:** open
**Depends on:** none
**Blocks:** any docker app deploy, guest-bootstrap on a fresh control node

## Problem

`ansible/requirements.yml` declares only `community.proxmox` and `ansible.utils`, but the codebase already uses modules from `community.docker` and `community.general`. A fresh clone + `ansible-galaxy collection install -r requirements.yml` will not have what `guest-bootstrap.yml` or the docker role template needs.

## Files

- `ansible/requirements.yml` — add missing collections
- (reference) `ansible/tasks/guest-bootstrap.yml:48` — uses `community.general.timezone`
- (reference) `ansible/roles/_template-docker/tasks/main.yml:31,36` — uses `community.docker.docker_compose_v2_pull`, `community.docker.docker_compose_v2`
- (reference) CLAUDE.md mandates `community.general.bitwarden` lookup

## Approach

Add the two missing collections to `requirements.yml`. Pin to known-good major versions to keep deterministic.

```yaml
collections:
  - name: community.proxmox
  - name: ansible.utils
  - name: community.docker
  - name: community.general
```

## Acceptance

- [ ] `ansible-galaxy collection install -r ansible/requirements.yml` on an empty collection path installs all four
- [ ] No `community.X` reference in the codebase points to a collection not in requirements.yml
