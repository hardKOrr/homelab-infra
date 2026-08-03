# 500 — Bootstrap playbook plays

**Status:** built — all seven steps implemented and gate-verified; 401–406 landed 2026-07-25 and their imports are now active. Only `apps/nginx.yml` remains staged (no nginx app playbook exists yet — slice 301 shipped only the nginx wiring pair). Fact-writing moved out of this playbook into each app's Play 3. **Ran green in vault mode 2026-08-03** (Rundeck execution 12, revision `bb84574`) — all seven services, `failed=0` on every host, PBS included for the first time. Three of four acceptance items observed; full convergence still needs a clean re-run (see notes.md).
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

- [x] Running `bootstrap.yml` on a fresh Proxmox host with completed config files brings up
      all baseline services — observed 2026-08-03. Seven hosts, `failed=0`:
      caddy ok=47, vaultwarden ok=45, ntfy ok=79, sso-stack ok=67, monitoring-stack ok=132,
      pbs ok=72, localhost ok=470. Guests 168000010–168000015 plus the runner, each tagged
      with its own stack and nothing else
- [x] The two-pass behavior is clearly messaged — **superseded, and recorded as met on that
      basis.** The Vaultwarden two-pass this criterion describes was replaced by 014's
      seed → cutover → vault-mode model; execution 12 was a single unattended pass with no
      token paste. The criterion no longer describes the shipped design
- [ ] Subsequent re-runs are idempotent — no destructive operations — **not yet.** Execution
      12 still shows `changed` on every host (caddy 0, sso-stack 1, monitoring-stack 2,
      ntfy 4, vaultwarden 5, pbs 23), and pbs is high because it was created in that run.
      Convergence to `changed=0` needs one more clean re-run over the now-complete estate
- [x] Bootstrap can be re-run safely after any failure point — strongly evidenced:
      executions 10, 11 and 12 each resumed after a mid-run failure at a different step
      (second stack host, PBS template, then green) with no manual cleanup between them
