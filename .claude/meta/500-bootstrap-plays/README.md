# 500 — Bootstrap playbook plays

**Status:** open
**Depends on:** 400, 401, 402, 403, 404, 405, 406
**Blocks:** the "one click to set up the platform" promise; 600 (Semaphore), 601 (Rundeck)

## Problem

`playbooks/bootstrap.yml` currently has only the config-load and assert play. Per CLAUDE.md it should run all seven baseline app deploys in order.

## Files

- `ansible/playbooks/bootstrap.yml` — add plays 2-8

## Approach

After the existing load+assert play, add seven `import_playbook` calls (or one-task plays that include each app's playbook). Each must pass the right `instance` name:

```yaml
- import_playbook: apps/vaultwarden.yml
  vars:
    instance: vaultwarden

- import_playbook: apps/ntfy.yml
  vars:
    instance: ntfy

# ... etc through pbs
```

Two open questions:

1. **Chicken-and-egg for Vaultwarden admin token.** First-deploy generates+prints; subsequent steps need it in `homelabinfra_infra` to write their own secrets. Two passes? Or block bootstrap with a clear "paste this token and re-run" message after step 1? CLAUDE.md says the latter. Implement a fail-soft: vaultwarden play prints the token, then if `homelabinfra_config.infrastructure.vaultwarden.admin_token` is empty, bootstrap halts with a clear message; user pastes, re-runs, second pass picks up at step 2.

2. **Reverse proxy choice.** `infrastructure.reverse_proxy.provider` selects caddy or nginx — bootstrap must conditionally import the right playbook. Use `when:` on the import.

## Acceptance

- [ ] Running `bootstrap.yml` on a fresh Proxmox host with completed config files brings up all baseline services (modulo the documented Vaultwarden two-pass)
- [ ] The two-pass behavior is clearly messaged (Ntfy notification + console)
- [ ] Subsequent re-runs are idempotent — no destructive operations
- [ ] Bootstrap can be re-run safely after any failure point (each slice idempotent on its own)
