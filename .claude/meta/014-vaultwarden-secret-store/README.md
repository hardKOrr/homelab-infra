# 014 — Make Vaultwarden the mandatory secret store

**Status:** open
**Depends on:** 015 (Caddy-first wildcard HTTPS), 016 (Vaultwarden identities and Rundeck
bootstrap keyring), 200 (write-generated-facts)
**Blocks:** the durability half of 010; every deploy after the bootstrap cutover

## Goal

After 015 and 016 establish HTTPS and the bounded bootstrap keyring, move every application
secret out of temporary files and generated facts into Vaultwarden. Delete those file copies
and make Vaultwarden the only source and sink for application credentials from that point
forward.

There are exactly two operating modes:

1. **Bootstrap mode:** a fresh or explicitly recovered runner may use only the bounded
   Rundeck roots defined by 016 long enough to establish HTTPS, Vaultwarden, and vault CRUD.
2. **Vault mode:** after the cutover marker is written, every deploy reads its required
   secrets from Vaultwarden and writes every new or changed secret back to Vaultwarden.

Vault mode is fail-closed: **no reachable and unlockable Vaultwarden means no deploy.**
Offline deploys and a secret-bearing `facts.yml` cache are deliberately not supported.
Only the explicit bootstrap/recovery path may cross that boundary.

## Problem

The current implementation contradicts that model:

- `/etc/homelab-infra/secrets.env` supplies the Proxmox token to every job indefinitely.
- `/etc/homelab-infra/secrets.d/vaultwarden.env` permanently holds the generated
  Vaultwarden admin token.
- Generated service credentials live in `config/.generated/facts.yml`; Vaultwarden is not
  involved in their reads or writes.
- The two files under `ansible/tasks/bitwarden/todo/` are documentation links, not an
  implementation.
- Jobs tolerate an absent facts file and can continue without proving that the secret store
  is available.

This creates several sources of truth and makes a Vaultwarden outage invisible until some
later operation happens to need a missing credential. It also leaves irreplaceable values,
such as the PBS API token secret, on one runner filesystem.

## Security boundary

A secret store cannot provide the credentials used to build, route, administer, and unlock
itself. Slice 016 therefore defines a small fixed external root set in Rundeck Key Storage:
Proxmox, Caddy DNS-01, Rundeck SSH transport, Vaultwarden administration, and Vaultwarden
owner/automation unlock credentials. Values enter job memory only for the operation that
needs them, through scoped secure options.

That bounded keyring is not a second application-secret store. It may open the vault and
its prerequisites; it may not carry service credentials, application tokens, OIDC secrets,
or generated passwords. The Vaultwarden admin token remains distinct from the Bitwarden
client credentials used for item access.

## Approach

### 1. Separate bootstrap from normal operation

Give the runner an explicit, durable cutover state rather than inferring it from whichever
files happen to exist.

- Before cutover, only the Vaultwarden bootstrap/recovery workflow may load the bounded
  Rundeck bootstrap keys.
- Every ordinary job refuses to run before cutover.
- After cutover, `lab-run` refuses to source the seed files even if somebody recreates
  them accidentally.
- Re-entering seed mode is an explicit recovery action, not an automatic fallback.

The fresh-lab order is:

1. create the runner and its bounded Key Storage bootstrap roots;
2. deploy Caddy and verify wildcard HTTPS (015);
3. deploy and wire Vaultwarden through that verified origin;
4. configure and verify the human owner and automation account (016);
5. import application secrets already present at bootstrap; the Proxmox, Caddy DNS,
   Vaultwarden admin, Vaultwarden automation, and Rundeck SSH roots remain in bounded Key
   Storage because they are needed to open the systems that contain later secrets;
6. read every imported value back and compare it without logging it;
7. write the cutover marker;
8. remove the temporary seed files created by the earlier implementation;
9. continue the platform bootstrap in Vault mode.

The transition must be resumable. A failure before verified cutover leaves seed mode intact;
a failure after the marker never silently falls back to files.

### 2. Make Vaultwarden the runtime read path

At the front door of every deploy, unlock Vaultwarden and resolve all secrets required by
that play into an in-memory runtime structure. Secret values must not be written to
`config/`, Ansible fact-cache files, job option values, artifacts, or managed-guest metadata.

`config/.generated/facts.yml` may remain only for non-sensitive discovery data such as
hosts, ports, VMIDs, instance names, and provider selection. Every token, password, private
key, API key, API secret, and credential field moves out of it. Existing consumers must be
changed to receive those fields from the runtime vault data instead.

Vault access is a mandatory preflight, not a lazy lookup halfway through a role. Failure to
reach, authenticate to, unlock, or read the required items stops the job before any
infrastructure mutation.

### 3. Make Vaultwarden the generated-secret write path

Each role that creates or rotates a secret writes it directly to the canonical Vaultwarden
item and verifies readback with `no_log: true`.

- Generate-before-apply flows store the value before applying it to the service.
- Create-once APIs, such as PBS token creation, store the returned value immediately in the
  same guarded block.
- A job may not report success until the vault contains the value it installed.
- A failed vault write is a failed deploy; the error reports the item identity and phase,
  never the value.

There is one item per registry role key
(`homelab-infra/sso`, `/notifications`, `/monitoring`, `/metrics`, `/backups`, `/dns`,
and `/vaultwarden`) and one per application instance for `media` entries. Field names
mirror the runtime contract so reads and writes do not require a second mapping.

### 4. Remove the old secret stores

After verified cutover:

- delete `/etc/homelab-infra/secrets.env`; Key Storage remains the source for the bounded
  bootstrap roots named by 016;
- delete `/etc/homelab-infra/secrets.d/vaultwarden.env` and the directory when empty;
- remove secret fields from `config/.generated/facts.yml`;
- remove file/env fallback for Proxmox and generated service secrets from normal jobs;
- reject newly written secret-shaped keys in `facts.yml`;
- update Get Config, backup, and diagnostic paths so none treats the deleted files as a
  recovery source.

A separately invoked recovery procedure may recreate seed material long enough to restore
Vaultwarden. It is not part of an ordinary deploy and must never activate automatically
when the vault is down.

## Files

- `ansible/tasks/bitwarden/` — replace `todo/` with vault authenticate/unlock, required-item
  read, item upsert, readback verification, and lock/cleanup tasks.
- `ansible/scripts/lab-run.sh` — distinguish seed and Vault modes; enforce the cutover
  marker and mandatory vault preflight.
- `ansible/tasks/load-user-vars.yml` — remove normal-operation file/env fallback for
  application secrets and merge the in-memory vault values into the runtime contract.
- `ansible/tasks/bootstrap/write-generated-facts.yml` — write non-secret registry facts
  only; reject secret-shaped fields.
- `ansible/playbooks/bootstrap.yml` — require 015 and 016, import and verify application
  secrets, cut over, remove seed files, then continue.
- `ansible/playbooks/maintenance/` — add an explicit Vaultwarden recovery/bootstrap path;
  do not add a job that restores secrets to `facts.yml`.
- `rundeck/bootstrap-rundeck.sh` — remove its secret-file runtime path and use the bounded
  Key Storage tree defined by 016.
- `rundeck/jobs/*.yaml`, `semaphore/project.json` — provide the machine credential to the
  job process and make the vault preflight common to every deploy.
- roles and wiring tasks that currently read credential fields from
  `config/.generated/facts.yml` — consume the in-memory vault contract instead.
- `ansible/vars/CONTRACT.md` — split non-secret generated facts from the secret runtime
  contract and name every canonical vault item and field.
- `.claude/CLAUDE.md`, `.claude/specs/secrets-handling.md`, runner documentation — state
  the seed/cutover model and the hard deployment dependency accurately.

## Acceptance

- [ ] A fresh runner uses only bounded Key Storage roots to establish HTTPS, deploy
      Vaultwarden, and initialize its identities
- [ ] Bootstrap imports every application seed secret, verifies each by readback, records
      cutover, and removes all seed files before deploying the next service
- [ ] Interrupting bootstrap before cutover is resumable and does not delete the only copy
      of a seed secret
- [ ] After cutover, stopping Vaultwarden causes every deploy to fail in preflight before
      making any infrastructure or service change
- [ ] After cutover, recreating a seed file does not make an ordinary deploy bypass
      Vaultwarden
- [ ] The explicit recovery workflow is the only path that can re-enter seed mode
- [ ] `config/.generated/facts.yml` contains no token, password, private key, API key, API
      secret, or other credential value
- [ ] Every generated or rotated secret is present in its canonical Vaultwarden item before
      the producing job can succeed
- [ ] Rebuilding the runner from non-secret config plus a backup of the bounded Key Storage
      roots restores access to all platform secrets without redeploying or rotating a service
- [ ] No secret or vault session appears in a job log, artifact, diff, Ansible cache, or
      managed-guest metadata
- [ ] Documentation describes Vaultwarden as a mandatory runtime dependency, not a
      durability mirror or optional restore source

## Decided

- **No Vaultwarden means no deploy.** There is no offline or cache-backed mode after
  cutover.
- **Seed files are temporary migration state.** They are removed after bounded roots are in
  Key Storage and application secrets are verified in Vaultwarden.
- **Bootstrap roots stay in Rundeck.** Proxmox access, Caddy DNS-01, Rundeck SSH transport,
  Vaultwarden administration, and the credentials that unlock the automation vault cannot
  depend on the vault they are required to open. Slice 016 defines and limits that set.
- **`facts.yml` is non-secret only.** It can cache topology and registry data, but never a
  value whose disclosure grants access.
- **Secrets move through memory.** Normal jobs read them from and write them to Vaultwarden;
  they do not restore a secret-bearing local cache.
- **Recovery is explicit.** Vaultwarden bootstrap/recovery may use seed material; ordinary
  deploys may not.

## Open questions

- **Semaphore parity.** Define the equivalent bounded environment/secret entries and secure
  injection path without expanding the Rundeck Key Storage set.
- **Cutover marker location.** It must survive repository refreshes, contain no secret, and
  be available before Ansible starts. Prefer runner state under `/etc/homelab-infra/`.
- **Break-glass recovery.** Define how an operator supplies temporary seed material and
  deliberately re-enters recovery mode without weakening the normal-job guard.
- **Write failure compensation.** For APIs that reveal a secret only after mutation, define
  whether a failed vault write triggers immediate token revocation or leaves the job failed
  with a named manual recovery action.
- **Rotation.** Item storage makes rotation possible but does not schedule it; automated
  rotation remains out of scope for this slice.
