# 016 — notes

Identity model, Key Storage boundary, approach and decisions, moved out of README.md during
the meta restructure (2026-08-08). Kept verbatim; the README now carries goal, remaining
acceptance and links.

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
- **Explicit collection grant, organization Admin retained** — decided 2026-08-09, the
  decision that had blocked the slice. The automation account now holds a `users_collections`
  grant with manage rights on `platform-secrets`, so its access no longer rests on
  `allowAdminAccessToAllCollectionItems`, and the human owner turns that org-wide flag off as
  the last tightening step.

  The two alternatives were rejected for reasons worth keeping. **Demoting the account to a
  collection-scoped user** is stricter but breaks the platform: `upsert-item.yml` creates the
  canonical collection at runtime, which a plain user cannot do, and that path is what keeps
  enrollment from depending on an instruction printed in job output. **Amending the criterion**
  to record org-scoped Admin would have been honest about what shipped but leaves the account's
  reach defined by a flag nobody would think to check.

  **Correction, same day: there is no flag to revoke on Vaultwarden.** The decision above
  stands, but the tightening step it implied does not exist. Vaultwarden serializes
  `"allowAdminAccessToAllCollectionItems": true` as a hardcoded literal in
  `src/db/models/organization.rs` — both places the organization is emitted, unchanged
  through 1.37.1 and `main` — and ships no endpoint behind it:
  `PUT /api/organizations/<id>/collection-management` answers 404 on the live server while
  `PUT /api/organizations/<id>` answers 401, so the route table is real and that one is
  simply absent. The web vault hides the Collection management page for that reason.

  What remains achievable is lowering the **role** rather than the flag, and the floor is
  **Manager**, not User. Every vault write already calls org-scoped endpoints that take
  `ManagerHeadersLoose` — `GET /organizations/<id>/collections` for the collection list and
  `GET /organizations/<id>/users` for the member list — which errors with "You need to be a
  Manager, Admin or Owner to call this endpoint". A User is refused at the first write, not
  merely at collection creation. Manager passes it, and the strict `ManagerHeaders` guarding
  the collection detail and update calls `is_coll_manageable_by_user`, which is precisely
  what the explicit grant satisfies. So Manager plus the grant is least privilege that still
  functions, and it is the only arrangement here where the grant is load-bearing.

  It stays lab-local rather than shipped: creating the canonical collection needs Admin, so
  a fresh bootstrap cannot start from Manager. Revert is setting the role back to Admin.

  The general lesson is cheap to state and was not: **a permission model borrowed from
  upstream Bitwarden is not evidence about Vaultwarden.** Both halves of this were checkable
  in one request each — a route probe against the live server, and a grep of the pinned
  version's source.

## Where the grant is enforced

In `tasks/bitwarden/upsert-item.yml`, beside the collection resolution, not in a one-shot
enrollment step — so it is self-healing on any run rather than a state a re-enrollment could
lose. A guard fact makes it once per play, so a bootstrap's nine upserts cost one check.

Three traps the tasks are written around:

- **`bw list org-collections` carries no `users` array; `bw get org-collection` does.** The
  edit rewrites the collection object wholesale, so reading the list form and sending it back
  would erase every existing grant. A payload without `users` fails the run instead.
- **The collection this platform creates already grants confirmed members.** These tasks are
  the repair path for a collection that predates the decision — including this lab's, whose
  `users_collections` was empty.
- **A stale read-only row is not absence.** The account may already appear with
  `manage: false`; the payload replaces its row rather than appending a second one.

`.claude/gate/test-vaultwarden.sh` lifts the payload builder out of the task file and runs it
against a fake `bw`, so the test cannot drift from the shipped code: another member's row must
come back byte-identical and the account's must end at manage.
