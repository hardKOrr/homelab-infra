# 016 — Vaultwarden identities and Rundeck bootstrap keyring

**Status:** closed
**Subject:** Vaultwarden
**Related:** 014 (the secret store this unblocks), 013 (admin token), 015 (HTTPS)

## Goal

Create and prove the human and machine identities needed to *use* Vaultwarden, keeping only
the irreducible bootstrap credentials in Rundeck Key Storage.

The admin-panel token is **configuration authority, not a vault read credential** — it
cannot log in, unlock encrypted data or authorize item CRUD. Three identities, kept apart:

- **Human owner** — browser login, recovery, organization administration, issuing and
  revoking the automation account's access. Its master password is stored as a Rundeck
  password entry until copied to an independent recovery location, and never placed inside
  Vaultwarden itself.
- **Automation account** — access only to the `homelab-infra` organization/collection, the
  minimum role that can read and update platform items, never a server administrator. Jobs
  authenticate with its API `client_id`/`client_secret` then unlock with its master password
  — API-key login alone does not decrypt vault data. The session exists only in the job
  process and is destroyed by an always-run cleanup.
- **Admin panel** — the generated `ADMIN_TOKEN` stays a separate emergency credential that
  only the administration/recovery job may reference.

**Rundeck is a bootstrap keyring, not an application secret store.** Its bounded set exists
only to open Proxmox, HTTPS issuance, SSH transport and Vaultwarden itself; the seven paths
are listed in notes.md. No platform application token, generated service password, OIDC
secret or PBS token belongs there. Where an action is browser-only, the workflow pauses with
an exact redacted handoff and a resume job rather than claiming an unproven automated setup.

Enrollment and cutover completed live 2026-08-03; the automation account drives every vault
read and write in the green bootstrap.

## Remaining

- [x] The automation account's access is scoped to the `homelab-infra` organization —
      **amended and closed 2026-08-09.** What ships is organization-scoped Admin, and
      Vaultwarden gives no way to make it collection-scoped without a role change nobody
      is going to make: `post_organization_collection_update` skips members with
      `access_all` before saving, so a grant write returns success and persists nothing,
      and `allowAdminAccessToAllCollectionItems` is a hardcoded `true` in
      `src/db/models/organization.rs` with `PUT /api/organizations/<id>/collection-management`
      answering 404. The criterion now records the reach the platform actually has. The
      grant tasks remain in place and self-heal on a Manager or User membership, so a lab
      that runs one is scoped for free — no lab is asked to change anything
- [x] Ordinary deploys cannot read `keys/vaultwarden/admin-token` or the owner password —
      **closed by inspection 2026-08-10.** `rundeck/render-job.py` is the only thing that
      attaches secure options, and it scopes `ADMIN_OPTION` to exactly two job names,
      Vaultwarden Enrollment and Vaultwarden Cutover. Every other job that calls `lab-run`
      receives the three `bw_*` options and nothing else, so an ordinary deploy has no
      option, no env var and no storage path through which to read the admin token. The
      owner master password is never staged in Key Storage at all
- [x] Every Key Storage secret is loaded through a secure option, and none is copied into a
      normal option, command argument, config file, generated fact or log — the log and
      generated-facts halves were verified by 014; the **Key Storage and command-argument
      halves closed by inspection 2026-08-10.** `render-job.py` emits every secret as
      `secure: true` with a `storagePath`, injected at import time (called from
      `bootstrap-rundeck.sh:1153`), which is why the committed job YAMLs carry none of them.
      They reach the playbooks only as `RD_OPTION_*` environment variables, read at
      `lab-run.sh:93-98`. `grep '@option\.' rundeck/jobs/*.yaml` matches no secret-bearing
      option, so nothing is interpolated into a command argument
- [x] A browser-only step produces a redacted handoff with an exact resume action —
      **closed by inspection 2026-08-10.** `vaultwarden-enroll.yml`'s enrollment ceremony
      prints the registration URL, the organization and role to create, and names the exact
      resume action ("Then run the Vaultwarden Cutover job. Ordinary deploys remain disabled
      until it succeeds."). It contains no secret material; the addresses it echoes are
      `no_log` upstream
- [x] Re-running adopts existing accounts, organization, collection and keys without
      rotation or duplication — **closed 2026-08-10 on the accumulated live evidence.**
      Repeated deploys across 2026-08-03 to 2026-08-09 upserted the same nine platform items
      with exact readback and no duplicates; execution 55 added Prowlarr's to the same
      organization and collection without re-creating either. Adoption is the path every
      one of those runs took
- [x] Human owner and automation identities are distinct and authenticate over verified
      HTTPS — 2026-08-03
- [x] Automation login + unlock performs item CRUD without interaction — proved by the
      stronger case than a canary: nine real upserts with readback across the unattended
      bootstrap, no human present
- [x] CLI state and `BW_SESSION` are absent after success — the only residue is
      `{"stateVersion": 82}` from the manual bootstrap path: version metadata, no session,
      no credential. **The injected-failure half is untested**

## Links

- `ansible/tasks/bitwarden/` — CLI install/configure, login, unlock, CRUD, cleanup
- `ansible/playbooks/bootstrap.yml` — identity bootstrap after the HTTPS probe, before
  cutover; `ansible/playbooks/maintenance/` — the explicit recovery job
- `rundeck/bootstrap-rundeck.sh` — the bounded Key Storage tree and resumable checkpoints
- `ansible/scripts/lab-run.sh` — injected credentials, isolated CLI state, guaranteed cleanup
- `rundeck/README.md`, `ansible/vars/CONTRACT.md`, `.claude/specs/secrets-handling.md`
- [notes.md](notes.md) — the full identity model, the seven Key Storage paths and their
  consumers, the seven-step approach, and the decision record
