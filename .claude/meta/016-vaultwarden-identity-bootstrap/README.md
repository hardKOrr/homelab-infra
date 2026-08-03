# 016 — Vaultwarden identities and Rundeck bootstrap keyring

**Status:** open — enrollment and cutover completed live 2026-08-03 and the automation
account drives every vault read and write in the green bootstrap. Three items are observed;
the collection-scoping item needs a decision rather than a test (see below).
**Depends on:** 013 (admin-token capture), 015 (Caddy-first wildcard HTTPS)
**Blocks:** 014 (Vaultwarden secret store)

## Problem

The repository has a Vaultwarden admin-panel token but no proven Bitwarden vault identity.
The admin token cannot log in, unlock encrypted vault data, or authorize item CRUD. There is
also no created human owner, limited automation account, organization/collection membership,
or durable and scoped way for jobs to receive the credentials that unlock the vault. Slice
014 cannot safely replace existing secret stores until those identities work end to end.

## Goal

Create and prove the human and machine identities needed to use Vaultwarden, while keeping
only the irreducible bootstrap credentials in Rundeck Key Storage. Produce exact, redacted
handoff output for any account action that Vaultwarden or the Bitwarden CLI cannot safely
automate.

The Vaultwarden admin-panel token is configuration authority; it is not a vault item-read
credential. Normal secret reads and writes use a dedicated Bitwarden vault account and the
official CLI authentication flow.

## Identity model

### Human owner

- One named owner account for browser login, recovery, organization administration, and
  issuing or revoking the automation account's access.
- Its email is non-secret configuration. Its master password is generated or supplied once
  and stored as a Rundeck password entry until copied to an independent recovery location.
- The bootstrap never places the human owner password inside Vaultwarden itself.

### Automation account

- One distinct account with access only to the `homelab-infra` organization/collection.
- It receives the minimum organization role that can read and update platform items; it is
  not a Vaultwarden server administrator.
- Jobs authenticate with its personal API `client_id` and `client_secret`, then unlock with
  its master password. API-key login alone does not decrypt vault data.
- The session key exists only in the job process, is masked from Ansible output, and is
  destroyed by an always-run cleanup step.

### Admin panel

- The existing generated `ADMIN_TOKEN` remains a separate emergency/configuration credential
  for `/admin`.
- Ordinary deploys do not receive it. Only the Vaultwarden administration/recovery job may
  reference its Key Storage path.

## Rundeck Key Storage boundary

Password entries are limited to bootstrap roots that cannot live in Vaultwarden yet:

| Path | Purpose | Normal consumer |
|---|---|---|
| `keys/proxmox/api-token` | create/reconcile managed guests | common deploy entry point |
| `keys/caddy/dns-api-token` | issue the bootstrap wildcard certificate | Caddy deploy only |
| `keys/vaultwarden/admin-token` | Vaultwarden server administration/recovery | admin job only |
| `keys/vaultwarden/owner/master-password` | initial human recovery handoff | bootstrap/recovery only |
| `keys/vaultwarden/automation/master-password` | decrypt automation vault | common vault preflight |
| `keys/vaultwarden/automation/client-id` | API-key login | common vault preflight |
| `keys/vaultwarden/automation/client-secret` | API-key login | common vault preflight |

The existing `keys/rundeck/homelab-ssh` private key remains Rundeck's SSH transport identity.
Emails, server URL, collection name, and Key Storage paths are non-secret config.

No platform application token, generated service password, OIDC secret, PBS token, or DNS
service credential other than Caddy's bootstrap DNS-01 token belongs in Rundeck.

## Approach

1. Verify Vaultwarden only through `https://vaultwarden.<domain>` with normal certificate
   validation.
2. Install and configure a pinned Bitwarden CLI on the runner and isolate its app-data
   directory per execution.
3. Create or invite the human owner and automation accounts, organization, collection, and
   membership through supported APIs/CLI operations where available.
4. When confirmation, API-key reveal, or a browser-only action is required, stop at a
   checkpoint and emit the exact URL, account, action, verification command, and resume job.
   Never emit a password, client secret, session, or admin token.
5. Store each resulting root credential directly in its Key Storage password entry.
6. Run `bw login --apikey`, unlock using a password environment variable, create a canary
   item, read it back, update it, and delete it without exposing values.
7. Lock/logout and remove execution-specific CLI state even when a step fails.

Rundeck jobs consume password entries through secure options with `storagePath`; the value is
exposed only to the wrapper environment. Job YAML may be generated from one shared definition,
but every imported job declares only the credentials it actually needs.

## Files

- `ansible/tasks/bitwarden/` — CLI install/configure, login, unlock, canary CRUD, and cleanup.
- `ansible/playbooks/bootstrap.yml` — run identity bootstrap after the external HTTPS probe and
  before Vault mode cutover.
- `ansible/playbooks/maintenance/` — explicit Vaultwarden identity bootstrap/recovery job.
- `rundeck/bootstrap-rundeck.sh` — create the bounded Key Storage tree and resumable account
  checkpoints; migrate the 013 file token into Key Storage after HTTPS is available.
- `rundeck/jobs/*.yaml` — Key-Storage-backed secure options scoped per job.
- `ansible/scripts/lab-run.sh` — accept injected credentials, isolate CLI state, and guarantee
  cleanup; stop treating `secrets.env`/`secrets.d` as durable stores.
- `rundeck/README.md`, `ansible/vars/CONTRACT.md`, `.claude/specs/secrets-handling.md` — document
  identity separation, key paths, recovery ownership, and rotation boundaries.

## Acceptance

- [x] Human owner and automation identities are distinct and authenticate over verified
      HTTPS — observed 2026-08-03; Vaultwarden is reached over a verified Let's Encrypt
      origin (see 015)
- [x] Automation login + unlock performs canary create/read/update/delete without
      interaction — proved by the stronger case rather than a canary: the automation
      account performed nine real item upserts with readback across the unattended
      bootstrap, no human present
- [ ] The automation account can access only the intended organization/collection —
      **needs a decision, not a test.** `users_collections` is empty; the account reads the
      organization purely by being an Admin with
      `allowAdminAccessToAllCollectionItems`. There is one organization and the account
      cannot reach outside it, so the *organization* half holds. The *collection* half does
      not: the account has org-wide access rather than a grant scoped to
      `platform-secrets`. Decide whether that satisfies the intent or whether the criterion
      requires a per-collection grant
- [ ] Ordinary deploys cannot read `keys/vaultwarden/admin-token` or the owner password —
      not tested
- [ ] Every Key Storage secret is loaded through a secure option; none is copied into a
      normal option, command argument, config file, generated fact, or log — the log and
      generated-facts halves are verified (see 014); the Key Storage and command-argument
      halves are not
- [x] CLI state and `BW_SESSION` are absent after success — no `BW_SESSION` in any live
      process environment, and the job user (`rundeck`) leaves no CLI state at all. The
      only residue is `/root/.config/Bitwarden CLI/data.json` from the manual bootstrap
      path, containing `{"stateVersion": 82}` — version metadata, no session, no
      credential. **The injected-failure half is untested**
- [ ] A browser-only step produces a redacted handoff with an exact resume action
- [ ] Re-running adopts existing accounts, organization, collection, and keys without rotation
      or duplication

## Decisions

- **Two accounts, two responsibilities.** The human owner recovers and administers; the
  automation account reads and writes only platform items.
- **API key plus master password.** Bitwarden CLI API-key authentication does not decrypt the
  vault, so both are irreducible automation roots until a supported machine-unlock mechanism
  removes that need.
- **Rundeck is a bootstrap keyring, not the application secret store.** Its small fixed set
  exists only to open Proxmox, HTTPS issuance, SSH transport, and Vaultwarden itself.
- **Unsupported account steps remain honest.** The workflow pauses with exact instructions
  and resumes after verification instead of claiming an unproven automated setup.
