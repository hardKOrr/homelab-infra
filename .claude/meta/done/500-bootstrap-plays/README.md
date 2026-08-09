# 500 — Bootstrap playbook plays

**Status:** done — all four acceptance items observed. The seven-service run went green in vault mode on 2026-08-03 (execution 12), and on **2026-08-08 a re-run converged to `changed=0` on every host** (execution 34), which was the last item. Only `apps/nginx.yml` remains staged, and it is staged because no nginx app playbook exists — slice 301 shipped the wiring pair only. Fact-writing lives in each app's Play 3, not here.
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
- [x] Subsequent re-runs are idempotent — no destructive operations — **observed
      2026-08-08, execution 34: `changed=0` on every one of the seven hosts.** It took
      six consecutive re-runs (executions 29–34) and eight fixes to get there, because
      almost nothing that reported `changed` was reporting it for the same reason:

      | Cause | Where |
      |---|---|
      | Unconditional vault writes | `tasks/bitwarden/upsert-item.yml` now compares first |
      | Declarative re-asserts hardcoded `changed_when: true` | `ntfy access`, `proxmox-backup-manager acl update` |
      | A probe counted as a convergence step | ntfy's authenticated-publish check |
      | `add_host` always reports changed | five guest-reuse calls |
      | `:latest` pulled with policy `always` | observability — every re-run was a silent unreviewed Prometheus/Grafana upgrade |
      | A module's changed flag that disagrees with identical compose output | observability's pull |

      Two of the eight were not cosmetic. The vault write **deleted a field that a later
      write in the same run restored** — unrecoverable for Uptime Kuma's API key, which
      only a human can mint. And Prometheus **could not receive a config change at all**:
      its config was bind-mounted as a file, Ansible replaces files by rename, so the
      container held the old inode and the reload handler kept reloading the config it
      already had. Full accounts in 405 and in the commits `8d31ba4`, `f9c758a`, `64ec867`.

      The lesson worth carrying: convergence is not a tidiness property. Chasing
      `changed=0` is what surfaced both of those, and neither was visible to the gates or
      to any single-app test.
- [x] Bootstrap can be re-run safely after any failure point — strongly evidenced:
      executions 10, 11 and 12 each resumed after a mid-run failure at a different step
      (second stack host, PBS template, then green) with no manual cleanup between them
