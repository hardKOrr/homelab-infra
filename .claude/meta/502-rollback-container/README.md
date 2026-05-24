# 502 — Rollback container playbook

**Status:** open
**Depends on:** 201 (watchtower — to test the feedback loop)
**Blocks:** container update recovery story

## Problem

`playbooks/stacks/rollback-container.yml` is a TODO header only. Required for the Watchtower feedback loop in CLAUDE.md.

## Files

- `ansible/playbooks/stacks/rollback-container.yml` — implement

## Approach

Parameters: `stack` (required), `container` (required, = instance name), `image_tag` (optional — if omitted, pin to currently running tag = freeze).

Steps on the stack host (`tag_<stack>`):
1. Locate the compose file at `/opt/{{ container }}/docker-compose.yml`.
2. Read current image tag.
3. If `image_tag` is omitted, set it to the currently running tag (just pins to stop future updates).
4. Use `ansible.builtin.replace` or `lineinfile` to edit the `image:` line. Capture old and new for the notification.
5. `community.docker.docker_compose_v2_pull` — pull the rollback image.
6. `community.docker.docker_compose_v2` with `state: present` and `recreate: always` — restart with new tag.
7. Wait for container healthy (best effort — uri check the published port if known).
8. Ntfy notify: `"{{ container }} rolled back from {{ old_tag }} to {{ new_tag }}"`.

Note: pinning a non-latest tag disables Watchtower auto-update for that container until the deploy playbook is re-run (which restores `:latest` or the configured tag).

## Acceptance

- [ ] Rollback with explicit tag changes the compose file + restarts the container
- [ ] Rollback without tag freezes at current version (no compose-file change required if already pinned)
- [ ] Ntfy notification includes both old and new tags
- [ ] Idempotent on re-run with the same target tag
