# 014 — Make Vaultwarden the mandatory secret store

**Status:** built
**Subject:** Vaultwarden
**Related:** 016 (identities + bootstrap keyring), 015 (HTTPS), 013 (admin token), 400 (app)

## Goal

Move every application secret out of temporary files and generated facts into Vaultwarden,
delete the file copies, and make the vault the only source and sink for application
credentials from that point forward.

Exactly two operating modes:

1. **Bootstrap mode** — a fresh or explicitly recovered runner may use only the bounded
   Rundeck roots defined by 016, long enough to establish HTTPS, Vaultwarden and vault CRUD.
2. **Vault mode** — after the cutover marker is written, every deploy reads its required
   secrets from Vaultwarden and writes every new or changed secret back.

Vault mode is **fail-closed: no reachable and unlockable Vaultwarden means no deploy.**
Offline deploys and a secret-bearing `facts.yml` cache are deliberately unsupported; only
the explicit recovery path may cross that boundary. `facts.yml` keeps topology and registry
data only — never a value whose disclosure grants access.

The security boundary is the reason 016 exists: a secret store cannot provide the
credentials used to build, route, administer and unlock itself. That bounded keyring may
open the vault and its prerequisites; it may not carry service credentials, application
tokens, OIDC secrets or generated passwords.

Cutover completed 2026-08-03; the first full vault-mode bootstrap ran green the same day
(execution 12, seven services, `failed=0`).

## Remaining

- [ ] Rebuilding the runner from non-secret config plus a backup of the bounded Key Storage
      roots restores access to all platform secrets without redeploying or rotating a
      service — **not tested**, and the only remaining item needing real setup rather than
      an injection. Preferred shape: clone the runner to a scratch VMID and rebuild the
      copy, so a failure leaves the working runner untouched
- [ ] Interrupting bootstrap before cutover is resumable and does not delete the only copy
      of a seed secret — **not tested.** Resumability is well evidenced for vault mode
      (executions 10–12 each resumed cleanly mid-run); the pre-cutover case is the gap
- [x] A fresh runner uses only bounded Key Storage roots to establish HTTPS, deploy
      Vaultwarden and initialize identities — met with a caveat: the runner was not rebuilt
      from bare metal, so "fresh" is inferred from the seed → cutover → vault-mode sequence
- [x] Bootstrap imports every seed secret, verifies by readback, records cutover, removes
      all seed files before deploying the next service
- [x] After cutover, stopping Vaultwarden fails every deploy in preflight before any change
      — 2026-08-03. It holds more strongly than the criterion asks: Ansible never started
- [x] Recreating a seed file does not make an ordinary deploy bypass Vaultwarden —
      2026-08-06, tested in both directions (healthy vault ignored it; stopped vault still
      ignored it and refused the deploy)
- [x] The explicit recovery workflow is the only path that can re-enter seed mode —
      2026-08-06, in three parts. Two defects had to be fixed first; see notes.md
- [x] `facts.yml` contains no credential value — verified field by field
- [x] Every generated secret is in its canonical vault item before the producing job can
      succeed — nine org-owned items, the write ordered before the success notification
- [x] No secret or vault session appears in a job log, artifact, diff, cache or guest
      metadata — all 4,909 lines of the execution-12 log scanned
- [x] Documentation describes Vaultwarden as a mandatory runtime dependency

**Fail-closed is an observed property**, not a claim. Two of the three originally-remaining
injections were run and passed on 2026-08-06.

Adjacent finding, not this slice: `Deploy Ntfy` reports `changed=4` on a converged re-run —
three `changed_when` omissions, cosmetic rather than drift.

## Links

- `ansible/tasks/bitwarden/` — authenticate/unlock, required-item read, `upsert-item.yml`
  (compares before writing; takes `vault_item_merge`), readback verification, lock/cleanup
- `ansible/scripts/lab-run.sh` — seed vs vault mode, the cutover marker, `_vault_preflight`
- `ansible/tasks/load-user-vars.yml`, `ansible/tasks/bootstrap/write-generated-facts.yml`
- `ansible/playbooks/bootstrap.yml`, `ansible/playbooks/maintenance/` (recovery path)
- `ansible/vars/CONTRACT.md` — §5 runtime secrets, canonical vault items and fields
- [notes.md](notes.md) — the full problem analysis, the four-part approach, the break-glass
  and diagnostics defects found by injection, decisions and open questions
- [plan.md](plan.md), [migration-manifest.md](migration-manifest.md)
